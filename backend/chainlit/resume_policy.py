"""Helpers for the ``resume="delete"`` message flag.

A step whose metadata carries ``{"resume_policy": "delete"}`` does not
survive a thread resume of a dead session: it is stripped from the resumed
payload, deleted from the data layer together with its elements, and a
``delete_message`` is emitted to the client. Filtering and deletion run only
on the FIRST entry into the resume branch of a session
(``session.resume_processed`` gates it) — re-entries of the same session
(F5, transport reconnect) never delete. A live pending ask holding the step
(any session of the thread) is the second protection layer; it also guards
the read-only filtering that server endpoints apply on every thread read.
A thread with live activity — any session on it running a task — is not
dead at all: nothing is doomed then, whoever holds the steps (a second tab
resuming the same thread must not delete the live messages of the first).
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
        except ValueError, TypeError:
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

    # A running task on ANY session of this thread means the thread is
    # alive: its flagged messages are legitimately live (e.g. sent by that
    # task without an ask) and a resume from a second tab must not delete
    # them — the task's own later update() would resurrect the row as an
    # orphan and the feeds would diverge. Everything stays visible until a
    # resume finds the thread genuinely idle.
    if thread_has_live_task(thread.get("id")):
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


def thread_has_live_task(thread_id: Optional[str]) -> bool:
    """Whether any session on this thread is currently running a task.

    Checks both task slots: ``current_task`` and the on_thread_ready
    hook's ``thread_ready_task`` — a second-tab resume must not delete
    resume="delete" steps from under a running hook.

    Deliberately may include the resuming session itself — harmless: its
    slots are not set at resume time (the hook launches only after the
    cleanup decision is made), and its re-entries are gated by
    ``session.resume_processed`` anyway.
    """
    if not thread_id:
        return False

    from chainlit.session import ws_sessions_id

    for session in list(ws_sessions_id.values()):
        if getattr(session, "thread_id", None) != thread_id:
            continue
        for slot in ("current_task", "thread_ready_task"):
            task = getattr(session, slot, None)
            if task is not None and not task.done():
                return True
    return False


def protected_step_ids(thread_id: Optional[str]) -> Set[str]:
    """Step ids held by a live pending ask of any session on this thread.

    Deliberately includes the current (resuming) session. Deletion itself
    is first-entry-only (``session.resume_processed``), but this protection
    still matters on its own: server endpoints filter flagged steps out of
    every thread read, and a first-entry resume of one session must not
    delete the step of another live session's still-pending ask.
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
