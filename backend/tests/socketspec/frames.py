"""The frame vocabulary, and how a socket.io emit translates into it.

A frame is a protocol tag and a payload; the ledger is the ordered list of
them. Nothing here knows how a frame reached the wire, and in particular no
socket.io event name appears -- those live with the driver that speaks them,
because a name is as much of a transport dependency as an import is, and the
boundary test cannot see a string literal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence

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


__all__ = [
    "Effect",
    "Expect",
    "Frame",
    "Ledger",
]
