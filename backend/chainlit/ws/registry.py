"""The registry of live websocket sessions, and the policy that reads it.

A conversation is not one socket. At any moment the server may be holding
the tab the user is looking at, a tab they left open on another screen, and
a session some connection walked away from without closing. Nothing here
sends anything to any of them: there is no fan-out in this server, and there
must not be one. What looks like cross-session behaviour is a *scan of this
registry* -- "is anybody still working in this conversation?", "which of
these was abandoned mid-question?" -- and the answers decide what the
arriving session is allowed to do.

So this module is a data structure plus the predicates that read it. It
imports nothing from ``chainlit`` and nothing from the transport: not
``litestar``, not ``chainlit.protocol``, ``chainlit.socket``,
``chainlit.server`` or ``chainlit.emitter``. That independence is the point.
The registry is the one piece of server state that would have to become
shared storage the day this runs on more than one replica, and a thing that
may have to be swapped has to be testable on its own.

What the registry owns, and what it only observes
-------------------------------------------------
It owns the three facts that decide policy but do not belong to any single
session: which conversation a session is in (``thread_id``), who it belongs
to (``user_identifier``), and whether anyone is on the other end
(``connected`` -- the flag the old code kept on the session as
``socket_disconnected``). Those are indexed, so they must be changed through
``set_thread`` / ``mark_connected`` / ``mark_disconnected``, never by
assigning to the entry.

It only *observes* the work a session holds -- a live ask, a running task,
an answer parked on the handshake gate. Those live in the session object and
change without the registry's knowledge, so they are read through the narrow
``SessionView`` port at the moment a question is asked. Deadline semantics
in particular stay out of here: ``has_live_ask`` is already the answer to
"not expired and not resolved".

Deciding is not doing
---------------------
Nothing in this module deletes a session. ``claim`` returns an outcome and
``abandoned_ask_sessions`` returns candidates; the caller performs the
eviction, because eviction is an ``await`` (closing MCP sessions, removing
files) and a registry that awaits inside a scan is neither testable nor
reasonable to lock. The caller is expected to re-check each candidate with
``should_evict`` immediately before the awaiting delete: a candidate may
have reconnected while a previous delete was in flight.

No index by socket id
---------------------
The old code carried a second dict keyed by the socket.io ``sid``
(``ws_sessions_sid``) because socket.io hands a handler nothing but that
string, so the server had no other way back to the session. A raw websocket
handler holds the connection object itself and can carry its session on it,
so the reverse index would buy nothing and cost the thing it always cost: a
second mapping that has to be re-keyed on every reconnect, and that leaks
whenever a session is removed after its socket id has already moved on. It
is deliberately not here.

Per-process, single-loop
------------------------
This registry is per-process and assumes the single event loop that owns
it: no locks, and every method is synchronous so that a scan cannot be
interleaved with a mutation. For more than one replica it is not enough to
put this dict in Redis -- entries hold live ``asyncio`` objects that cannot
cross a process. Shared storage could hold only the owned facts (thread,
owner, connected, and the *observed booleans* snapshotted on change), which
makes the queries here answerable anywhere; the eviction itself would have
to become a message to the replica that holds the session, which is the
first genuine fan-out this server would ever need.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Optional, Protocol, cast

if TYPE_CHECKING:
    from chainlit.ws.session import Session

__all__ = [
    "Claim",
    "ClaimOutcome",
    "SessionEntry",
    "SessionRegistry",
    "SessionView",
    "has_live_work",
    "is_abandoned_ask_session",
    "is_owned_by",
]


class SessionView(Protocol):
    """What the registry needs to know about a session it is holding.

    Deliberately five read-only members. Everything else about a session --
    its emitter, its files, its user object, how an ask stores its deadline
    -- is none of the registry's business, and keeping the port this narrow
    is what lets the policy be tested against a five-attribute stub.
    """

    @property
    def id(self) -> str:
        """The session id: the key the client offers on connect."""

    @property
    def has_live_ask(self) -> bool:
        """A question is on screen and can still be answered.

        Already accounts for the deadline and for a reply having landed.
        """

    @property
    def has_live_task(self) -> bool:
        """Work is running that will post its own results when it finishes."""

    @property
    def has_parked_reply(self) -> bool:
        """An answer arrived early and is still waiting on the handshake.

        Nothing is running, but the session holds the only copy of something
        the user typed.
        """

    @property
    def live_ask_step_ids(self) -> Collection[str]:
        """Step ids a live ask of this session is displaying."""


class ClaimOutcome(StrEnum):
    """What happens to the session id a connecting client offers.

    The values are the four words the scenario table uses for ``on_open``.
    """

    CREATED = "created"
    """Nothing was held under that id: an ordinary first connection."""

    KEPT = "kept"
    """The held session survives and is handed the new socket."""

    REPLACED = "replaced"
    """The held session had nothing worth keeping: drop it, start over."""

    REFUSED = "refused"
    """The id is held by somebody else. The connection does not open."""


@dataclass(slots=True)
class SessionEntry:
    """One session's tenancy in the registry.

    Identity matters: the entry object, not the id, is what a pending
    eviction holds on to. An id can be re-claimed by a successor session
    while a delete of its predecessor is still awaiting, and acting on the
    id alone would then wipe the successor.
    """

    session: SessionView
    user_identifier: Optional[str] = None
    thread_id: Optional[str] = None
    connected: bool = True

    @property
    def id(self) -> str:
        return self.session.id


@dataclass(frozen=True, slots=True)
class Claim:
    """The decision about a connecting client's session id."""

    outcome: ClaimOutcome
    entry: Optional[SessionEntry] = None
    """The held entry, for every outcome but ``CREATED``."""


