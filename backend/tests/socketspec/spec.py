"""The scenario vocabulary: what a case says, in transport-free terms.

Nothing here names a socket.io event, a ``chainlit.socket`` function or a
session attribute. A scenario describes the *conversation's* state and the
frames that cross the wire; turning that into a session object and handler
calls is the driver's job, and is the only part that has to be written twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Tuple,
    Union,
)

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
class TranscriptStep:
    """One message the server already holds for this conversation.

    ``elements`` are attachments the server still has as live objects;
    ``stored_elements`` are the dicts recorded when the conversation was
    rebuilt from storage, where the live objects no longer exist. Both end up
    on the wire, and an attachment present in both must not go out twice.
    """

    id: str
    output: str = ""
    wait: Optional[Mapping[str, Any]] = None
    elements: Tuple[Mapping[str, Any], ...] = ()
    stored_elements: Tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class Handover:
    """A message parked by the session that handed this one its thread.

    A profile switch mints the successor's *thread* server-side and parks
    the message under it, so the value never travels through the browser.
    The record carries the previous thread's id even when there is no
    message at all -- that is how the new thread learns what it descends
    from. Finding the record is also how the server knows this arrival is a
    handover and not a resume.
    """

    message: Optional[str] = None
    parent: Optional[str] = None
    foreign: bool = False
    """Parked by a different user. Must not be delivered to this one."""


@dataclass(frozen=True)
class Bystander:
    """A session in *another* conversation.

    It used to mean another session in this one -- a second tab, or a
    connection that walked away without closing. A thread holds one session
    now, so that is not a state the server can be in, and a row that states
    one is refused by the driver rather than built. What is left is the
    rule it was always really about: one conversation says nothing about
    any other.
    """

    connected: bool = True
    pending_ask: Optional[AskState] = None
    running_task: bool = False
    thread: Optional[str] = None
    """Which conversation it belongs to. Must name one, and not this one."""


@dataclass(frozen=True)
class Given:
    """The state of the conversation before the frames arrive."""

    restored: bool = False
    """The session outlived the previous socket and is handed back to this one.

    Causal in neither direction and checked in one: the hand-back happens
    because the hello names the conversation a held session is in, which the
    driver does for any row that holds one -- but a row that *states* it is
    held to its word, and the driver fails if ``session.ready`` comes back
    with ``restored: false``.

    It used to double as the flag for a profile switch's successor, which is
    the opposite kind of arrival: nothing is held, nothing is handed back,
    and the frame says ``restored: false``. That is ``switch_successor``
    below. One field meaning both left rows asserting a hand-back that never
    happened.
    """

    switch_successor: bool = False
    """This arrival is the successor a profile switch minted a thread for.

    Not a hand-back: the switch parks a record under a **new** thread and
    tears its own session down, so nothing is live in the conversation the
    successor names and the arrival creates a session like any other cold
    open. What makes it a handover is the record waiting under the thread,
    which is ``handover``; this only tells the driver not to build a held
    session for a row that would otherwise get one.
    """

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

    transcript: Tuple[TranscriptStep, ...] = ()
    """The conversation the server already holds."""

    hooks: Tuple[Literal["chat_start", "chat_resume", "thread_ready"], ...] = ()
    """Which callbacks the running application registered.

    Application state, not transport state: the same app runs on either
    transport, and several handshake branches exist only because a hook is
    registered. A hook is not a frame -- what a scenario asserts about one is
    how many times it ran and what it could see when it did, both reported
    through ``Result.state``.
    """

    chat_profile: Optional[str] = None
    """The profile this session runs under, if any."""

    handover: Optional[Handover] = None
    """A record parked for this session by the one it succeeds."""

    server_holds_session: bool = True
    """Whether a session is live in the conversation the client names.

    The one fact the arrival turns on: a held conversation is handed over,
    a free one is begun. Set it ``False`` for every cold open -- a first
    visit, and a thread read back from storage after the reaper, which is
    reachable only when nobody is in it.

    Only meaningful for the frame that opens a connection; every other frame
    in the table is addressed to a session that exists by definition.
    """

    owned_by_someone_else: bool = False
    """The held session belongs to a different user than the one arriving.

    Not a refusal: the arriving user is given a conversation of their own
    and told nothing about this one.
    """

    parked_reply: bool = False
    """An answer arrived early and is still waiting for the handshake to end.

    A session holding one is doing work even though nothing is running: the
    only copy of something the user typed is inside it.
    """

    bystanders: Tuple[Bystander, ...] = ()
    """Sessions of other conversations, live when the frames arrive."""

    produced_between_connections: Tuple[Mapping[str, Any], ...] = ()
    """Steps the conversation adds after the first frame is handled.

    The only way the table can say "and then time passed". A scenario about
    what a *reconnect* must not undo needs something to exist that the first
    connection did not know about, and no arrangement of inbound frames can
    express that on its own.
    """

    undeletable: Tuple[str, ...] = ()
    """Ids the storage refuses to delete, however often it is asked.

    A deletion that cannot be completed is not the same as one that was not
    attempted, and the difference is the whole retry contract.
    """

    stored_thread: Optional[Mapping[str, Any]] = None
    """What persistence has for this thread, if persistence is configured.

    ``None`` means there is no data layer at all -- the default. An empty
    mapping means there is one and it has nothing for this thread, which is
    the ``None`` a real data layer answers an unknown id with.
    """

    during_restore: Optional[Literal["answer", "successor", "successor_dead"]] = None
    """Something that happens while the form is being rebuilt.

    Rebuilding an ask is not atomic -- every frame it sends is an await, and
    the client is free to answer, or the app to ask something else, in any of
    those gaps. The three cases differ in what the server must do with the
    form it was halfway through re-sending:

    ``answer``
        the reply lands; the form must come down, not go up.
    ``successor``
        another ask took the slot and has already sent its own form; ending
        the ask now would wipe a form the server is still waiting on.
    ``successor_dead``
        the slot changed hands to something already finished; nothing live is
        on screen, so the form comes down.
    """


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

    superseded: str = ""
    """Why the row no longer states the protocol, when it does not.

    A row is retired here, never deleted: the decision it encoded was
    reversed on purpose, and the reversal has to stay readable next to what
    it reversed. The text names the reversal and points at where the new
    behaviour lives. ``test_spec`` skips a superseded row with this reason.
    """


def assert_that(condition: object, message: str) -> bool:
    """Assert inside a lambda: a scenario's ``then`` is data, not a function body.

    Returns a value so several of them compose into one tuple expression.
    """
    assert condition, message
    return True


class Driver(Protocol):
    """Runs one scenario against one implementation and reports what happened.

    Deliberately allowed to be synchronous. Litestar's ``WebSocketTestSession``
    is driven by a ``queue.Queue`` on a blocking portal and has no async
    variant in 2.24 -- ``AsyncTestClient.websocket_connect`` hands back the
    same synchronous object -- so the driver for the new transport will be a
    plain function. A contract that insisted on a coroutine would have to be
    rewritten the moment it met the primitive it exists to drive.
    """

    def __call__(self, scenario: "Scenario") -> Union[Result, Awaitable[Result]]: ...


__all__ = [
    "AskState",
    "Bystander",
    "Driver",
    "Given",
    "Handover",
    "Incoming",
    "Result",
    "Scenario",
    "TranscriptStep",
    "assert_that",
]
