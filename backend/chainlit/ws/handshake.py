"""What happens when a client says ``hello``.

The first frame is a *decision*, not a lookup. The id a browser offers is
the same whether it reloaded the page or merely lost its network for a
moment, and by the time it arrives the server may be holding something the
user is still owed: a question waiting for an answer, work that was paid
for, an answer typed just before the reload and not yet filed. So arriving
means: keep what is running, and only start over when nothing is.

Two properties of this module are load-bearing and easy to lose.

**The side effects happen once.** Evicting the sessions this arrival
supersedes is irreversible, and so is the resume itself (the hooks it
fires, the steps it hides). They belong to the *first* entry into the
resume branch, not to every reconnect that follows.

**The replay is not a blocking prefix.** ``session.ready`` goes out before
it, because that is the frame the client flushes its outbound buffer on --
which is exactly how an answer typed before the reload reaches us *during*
the restore. So the restore re-reads the session between its awaits: an
``ask.reply`` that lands mid-restore forbids the ``ask.start`` we were
about to send.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    List,
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
from chainlit.ws.registry import Claim, ClaimOutcome, SessionEntry, SessionRegistry
from chainlit.ws.session import Session, TranscriptEntry

__all__ = [
    "Arrival",
    "ThreadStore",
    "arrive",
    "ready_frame",
    "restore",
    "resume_frame",
    "sweep_superseded",
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
    connection *is* -- a resume, a chat that has not started, a client
    asking for a thread that is not there -- and both the replay and
    ``on_ready`` need that answer; the two run on either side of
    ``session.ready``, so it has to be carried rather than recomputed. It
    used to travel as ``session.state["__resumed_thread"]`` and
    ``["__thread_not_found"]``: string keys in the dict the *application*
    keeps its own state in, popped by whoever read them first, and
    persisted into thread metadata unless something remembered to filter
    them out.
    """

    outcome: ClaimOutcome
    session: Optional[Session]
    fresh_page_load: bool = True
    #: Sessions this arrival supersedes. Already out of the registry; the
    #: caller still has to tear each one down.
    superseded: List[SessionEntry] = field(default_factory=list)
    #: The stored thread this arrival resumed, if it resumed one. Both the
    #: snapshot the replay sends and the dict the hooks receive.
    resumed_thread: Optional[Mapping[str, Any]] = None
    #: The thread the client asked for and did not get, if it asked for one
    #: that is not there or is not its own. Reported after ``session.ready``.
    missing_thread: Optional[str] = None
    #: Whether this arrival begins a chat, and so owes it an ``on_chat_start``
    #: once the screen is ready. Decided with the rest; a reconnect is not a
    #: beginning and never carries it.
    start_chat: bool = False

    @property
    def refused(self) -> bool:
        return self.outcome is ClaimOutcome.REFUSED


async def arrive(
    *,
    registry: SessionRegistry,
    session_id: str,
    user_identifier: Optional[str],
    page_load: bool,
    thread_id: Optional[str],
    make_session: Callable[[str], Session],
) -> Arrival:
    """Decide what the id this client offers is allowed to mean.

    Four outcomes, and their names are the scenario table's:

    ``refused``   the id belongs to somebody else. Nothing is created, and
                  the client is told nothing beyond the close code --
                  "that session exists but is not yours" says it exists.
    ``kept``      the held session is still doing something, so it survives
                  and takes this socket.
    ``replaced``  a page load landed on an idle session. This is what
                  reloading has always meant, and it has to keep meaning it.
    ``created``   nothing was held under that id.
    """
    claim: Claim = registry.claim(session_id, user_identifier, page_load=page_load)

    if claim.outcome is ClaimOutcome.REFUSED:
        return Arrival(outcome=claim.outcome, session=None)

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

    superseded: List[SessionEntry] = []
    if claim.outcome is ClaimOutcome.REPLACED and claim.entry is not None:
        superseded.append(claim.entry)
        registry.discard(claim.entry)

    session = make_session(session_id)
    if thread_id:
        # The client's choice wins over whatever the factory minted: this is
        # a resume. Without one the session keeps the thread it was born
        # with, so it has a thread id from its first frame onwards -- and
        # nothing to look up: only a thread that was asked for can be
        # missing.
        session.thread_id = thread_id
        session.requested_thread_id = thread_id
    registry.register(
        session,
        user_identifier=user_identifier,
        thread_id=session.thread_id,
        connected=True,
    )
    return Arrival(
        outcome=claim.outcome,
        session=session,
        fresh_page_load=True,
        superseded=superseded,
    )


def sweep_superseded(
    registry: SessionRegistry, thread_id: Optional[str], arriving: Session
) -> List[SessionEntry]:
    """Evict the sessions this arrival takes over from.

    Only sessions of *this* thread, only ones nobody is looking at, and
    only ones parked on a question. A disconnected session working between
    questions is still working; a connected one showing the same question
    is somebody's second tab.

    A running task is deliberately not a shield. It protects steps from
    deletion, which is a different question -- and conflating the two is
    the regression this exists for, because an offer that waits hours keeps
    its task alive for every one of them.

    Returns what it evicted rather than tearing it down, so deciding and
    doing stay separable and the caller can await the teardown.
    """
    if thread_id is None:
        return []
    doomed = registry.abandoned_ask_sessions(thread_id, arriving_session_id=arriving.id)
    for entry in doomed:
        registry.discard(entry)
    return list(doomed)


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
    4. the form last, carrying what is *left* of its deadline, never a
       fresh one.

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

    # Level-triggered, and last: the client's spinner is a boolean, and the
    # only honest value for it is the one that is true once everything else
    # has been said.
    session.send(TaskIndicator(running=session.is_busy))
