"""What happens when a client says ``hello``.

The first frame names a conversation, and nothing else: no session id, no
claim on anything the server is holding except the thread in the address
bar. So arriving is a lookup of that thread and one of two answers -- the
session that is in it takes this socket, or this socket begins a session.
A reload, a second tab and a dropped network all say the same sentence and
all get the same answer; what tells them apart is only how much of the
screen the replay has to rebuild (``page_load``).

Two properties of this module are load-bearing and easy to lose.

**The side effects happen once.** The resume is irreversible -- the hooks it
fires, the steps it hides -- and belongs to the *first* entry into the
resume branch, not to every reconnect that follows.

**The replay is not a blocking prefix.** ``session.ready`` goes out before
it, because that is the frame the client flushes its outbound buffer on --
which is exactly how an answer typed before the reload reaches us *during*
the restore. So the restore re-reads the session between its awaits: an
``ask.reply`` that lands mid-restore forbids the ``ask.start`` we were
about to send.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    runtime_checkable,
)

import msgspec

from chainlit.protocol.payloads import Thread as ThreadPayload
from chainlit.protocol.server import (
    ActionAdd,
    AskStart,
    ElementUpsert,
    SessionReady,
    StepUpsert,
    TaskIndicator,
    ThreadFirstInteraction,
    ThreadParent,
    ThreadResume,
)
from chainlit.ws.registry import Claim, ClaimOutcome, SessionRegistry
from chainlit.ws.session import Session, TranscriptEntry
from chainlit.ws.sidebar import state_frame

__all__ = [
    "Arrival",
    "ThreadStore",
    "arrive",
    "ready_frame",
    "restore",
    "resume_frame",
]


# The metadata flag a message sets with ``resume="delete"``. Defined where it
# is read; ``controllers/project.py`` carries the same two literals for the
# HTTP read path.
RESUME_POLICY_KEY = "resume_policy"
RESUME_POLICY_DELETE = "delete"


@runtime_checkable
class ThreadStore(Protocol):
    """What the handshake needs from persistence, and nothing else.

    A port rather than an import: the scenario table drives this module
    with a stub, and the handshake is the one place where "what the server
    remembers" and "what was written down" have to be told apart.
    """

    async def transcript_of(self, thread_id: str) -> Sequence[TranscriptEntry]:
        """The conversation as it was written down, oldest first."""
        ...


@dataclass
class Arrival:
    """The outcome of one ``hello``, and everything decided about it.

    Also the handshake's own scratchpad. ``on_arrival`` decides what this
    connection *is* -- a resume, a chat that has not started -- and both the
    replay and ``on_ready`` need that answer; the two run on either side of
    ``session.ready``, so it has to be carried rather than recomputed. It
    used to travel as ``session.state["__resumed_thread"]``: a string key in
    the dict the *application* keeps its own state in, popped by whoever
    read it first, and persisted into thread metadata unless something
    remembered to filter it out.
    """

    outcome: ClaimOutcome
    session: Session
    fresh_page_load: bool = True
    #: The stored thread this arrival resumed, if it resumed one. Both the
    #: snapshot the replay sends and the dict the hooks receive.
    resumed_thread: Optional[Mapping[str, Any]] = None
    #: Whether this arrival begins a chat, and so owes it an ``on_chat_start``
    #: once the screen is ready. Decided with the rest; a reconnect is not a
    #: beginning and never carries it.
    start_chat: bool = False


async def arrive(
    *,
    registry: SessionRegistry,
    user_identifier: Optional[str],
    page_load: bool,
    thread_id: Optional[str],
    make_session: Callable[[Optional[str]], Session],
) -> Arrival:
    """Decide what the conversation this client names is allowed to mean.

    ``kept``      somebody is in that conversation and it is this user:
                  the session survives and takes this socket. The socket it
                  was wearing is closed 4409 by the caller.
    ``created``   nobody is in it, or the one who is is a stranger. Either
                  way this connection gets a session of its own -- on the
                  thread it asked for, or on a fresh one when the thread is
                  somebody else's, which is the same answer a thread that
                  never existed gets.

    Nothing awaits between the claim and the ``register`` that acts on it.
    That gap is the whole guard on the one-session-per-thread key: a second
    hello landing inside it would claim the same free thread and the second
    ``register`` would raise.
    """
    claim: Claim = registry.claim(thread_id, user_identifier)

    if claim.outcome is ClaimOutcome.KEPT:
        assert claim.entry is not None
        held = claim.entry.session
        assert isinstance(held, Session)
        held.connected = True
        registry.mark_connected(held.id)
        if held.reaper is not None and not held.reaper.done():
            # The user is back inside the grace period. The teardown that
            # was waiting for them is the one thing this arrival must stop.
            held.reaper.cancel()
            held.reaper = None
        # A page load is the client saying it lost its screen. The session
        # survives; the replay that follows is unconditional either way,
        # but only a page load means the browser is holding nothing.
        return Arrival(outcome=claim.outcome, session=held, fresh_page_load=page_load)

    # A thread held by a stranger is not asked for again: the session is
    # minted on one of its own, and there is nothing to look up in storage
    # either -- a thread somebody else is live in is a thread whose absence
    # is not this user's to hear about.
    requested = thread_id if thread_id and claim.entry is None else None
    session = make_session(requested)
    #: Only an asked-for thread can be missing. A minted one is not in the
    #: database yet and never will be until somebody speaks in it.
    session.requested_thread_id = requested
    assert session.thread_id is not None, "a session is minted into a thread"
    registry.register(
        session,
        thread_id=session.thread_id,
        user_identifier=user_identifier,
        connected=True,
    )
    return Arrival(outcome=claim.outcome, session=session, fresh_page_load=True)


def ready_frame(session: Session, *, restored: bool, heartbeat_ms: int) -> SessionReady:
    """The frame that ends the handshake and releases the client's buffer.

    ``thread_id`` goes out on *every* branch, ``kept`` included. The client
    used to learn it only from ``thread.first_interaction``, which is sent
    once per session -- so a reload into a session that had already had its
    first interaction came back with no thread id, and the feedback buttons
    stayed dead for the rest of the conversation.
    """
    return SessionReady(
        session_id=session.id,
        thread_id=session.thread_id,
        chat_profile=session.chat_profile,
        restored=restored,
        heartbeat_interval_ms=heartbeat_ms,
    )


def resume_frame(thread: ThreadPayload) -> ThreadResume:
    """The whole-thread snapshot that replaces the client's feed."""
    return ThreadResume(thread=thread)


