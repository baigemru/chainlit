"""The frame vocabulary, and how a socket.io emit translates into it.

Outbound frames leave the current implementation through four channels --
``emitter.emit``, ``emitter.clear``, ``emitter.send_timeout`` and
``session.emit_ask`` -- plus a dozen emitter helpers that funnel into the
first one in real code but are mocked out in tests. The ledger below is the
single ordered list all of them append to. Separate recorders per channel
could not express "the actions went out before the ask", which is an
invariant the fork already relies on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

MISSING = object()


@dataclass(frozen=True)
class Frame:
    """One outbound message, named by its protocol tag."""

    tag: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.tag}{dict(self.payload)!r}"


@dataclass(frozen=True)
class Effect:
    """An outbound call that is not a wire frame, recorded for context."""

    name: str


def _read(payload: Mapping[str, Any], path: str) -> Any:
    """Read a dotted path out of a payload, or MISSING."""
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return MISSING
    return current


@dataclass(frozen=True)
class Expect:
    """One frame the scenario requires, and the fields it cares about.

    Fields not named are not asserted -- deliberately. The table states the
    protocol, not the current payload shapes, and pinning every key would
    make it a change-detector for the implementation it is meant to outlive.
    A value may be a literal or a predicate.
    """

    tag: str
    where: Mapping[str, Any] = field(default_factory=dict)

    def matches(self, frame: Frame) -> bool:
        if frame.tag != self.tag:
            return False
        for path, expected in self.where.items():
            actual = _read(frame.payload, path)
            if actual is MISSING:
                return False
            if callable(expected):
                if not expected(actual):
                    return False
            elif actual != expected:
                return False
        return True

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.tag}{dict(self.where)!r}" if self.where else self.tag


class Ledger:
    """The ordered record of everything one scenario sent."""

    def __init__(self) -> None:
        self.frames: List[Frame] = []
        self.effects: List[Effect] = []

    def wire(self, tag: str, payload: Optional[Mapping[str, Any]] = None) -> None:
        self.frames.append(Frame(tag, dict(payload or {})))

    def effect(self, name: str) -> None:
        self.effects.append(Effect(name))

    @property
    def tags(self) -> List[str]:
        return [frame.tag for frame in self.frames]

    def count(self, expectation: Expect) -> int:
        """How many frames match. For "exactly once", which subsequence
        matching cannot say on its own."""
        return sum(1 for frame in self.frames if expectation.matches(frame))

    def find_in_order(self, expected: Sequence[Expect]) -> Optional[Expect]:
        """Return the first expectation that is not satisfiable in order.

        An ordered subsequence, not an exact stream: the scenarios assert the
        frames whose presence and relative order carry meaning, and stay
        silent about the rest.
        """
        index = 0
        for expectation in expected:
            while index < len(self.frames) and not expectation.matches(
                self.frames[index]
            ):
                index += 1
            if index == len(self.frames):
                return expectation
            index += 1
        return None


# --------------------------------------------------------------------------
# socket.io event -> protocol tag
# --------------------------------------------------------------------------

# Straight renames: the payload travels as-is under the new tag.
_RENAMES: Dict[str, str] = {
    "resume_thread": "thread.resume",
    "resume_thread_error": "thread.resume_error",
    "first_interaction": "thread.first_interaction",
    "parent_thread": "thread.parent",
    "open_thread": "thread.open",
    "chat_profile_changed": "profile.changed",
    "set_chat_profile": "session.handoff",
    "audio_interrupt": "audio.interrupt",
    "toast": "toast",
    "reload": "reload",
    "window_message": "window.message",
    "call_fn": "rpc.call",
}

# The payload moves under a named key -- the old event shipped a bare value.
_WRAPPED: Dict[str, Tuple[str, str]] = {
    "new_message": ("step.upsert", "step"),
    "update_message": ("step.update", "patch"),
    "delete_message": ("step.delete", "step"),
    "stream_start": ("step.stream.start", "step"),
    "stream_token": ("step.stream.token", "token"),
    "element": ("element.upsert", "element"),
    "remove_element": ("element.remove", "element"),
    "action": ("action.add", "action"),
    "remove_action": ("action.remove", "action"),
    "ask": ("ask.start", "ask"),
    "chat_settings": ("settings.set", "inputs"),
    "set_commands": ("commands.set", "commands"),
    "set_modes": ("modes.set", "modes"),
    "set_favorites": ("favorites.set", "steps"),
    "token_usage": ("token.usage", "count"),
    "audio_connection": ("audio.connection", "state"),
}

# Collapsed pairs. The reason is only knowable for the timeout half: the
# legacy `clear_ask` / `clear_call_fn` carry no reason at all, so the table
# must not pin one on them. Phase 5 tightens this, it cannot be tightened here
# without inventing information the current wire does not carry.
_COLLAPSED: Dict[str, Tuple[str, Dict[str, Any]]] = {
    "ask_timeout": ("ask.end", {"reason": "timeout"}),
    "clear_ask": ("ask.end", {}),
    "call_fn_timeout": ("rpc.cancel", {"reason": "timeout"}),
    "clear_call_fn": ("rpc.cancel", {}),
    "task_start": ("task.indicator", {"running": True}),
    "task_end": ("task.indicator", {"running": False}),
}


def translate(event: str, payload: Any = None) -> Tuple[str, Dict[str, Any]]:
    """Turn one socket.io event into its protocol tag and payload."""
    if event in _COLLAPSED:
        tag, extra = _COLLAPSED[event]
        return tag, dict(extra)
    if event in _RENAMES:
        return _RENAMES[event], _as_payload(payload)
    if event in _WRAPPED:
        tag, key = _WRAPPED[event]
        return tag, {key: payload}
    raise KeyError(
        f"No protocol tag for socket.io event {event!r}. Add it to "
        f"tests/socketspec/frames.py -- an unmapped event would otherwise "
        f"vanish from the ledger and the scenario would pass by silence."
    )


def _as_payload(payload: Any) -> Dict[str, Any]:
    return dict(payload) if isinstance(payload, Mapping) else {"value": payload}


def ask_start(payload: Mapping[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """``emit_ask({"msg": ..., "spec": ...})`` under the new field names."""
    return "ask.start", {"step": payload.get("msg"), "spec": payload.get("spec")}


__all__ = [
    "Effect",
    "Expect",
    "Frame",
    "Ledger",
    "ask_start",
    "translate",
]