# --- Predicates -----------------------------------------------------------
#
# Named after what they decide, and kept out of the scans that use them so
# that each can be pinned by a test on its own.


def is_owned_by(entry: SessionEntry, user_identifier: Optional[str]) -> bool:
    """Whether this session may be handed to that user.

    An anonymous session belongs to the anonymous user and to nobody else:
    with authentication off both sides are ``None`` and match, but a named
    user must never inherit an unowned session, nor the reverse. The session
    id is a bearer token in everything but name, so guessing one must not be
    enough to reconnect to somebody else's conversation.
    """
    if entry.user_identifier is None and user_identifier is None:
        return True
    if entry.user_identifier is None or user_identifier is None:
        return False
    return entry.user_identifier == user_identifier


def has_live_work(entry: SessionEntry) -> bool:
    """Whether dropping this session would take something from the user.

    Three distinct things, and each is somebody's loss: a question waiting
    for an answer, work that was started and may have been paid for, and an
    answer that was typed and has not been filed yet.
    """
    session = entry.session
    return session.has_live_ask or session.has_live_task or session.has_parked_reply


def is_abandoned_ask_session(entry: SessionEntry, thread_id: Optional[str]) -> bool:
    """Whether this session is holding a conversation open for nobody.

    All three, and nothing else: it belongs to this conversation, its socket
    is gone, and it is parked on a question. The user who would have
    answered that question is arriving somewhere else in the same thread, so
    the question is never going to be answered where it stands -- while it
    stands, the conversation counts as busy and is never tidied up.

    Note what is *not* here. A running task does not shield an abandoned
    ask: a hook parked on a long offer keeps its task live for as long as
    the deadline lasts, and shielding on that is exactly how every reload
    used to leave another session behind. A connected session is never
    abandoned however long its question has been up -- the form is really on
    screen and the user can really answer it.
    """
    if thread_id is None or entry.thread_id != thread_id:
        return False
    return not entry.connected and entry.session.has_live_ask


