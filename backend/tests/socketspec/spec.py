"""The scenario vocabulary: what a case says, in transport-free terms.

Nothing here names a socket.io event, a ``chainlit.socket`` function or a
session attribute. A scenario describes the *conversation's* state and the
frames that cross the wire; turning that into a session object and handler
calls is the driver's job, and is the only part that has to be written twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal, Mapping, Optional, Tuple

from .frames import Expect, Ledger


@dataclass(frozen=True)
class AskState:
    """A question the server is waiting on when the frames arrive.

    ``remaining`` is seconds left on the deadline; ``None`` means the ask has
    already expired. Expressed as an offset, never as a sleep.
    """

    step_id: str = "step-1"
    parent_id: Optional[str] = "parent-1"
    type: Literal["text", "file", "action", "element"] = "action"
    timeout: int = 60
    remaining: Optional[float] = 60.0
    actions: Tuple[Mapping[str, Any], ...] = ()
    element: Optional[Mapping[str, Any]] = None
    answered: bool = False


@dataclass(frozen=True)
class Given:
    """The state of the conversation before the frames arrive."""

    restored: bool = False
    """The session outlived the previous socket and was handed back."""

    chat_started: bool = False
    """``on_chat_start`` has already run for this session."""

    has_first_interaction: bool = True

    fresh_page_load: bool = True
    """The client lost its UI state -- a reload rather than a transport blip."""

    resuming_thread: Optional[str] = None

    pending_ask: Optional[AskState] = None

    parent_thread: Optional[str] = None

    running_task: bool = False

    last_resolved_ask_step_id: Optional[str] = None

    transcript: Tuple[Mapping[str, Any], ...] = ()
    """The conversation the server already holds, as step payloads."""


@dataclass(frozen=True)
class Incoming:
    """One frame arriving from the client."""

    tag: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    """What a driver reports back: the frames, and protocol-level state.

    ``state`` is a plain dict so a driver can report facts without the table
    knowing how they are stored. Scenarios read it by key; the keys are the
    contract, the session attributes behind them are not.
    """

    ledger: Ledger
    state: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    """One row of the table."""

    name: str
    given: Given
    when: Tuple[Incoming, ...]
    expect: Tuple[Expect, ...] = ()
    """Frames that must go out, in this relative order."""

    forbid: Tuple[str, ...] = ()
    """Tags that must not go out at all."""

    then: Optional[Callable[[Result], object]] = None
    """Extra assertions over the reported state."""

    why: str = ""
    """What breaks if this stops holding. Not decoration -- the reason the
    row is in the table at all, for whoever reads a failure."""


__all__ = ["AskState", "Given", "Incoming", "Result", "Scenario"]
