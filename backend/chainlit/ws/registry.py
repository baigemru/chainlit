"""The registry of live sessions, keyed by the conversation they are in.

One conversation, one session. The thread id is the identity on this wire
-- it is what the URL says, what the client offers in ``hello`` and the only
thing it may offer -- so the registry is a map from thread to the session
holding it, and a second socket asking for a thread somebody is already in
is not a second session: it is that session changing hands.

That is the whole of the policy. ``claim`` answers *kept* when the thread is
held by this user and *created* when it is free -- or held by somebody else,
which is answered with a thread of the arriving user's own rather than with
a refusal, because "that thread is not yours" is an answer about a row that
exists. The session id the server mints is a handle for the HTTP routes
(uploads, actions, custom-element writes) and never a claim on anything; it
is indexed here only so those routes can find their session.

What the old shape carried, and why none of it is left
------------------------------------------------------
Keyed by session id, several sessions could sit in one conversation at once,
and everything that followed was bookkeeping for that: a thread index that
was a set, a sweep for the ones parked on a question nobody would answer,
``REPLACED`` for a reload onto an idle session, ``REFUSED`` for an id that
was somebody else's. With one session per thread, a reload is the same
session handed a new socket, a second tab is a takeover, and there is no
such thing as a bystander to sweep.

So this module is a data structure plus the predicates that read it. It
imports nothing from ``chainlit`` and nothing from the transport: not
``litestar``, not ``chainlit.protocol``, ``chainlit.ws.connection``,
``chainlit.server`` or ``chainlit.emitter``. That independence is the point.
The registry is the one piece of server state that would have to become
shared storage the day this runs on more than one replica, and a thing that
may have to be swapped has to be testable on its own.

What the registry owns, and what it only observes
-------------------------------------------------
It owns the three facts that decide policy but do not belong to any single
session: which conversation a session is in (``thread_id``, the primary
key), who it belongs to (``user_identifier``), and whether anyone is on the
other end (``connected``). Those are indexed, so they must be changed
through ``set_thread`` / ``mark_connected`` / ``mark_disconnected``, never
by assigning to the entry.

It only *observes* the work a session holds -- a live ask, a running task,
an answer parked on the handshake gate. Those live in the session object and
change without the registry's knowledge, so they are read through the narrow
``SessionView`` port at the moment a question is asked. Deadline semantics
in particular stay out of here: ``has_live_ask`` is already the answer to
"not expired and not resolved".

Deciding is not doing
---------------------
Nothing in this module tears a session down. ``claim`` returns an outcome;
the caller performs whatever follows, because a teardown is an ``await``
(closing writers, removing files) and a registry that awaits inside a
lookup is neither testable nor reasonable to lock.

Per-process, single-loop
------------------------
This registry is per-process and assumes the single event loop that owns
it: no locks, and every method is synchronous so that a read cannot be
interleaved with a mutation. For more than one replica it is not enough to
put this dict in Redis -- entries hold live ``asyncio`` objects that cannot
cross a process. Shared storage could hold only the owned facts (thread,
owner, connected), which makes the queries here answerable anywhere; the
takeover itself would have to become a message to the replica holding the
session, which is the first genuine fan-out this server would ever need.
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
    "ThreadHeld",
    "is_owned_by",
]


class ThreadHeld(RuntimeError):
    """A session was registered on a thread another session already holds.

    Never a client's doing: ``claim`` answers *kept* for a held thread, so
    reaching ``register`` with one means the caller skipped the claim or
    awaited between the two. Loud rather than silent, because the quiet
    alternatives are both worse -- overwriting orphans a live session in
    somebody's browser, and keeping both brings back the fan-out this key
    exists to forbid.
    """


class SessionView(Protocol):
    """What the registry needs to know about a session it is holding.

    Deliberately five read-only members. Everything else about a session --
    its emitter, its files, its user object, how an ask stores its deadline
    -- is none of the registry's business, and keeping the port this narrow
    is what lets the policy be tested against a five-attribute stub.
    """

    @property
    def id(self) -> str:
        """The handle the server minted for this session.

        Not an identity the client may claim: it is how an HTTP route finds
        the session that rendered the button it is pressing.
        """

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
    """What happens to the thread a connecting client asks for."""

    CREATED = "created"
    """Nobody is in that conversation -- or the one who is, is not this
    user. Either way this connection begins a session of its own."""

    KEPT = "kept"
    """The session in that conversation survives and takes this socket."""


@dataclass(slots=True)
class SessionEntry:
    """One session's tenancy in the registry.

    Identity matters: the entry object, not the thread id, is what a
    deferred cleanup holds on to. A thread can be re-claimed by a successor
    session while a teardown of its predecessor is still awaiting, and
    acting on the id alone would then wipe the successor.
    """

    session: SessionView
    thread_id: str
    user_identifier: Optional[str] = None
    connected: bool = True

    @property
    def id(self) -> str:
        return self.session.id


@dataclass(frozen=True, slots=True)
class Claim:
    """The decision about the thread a connecting client asked for."""

    outcome: ClaimOutcome
    entry: Optional[SessionEntry] = None
    """For ``KEPT``, the session being handed over. For ``CREATED``, the
    tenant that made the requested thread unavailable, if there was one --
    the arriving connection is told nothing about it, but the handshake has
    to know the thread it asked for is not the one it is getting, and
    learning that from the claim saves a second lookup racing this one."""


# --- Predicates -----------------------------------------------------------


def is_owned_by(entry: SessionEntry, user_identifier: Optional[str]) -> bool:
    """Whether this session may be handed to that user.

    An anonymous session belongs to the anonymous user and to nobody else:
    with authentication off both sides are ``None`` and match, but a named
    user must never inherit an unowned session, nor the reverse.

    With authentication off this is true of every pair, so the thread id in
    the URL is the whole of the capability -- a uuid4, like a share link,
    and anyone who has it takes the conversation over. That is the deal an
    anonymous deployment makes; a deployment that cannot make it turns
    authentication on.
    """
    if entry.user_identifier is None and user_identifier is None:
        return True
    if entry.user_identifier is None or user_identifier is None:
        return False
    return entry.user_identifier == user_identifier


class SessionRegistry:
    """Live sessions, keyed by thread and indexed by session id.

    The thread map is the registry; the id map is a lookup table for the
    HTTP routes, which are handed a session id and nothing else. There is
    deliberately no index by user -- nothing asks that question, and a third
    index is a third thing to keep consistent.
    """

    def __init__(self) -> None:
        self._by_thread: dict[str, SessionEntry] = {}
        self._by_id: dict[str, SessionEntry] = {}

    # --- Registration -----------------------------------------------

    def register(
        self,
        session: SessionView,
        *,
        thread_id: str,
        user_identifier: Optional[str] = None,
        connected: bool = True,
    ) -> SessionEntry:
        """Take a session into the registry and return its entry.

        Raises:
            ThreadHeld: something is already in that conversation.

        The session id is not checked, because it is not offered by anybody:
        the server mints a fresh uuid4 for every session it creates, so the
        secondary index cannot collide unless the mint is broken.
        """
        held = self._by_thread.get(thread_id)
        if held is not None:
            raise ThreadHeld(f"thread {thread_id} is already held by session {held.id}")
        entry = SessionEntry(
            session=session,
            thread_id=thread_id,
            user_identifier=user_identifier,
            connected=connected,
        )
        self._by_thread[thread_id] = entry
        self._by_id[entry.id] = entry
        return entry

    def get(self, session_id: str) -> Optional[SessionEntry]:
        """The entry of the session with this handle, if it is still here."""
        return self._by_id.get(session_id)

    def entry_of_thread(self, thread_id: Optional[str]) -> Optional[SessionEntry]:
        """The entry holding this conversation, if anybody is in it."""
        if thread_id is None:
            return None
        return self._by_thread.get(thread_id)

    def find(self, session_id: str) -> Optional["Session"]:
        """The session with this handle, without its bookkeeping.

        What the HTTP routes are given: a controller has no business with
        the entry around a session, and handing it over would let one
        write to it.
        """
        entry = self._by_id.get(session_id)
        return None if entry is None else cast("Session", entry.session)

    def find_thread(self, thread_id: Optional[str]) -> Optional["Session"]:
        """The session in this conversation, for the routes that act on threads."""
        entry = self.entry_of_thread(thread_id)
        return None if entry is None else cast("Session", entry.session)

    def holds(self, entry: SessionEntry) -> bool:
        """Whether this exact entry is still the tenant of its thread.

        Identity, not equality: a successor registered on the same thread
        does not count.
        """
        return self._by_thread.get(entry.thread_id) is entry

    def discard(self, entry: SessionEntry) -> bool:
        """Drop this exact entry, and only if it is still the tenant.

        The removal to prefer everywhere: a deferred cleanup that removed by
        thread alone would wipe the registry entry of a successor that took
        the conversation over in the meantime.
        """
        if not self.holds(entry):
            return False
        del self._by_thread[entry.thread_id]
        if self._by_id.get(entry.id) is entry:
            del self._by_id[entry.id]
        return True

    # --- Owned, indexed state ---------------------------------------

    def set_thread(self, session_id: str, thread_id: str) -> bool:
        """Move a session into another conversation, re-keying the registry.

        The only way the thread of a registered session may change, and the
        one caller is the disown: a session that asked for a thread it may
        not have is given one of its own.

        Raises:
            ThreadHeld: the conversation it is being moved into is occupied.
        """
        entry = self._by_id.get(session_id)
        if entry is None:
            return False
        if entry.thread_id == thread_id:
            return True
        held = self._by_thread.get(thread_id)
        if held is not None:
            raise ThreadHeld(f"thread {thread_id} is already held by session {held.id}")
        del self._by_thread[entry.thread_id]
        entry.thread_id = thread_id
        self._by_thread[thread_id] = entry
        return True

    def mark_connected(self, session_id: str) -> bool:
        """Record that a socket is on the other end again."""
        return self._set_connected(session_id, True)

    def mark_disconnected(self, session_id: str) -> bool:
        """Record that the socket went away without the session closing."""
        return self._set_connected(session_id, False)

    def _set_connected(self, session_id: str, connected: bool) -> bool:
        entry = self._by_id.get(session_id)
        if entry is None:
            return False
        entry.connected = connected
        return True

    # --- Queries ----------------------------------------------------

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._by_id

    def __len__(self) -> int:
        return len(self._by_thread)

    def __iter__(self) -> Iterator[SessionEntry]:
        """A snapshot, so a caller may remove sessions while iterating."""
        return iter(tuple(self._by_thread.values()))

    # --- Policy: what happens to the thread a client asks for --------

    def claim(
        self, thread_id: Optional[str], user_identifier: Optional[str] = None
    ) -> Claim:
        """Decide what a connecting client gets for the conversation it named.

        Two answers, and neither depends on why the socket opened. A reload,
        a second tab and a transport blip all say the same thing -- "I am in
        thread T" -- and the answer to all three is the session that is in
        thread T, if it is this user's. The client learns which socket won
        from the 4409 the loser gets, not from anything decided here.

        A thread held by somebody else is answered exactly as a thread that
        was never held: ``CREATED``, and a conversation of the arriving
        user's own. A refusal would confirm the thread exists, and the id in
        a URL is a bearer token in everything but name.
        """
        if thread_id is None:
            return Claim(ClaimOutcome.CREATED)
        entry = self._by_thread.get(thread_id)
        if entry is None:
            return Claim(ClaimOutcome.CREATED)
        if not is_owned_by(entry, user_identifier):
            return Claim(ClaimOutcome.CREATED, entry)
        return Claim(ClaimOutcome.KEPT, entry)

    # --- Policy: what a conversation protects -------------------------

    def has_live_task(self, thread_id: Optional[str]) -> bool:
        """Whether work is running in this conversation.

        Read by the resume-delete filter: work that is running is not a
        leftover, and a read that deleted its messages would have the task
        put its rows back as orphans and the two feeds disagree.

        Connectedness is not consulted -- work running behind a dropped
        socket is still work, and it will post its results.
        """
        entry = self.entry_of_thread(thread_id)
        return entry is not None and entry.session.has_live_task

    def protected_step_ids(self, thread_id: Optional[str]) -> frozenset[str]:
        """Step ids a live question of this conversation is displaying.

        Deleting the step from under a question that is still on screen
        leaves the user with nothing to answer.
        """
        entry = self.entry_of_thread(thread_id)
        if entry is None or not entry.session.has_live_ask:
            return frozenset()
        return frozenset(entry.session.live_ask_step_ids)