class SessionRegistry:
    """Live sessions, keyed by session id and indexed by thread.

    The thread index is the part that must not be a scan: it is read on
    every resume, by the eviction sweep and by both protection queries.
    There is deliberately no index by user -- nothing asks that question,
    and a second index is a second thing to keep consistent.
    """

    def __init__(self) -> None:
        self._entries: dict[str, SessionEntry] = {}
        # thread id -> {session id: entry}, insertion-ordered, so the sweep
        # and both protection queries are O(sessions of this thread).
        self._by_thread: dict[str, dict[str, SessionEntry]] = {}

    # --- Registration -----------------------------------------------

    def register(
        self,
        session: SessionView,
        *,
        user_identifier: Optional[str] = None,
        thread_id: Optional[str] = None,
        connected: bool = True,
    ) -> SessionEntry:
        """Take a session into the registry and return its entry.

        Registering under an id that is already held evicts the previous
        entry from the indexes first: a successor created under the same id
        (what a replaced page load does) must not leave its predecessor
        behind in the thread index.
        """
        self.remove(session.id)
        entry = SessionEntry(
            session=session,
            user_identifier=user_identifier,
            thread_id=thread_id,
            connected=connected,
        )
        self._entries[entry.id] = entry
        self._index(entry)
        return entry

    def get(self, session_id: str) -> Optional[SessionEntry]:
        """The entry held under this id, or ``None`` if there is none."""
        return self._entries.get(session_id)

    def find(self, session_id: str) -> Optional["Session"]:
        """The session held under this id, without its bookkeeping.

        What the HTTP routes are given: a controller has no business with
        the entry around a session, and handing it over would let one
        write to it.
        """
        entry = self._entries.get(session_id)
        return None if entry is None else cast("Session", entry.session)

    def holds(self, entry: SessionEntry) -> bool:
        """Whether this exact entry is still the tenant of its id.

        Identity, not equality: a successor registered under the same id
        does not count.
        """
        return self._entries.get(entry.id) is entry

    def remove(self, session_id: str) -> Optional[SessionEntry]:
        """Drop the session held under this id and return it, if any."""
        entry = self._entries.pop(session_id, None)
        if entry is not None:
            self._deindex(entry)
        return entry

    def discard(self, entry: SessionEntry) -> bool:
        """Drop this exact entry, and only if it is still the tenant.

        The removal to prefer wherever a session tears itself down: a
        deferred cleanup that removed by id alone would wipe the registry
        entry of a successor created under the same id in the meantime.
        """
        if not self.holds(entry):
            return False
        self.remove(entry.id)
        return True

    # --- Owned, indexed state ---------------------------------------

    def set_thread(self, session_id: str, thread_id: Optional[str]) -> bool:
        """Move a session into a conversation, keeping the index consistent.

        The only way the thread of a registered session may change.
        """
        entry = self._entries.get(session_id)
        if entry is None:
            return False
        if entry.thread_id == thread_id:
            return True
        self._deindex(entry)
        entry.thread_id = thread_id
        self._index(entry)
        return True

    def mark_connected(self, session_id: str) -> bool:
        """Record that a socket is on the other end again."""
        return self._set_connected(session_id, True)

    def mark_disconnected(self, session_id: str) -> bool:
        """Record that the socket went away without the session closing."""
        return self._set_connected(session_id, False)

    def _set_connected(self, session_id: str, connected: bool) -> bool:
        entry = self._entries.get(session_id)
        if entry is None:
            return False
        entry.connected = connected
        return True

    # --- Queries ----------------------------------------------------

    def entries_of_thread(self, thread_id: Optional[str]) -> tuple[SessionEntry, ...]:
        """Every session of one conversation, in registration order."""
        if thread_id is None:
            return ()
        return tuple(self._by_thread.get(thread_id, {}).values())

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[SessionEntry]:
        """A snapshot, so a caller may remove sessions while iterating."""
        return iter(tuple(self._entries.values()))

    # --- Policy: what happens to the id a client offers --------------

    def claim(
        self,
        session_id: str,
        user_identifier: Optional[str] = None,
        *,
        page_load: bool = False,
    ) -> Claim:
        """Decide what a connecting client gets for the id it offered.

        Reloading has always meant "start over", and it keeps meaning that:
        an idle conversation is replaced. But by the time the reload arrives
        the server may be in the middle of something the user is still owed,
        and that survives. A transport reconnect asked for nothing, so
        nothing may be taken from it -- its conversation is kept whatever
        state it is in.

        Ownership is checked on both paths and before everything else: a
        reconnect to somebody else's id is as much a read of their
        conversation as a reload is.
        """
        entry = self._entries.get(session_id)
        if entry is None:
            return Claim(ClaimOutcome.CREATED)
        if not is_owned_by(entry, user_identifier):
            return Claim(ClaimOutcome.REFUSED, entry)
        if not page_load:
            return Claim(ClaimOutcome.KEPT, entry)
        if has_live_work(entry):
            return Claim(ClaimOutcome.KEPT, entry)
        return Claim(ClaimOutcome.REPLACED, entry)

    # --- Policy: the sweep -------------------------------------------

    def abandoned_ask_sessions(
        self,
        thread_id: Optional[str],
        *,
        arriving_session_id: Optional[str] = None,
    ) -> tuple[SessionEntry, ...]:
        """The sessions of this conversation that are holding it open for nobody.

        Read on the first entry of a new session into the resume branch,
        before the resume="delete" decision -- the whole point is that the
        conversation reads as idle again by the time that decision is made,
        so the caller must have performed the evictions before it asks
        ``has_live_task`` or ``protected_step_ids``.

        The arriving session is excluded, and cannot in practice be a
        candidate anyway: handing it the socket is what marks it connected,
        and that has already happened. Belt and suspenders.
        """
        return tuple(
            entry
            for entry in self.entries_of_thread(thread_id)
            if entry.id != arriving_session_id
            and is_abandoned_ask_session(entry, thread_id)
        )

    def should_evict(
        self,
        entry: SessionEntry,
        thread_id: Optional[str],
        *,
        arriving_session_id: Optional[str] = None,
    ) -> bool:
        """Re-check one candidate immediately before deleting it.

        Deleting awaits, so the plan is stale the moment it is made: a
        candidate may have reconnected, changed conversation or been
        replaced under its id while an earlier delete was in flight. Call
        this with no await between it and the delete.
        """
        if not self.holds(entry):
            return False
        if entry.id == arriving_session_id:
            return False
        return is_abandoned_ask_session(entry, thread_id)

    # --- Policy: what a conversation's other sessions protect ---------

    def has_live_task(self, thread_id: Optional[str]) -> bool:
        """Whether work is running anywhere in this conversation.

        A running task on *any* session means the conversation is alive, and
        the messages it is producing are not leftovers: a resume from a
        second tab that deleted them would have the running work put its
        rows back as orphans and the two feeds disagree.

        Connectedness is not consulted -- work running behind a dropped
        socket is still work, and it will post its results. Neither is the
        arriving session excluded: including it is harmless (its own slots
        are empty at resume time) and excluding it would be a second rule to
        keep true.
        """
        return any(
            entry.session.has_live_task for entry in self.entries_of_thread(thread_id)
        )

    def protected_step_ids(self, thread_id: Optional[str]) -> frozenset[str]:
        """Step ids that a live question of this conversation is displaying.

        Whose session the question belongs to is not the point -- that
        somebody is being asked is. Deleting the step from under another
        session's live question leaves that user with nothing to answer.
        """
        protected: set[str] = set()
        for entry in self.entries_of_thread(thread_id):
            if entry.session.has_live_ask:
                protected.update(entry.session.live_ask_step_ids)
        return frozenset(protected)

    # --- Index bookkeeping -------------------------------------------

    def _index(self, entry: SessionEntry) -> None:
        if entry.thread_id is None:
            return
        self._by_thread.setdefault(entry.thread_id, {})[entry.id] = entry

    def _deindex(self, entry: SessionEntry) -> None:
        if entry.thread_id is None:
            return
        bucket = self._by_thread.get(entry.thread_id)
        if bucket is None:
            return
        bucket.pop(entry.id, None)
        if not bucket:
            del self._by_thread[entry.thread_id]