async def restore(
    session: Session,
    *,
    thread_store: Optional[ThreadStore] = None,
    fresh_page_load: bool = True,
    resumed_thread: Optional[Mapping[str, Any]] = None,
) -> None:
    """Rebuild the client's screen, in the order it has to be rebuilt in.

    The order is normative, and every rule below is a bug it fixed:

    1. the conversation, oldest first -- a form is answered in the light of
       what came before it;
    2. attachments with their step, deduplicated -- an element the server
       holds live and has also written down must not go twice;
    3. the buttons before the form -- a form that arrives first is a form
       with no buttons;
    4. the form, carrying what is *left* of its deadline, never a fresh one;
    5. the element panel, whole, with the elements it names ahead of it --
       nothing else in the replay mentions them.

    Nothing here deletes. The steps a resume takes away are hidden by
    ``controllers.project.hide_resume_deleted`` before the snapshot is
    built, and the same filter serves the HTTP read path.

    ``resumed_thread`` is handed in rather than read off the session: what a
    hello means is the runner's decision, made once in ``on_arrival``, and
    the order the answer is drawn in is this module's.
    """
    entries: Sequence[TranscriptEntry] = list(session.transcript)
    snapshot = resumed_thread
    if (
        not entries
        and snapshot is None
        and thread_store is not None
        and session.thread_id
        and session.resumed_thread_id == session.thread_id
    ):
        # Nothing in memory: this session did not live through the
        # conversation it is showing. Fall back to what was written down --
        # but only for a thread the application has already let this
        # session resume. The id in the hello is the client's claim, not
        # its right, and a refused claim must not be answered from storage.
        entries = await thread_store.transcript_of(session.thread_id)

    if session.first_interaction and session.thread_id:
        session.send(
            ThreadFirstInteraction(
                interaction=session.first_interaction, thread_id=session.thread_id
            )
        )

    if session.parent_thread_id:
        # Re-sent on every reconnect by design: it is level state about
        # where this conversation came from, and the client loses it. After
        # the thread's own frame: a parent is said of a thread that exists.
        session.send(ThreadParent(parent_thread_id=session.parent_thread_id))

    if snapshot is not None:
        # The session has just resumed a stored thread and the client's feed
        # is whatever it had before: a snapshot *replaces* it, the way the
        # client understands ``thread.resume``. The transcript replay below
        # would say the same thing one step at a time.
        session.send(resume_frame(msgspec.convert(snapshot, ThreadPayload)))
    else:
        sent_elements: Set[str] = set()
        for entry in entries:
            session.send(StepUpsert(step=entry.step))
            for element in entry.elements:
                if element.id in sent_elements:
                    continue
                sent_elements.add(element.id)
                session.send(ElementUpsert(element=element))

    # Read here, not above: everything before this may have awaited, and an
    # answer is free to have landed in any of those awaits. Restoring a
    # question that has just been answered puts a form back on screen for
    # something the user has already dealt with.
    ask = session.pending_ask
    if ask is not None and ask.is_live:
        for action in ask.restore_actions:
            session.send(ActionAdd(action=action))
        if ask.restore_element is not None and fresh_page_load:
            # A transport blip keeps the element the client is still
            # holding; only a reload has lost it.
            session.send(ElementUpsert(element=ask.restore_element))
        # What is *left* of the deadline, never a fresh one: a form that
        # resets its own timer on every network hiccup never times out.
        session.send(
            AskStart(
                step=ask.step,
                spec=msgspec.structs.replace(
                    ask.spec, timeout=max(0, int(ask.remaining))
                ),
            )
        )

    # The element panel, after the form and before the spinner: it is the
    # last piece of *screen*, and it is sent whatever it holds. An empty,
    # hidden panel is still stated -- the frame is idempotent, and a client
    # that reloaded in the middle of a preview needs to be told the panel is
    # not there any more rather than left showing its own last guess.
    #
    # The elements go first, by id, because the frame names them and nothing
    # else will: elements of the panel never enter the transcript (they hang
    # off no step, so their ``forId`` is absent), so the replay above has not
    # mentioned them. Deduplicated across slots for the same reason the transcript's
    # attachments are -- one element can be in two tabs.
    sent_slot_elements: Set[str] = set()
    for slot in session.sidebar.slots:
        for element in slot.elements:
            if element.id in sent_slot_elements:
                continue
            sent_slot_elements.add(element.id)
            session.send(ElementUpsert(element=element))
    session.send(state_frame(session.sidebar))

    # Level-triggered, and last: the client's spinner is a boolean, and the
    # only honest value for it is the one that is true once everything else
    # has been said.
    session.send(TaskIndicator(running=session.is_busy))
