"""Helpers for the ``resume="delete"`` message flag.

A step whose metadata carries ``{"resume_policy": "delete"}`` does not
survive a thread resume of a dead session: it is stripped from the resumed
payload, deleted from the data layer together with its elements, and a
``delete_message`` is emitted to the client. A live pending ask holding the
step (any session of the thread, including the resuming one — a restored
session re-enters the resume branch on F5) protects it from deletion.
"""

import json
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

RESUME_POLICY_KEY = "resume_policy"
RESUME_POLICY_KEEP = "keep"
RESUME_POLICY_DELETE = "delete"


def step_metadata(step_dict: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the metadata of a step dict, always as a dict.

    Data layers disagree on the shape: SQLAlchemy passes the DB value
    through (a JSON string on SQLite), while others return a dict. Anything
    unreadable degrades to an empty dict — a malformed metadata must never
    break a resume.
    """
    metadata = step_dict.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (ValueError, TypeError):
            return {}
    return metadata if isinstance(metadata, dict) else {}


def is_resume_delete(step_dict: Mapping[str, Any]) -> bool:
    """Whether the step is flagged as not surviving a thread resume."""
    metadata = step_dict.get("metadata")
    if isinstance(metadata, str) and RESUME_POLICY_KEY not in metadata:
        # Cheap pre-check: thread reads on SQLAlchemy run this for every
        # step, and the JSON string cannot contain the flag without the
        # key's substring — skip the json.loads.
        return False
    return step_metadata(step_dict).get(RESUME_POLICY_KEY) == RESUME_POLICY_DELETE


def split_resume_delete(
    thread: Mapping[str, Any],
) -> Tuple[Mapping[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split a thread payload into a filtered copy and the doomed parts.

    Returns ``(new_thread, doomed_steps, doomed_elements)``. Doomed are the
    steps flagged ``resume="delete"`` that no live pending ask protects,
    plus — transitively — every step nested under one (a child left behind
    would keep a dangling ``parentId`` in the DB and render as a top-level
    message), plus the elements attached to any of them.

    Never mutates ``thread``: a custom data layer may hand out a live
    reference to its internal state, and in-place filtering would silently
    delete steps on a pure read. When nothing is doomed the original
    mapping is returned as-is.
    """
    steps = thread.get("steps") or []
    flagged = [step for step in steps if is_resume_delete(step)]
    if not flagged:
        return thread, [], []

    protected = protected_step_ids(thread.get("id"))
    doomed_ids = {
        step.get("id")
        for step in flagged
        if step.get("id") is not None and step.get("id") not in protected
    }
    if not doomed_ids:
        return thread, [], []

    # Transitive closure over parentId: descendants of a doomed step are
    # doomed too. A protected step is never doomed, whoever its parent is.
    changed = True
    while changed:
        changed = False
        for step in steps:
            step_id = step.get("id")
            if step_id is None or step_id in doomed_ids or step_id in protected:
                continue
            if step.get("parentId") in doomed_ids:
                doomed_ids.add(step_id)
                changed = True

    doomed_steps = [step for step in steps if step.get("id") in doomed_ids]
    elements = thread.get("elements") or []
    doomed_elements = [el for el in elements if el.get("forId") in doomed_ids]

    new_thread: Dict[str, Any] = {
        **thread,
        "steps": [step for step in steps if step.get("id") not in doomed_ids],
    }
    if "elements" in thread:
        new_thread["elements"] = [
            el for el in elements if el.get("forId") not in doomed_ids
        ]

    return new_thread, doomed_steps, doomed_elements


def protected_step_ids(thread_id: Optional[str]) -> Set[str]:
    """Step ids held by a live pending ask of any session on this thread.

    Deliberately includes the current (resuming) session:
    ``thread_id_to_resume`` is never cleared, so a live restored session
    re-enters the resume branch on F5 and must not delete the step of its
    own still-pending ask.
    """
    protected: Set[str] = set()
    if not thread_id:
        return protected

    from chainlit.session import ws_sessions_id

    for session in list(ws_sessions_id.values()):
        if getattr(session, "thread_id", None) != thread_id:
            continue
        pending_ask = getattr(session, "pending_ask", None)
        if pending_ask is None or not pending_ask.is_live:
            continue
        step_id = getattr(pending_ask.spec, "step_id", None)
        if step_id:
            protected.add(step_id)
        step_dict_id = (pending_ask.step_dict or {}).get("id")
        if step_dict_id:
            protected.add(step_dict_id)
    return protected
