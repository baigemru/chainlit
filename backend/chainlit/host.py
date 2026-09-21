"""What an application reaches the engine with when it is not in a session.

Everything else in the ``cl.*`` API is written for code the engine itself
launched: ``on_message``, a step, an action callback. They read the context
variable and that is how they know which conversation they are in. An
application also has code the engine never launched -- a route of its own, a
webhook a background service calls back on, a scheduled job -- and until this
module existed the only way in from there was to import the transport's
internals and assemble a step by hand, which is what the one consumer did:
five private imports, two hand-built copies of one message, and a comment
recording the ordering rule between a thread's owner and its first step.

So this is the seam, and it is deliberately narrow. Four entries, each of
which answers a question the application cannot answer for itself:

- ``deliver_to_thread`` -- where does a message go when the chat may or may
  not be open? That depends on the session registry, which is transport
  state.
- ``refresh_account_badge`` -- which sockets belong to this user? Same.
- ``persistence`` / ``uow`` -- the engine's own database session, so an
  application storing something of its own next to a thread does it in one
  unit of work with the engine's rows rather than through a second pool.

What is *not* here is a way to reach the runner itself. ``current_runner``
stays in ``chainlit.runner`` and is not exported: an application that holds
the runner holds the transport, and every method on it is a promise this
fork has not made.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, Mapping, Optional

from chainlit.runner import current_runner

if TYPE_CHECKING:
    from chainlit.persistence.config import Persistence, UnitOfWork

__all__ = [
    "deliver_to_thread",
    "persistence",
    "refresh_account_badge",
    "uow",
]


async def deliver_to_thread(
    thread_id: str,
    content: str,
    *,
    author: Optional[str] = None,
    id: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    user_identifier: Optional[str] = None,
) -> str:
    """Put a message in a conversation from outside any session.

    Whether the person is sitting in that chat right now is not the caller's
    question -- it is the registry's, and the answer changes between the
    moment a job starts and the moment it finishes. Pass the message and let
    the engine take whichever road exists:

    - a session is holding the thread (connected, or inside its disconnect
      grace): the message goes out on the socket, into the transcript the
      reconnect replays, and into that session's ordered writer;
    - nobody is: the row is written on its own, with the thread's owner
      patched in first so the conversation shows up in their history, and
      the thread's ``updatedAt`` bumped so it sorts as of now.

    ``id`` is the caller's handle on repetition. A webhook that may be
    delivered twice should mint the same id both times: the step is upserted,
    so the second delivery replaces the first instead of doubling it.
    ``metadata`` reaches the row and the client verbatim -- ``{"anchor":
    "none"}`` is how a result that arrives mid-conversation lands without
    dragging the view away from what the person is reading.

    Returns ``"live"`` or ``"stored"``, for a caller that logs which road it
    took. Raises ``RuntimeError`` when there is neither a session nor
    persistence, rather than answering "delivered" about a dropped message.

    Args:
        thread_id (str): The conversation to deliver into.
        content (str): The message text.
        author (Optional[str]): The name it is shown under; the app's own by default.
        id (Optional[str]): A stable step id, for a delivery that may repeat.
        metadata (Optional[Mapping[str, Any]]): Stored and sent as given.
        user_identifier (Optional[str]): The thread's owner, when the caller knows it.

    Returns:
        str: ``"live"`` if a session was told, ``"stored"`` if a row was written.
    """
    return await current_runner().deliver_to_thread(
        thread_id,
        content,
        author=author,
        id=id,
        metadata=metadata,
        user_identifier=user_identifier,
    )


async def refresh_account_badge(user: Any) -> None:
    """Recount the account badge for ``user`` and push it to every session.

    ``cl.context.emitter.refresh_account_badge()`` is the same thing from
    inside a session. This is the form for code that has a user and no
    session -- the job that granted the thing the badge is counting.

    Silent when the application declared no ``@cl.on_account_badge``: there
    is no number, so there is no frame. Raises whatever the hook raises.

    Args:
        user (Any): The user whose sessions should be told; only ``.identifier`` is read.
    """
    await current_runner().refresh_account_badge(user)


def persistence() -> Optional["Persistence"]:
    """The engine's database wiring, or ``None`` if it is running without one.

    For an application that needs the storage client or the SQLAlchemy config
    itself. For reading and writing rows, ``uow()`` is the shorter road.

    Returns:
        Optional[Persistence]: The wiring, or ``None``.
    """
    return current_runner().persistence


def uow() -> "AbstractAsyncContextManager[UnitOfWork]":
    """One database session with the engine's five services bound to it.

    ``async with cl.uow() as unit:`` -- ``unit.threads``, ``unit.steps``,
    ``unit.elements``, ``unit.users``, ``unit.feedbacks``. Commits on a clean
    exit, rolls back on an exception, and owns the connection: this is the
    form for code outside a request, which is all of this module's callers.

    An application's own tables go in an application's own session. What this
    is for is reading or amending the engine's rows -- a thread's metadata, a
    step somebody's job produced -- without a second pool and without a
    second copy of the schema.

    Raises ``RuntimeError`` when the application runs without persistence.

    Returns:
        AbstractAsyncContextManager[UnitOfWork]: The unit of work, unentered.
    """
    wiring = current_runner().persistence
    if wiring is None:
        raise RuntimeError(
            "This Chainlit application runs without persistence, so there is "
            "no unit of work to open."
        )
    return wiring.uow()
