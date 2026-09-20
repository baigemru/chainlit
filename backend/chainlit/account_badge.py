"""How many things on the account page the user has not seen yet.

One number, pushed. There is no ``GET /project/account/badge`` and nothing
on the client counts for itself: a badge that is polled is wrong for however
long the interval is, and a badge the client zeroes "visually" is a second
opinion about a fact only the application holds.

So there is one function here, and the three places that know the number may
have changed call it: the handshake (every hello, because a browser that has
been closed for a day arrives knowing nothing), the account route (the app
marks things seen inside ``on_account_load``, so the count it would report a
moment later is a different one), and the emitter (a run in the chat put
something on the page). Each recomputes through the hook and pushes to every
live session of that user -- the page and the chat are usually two tabs, and
only one of them did the thing that changed the count.

The hook is *handed* the account values, like ``on_account_load`` is, and for
the same reason: the route's mark-as-seen is a write of the request's own
session, which commits after the response is built, so a hook that counted by
reading ``users.account`` through a session of its own was always one page
load behind. Every call site passes the values as the **store** holds them --
the route passes ``strip_readonly`` of what it rendered, the other two read
the row -- so the hook sees one shape whichever of the three asked. A derived
number is the hook's to compute from those values; it is not carried in them,
because nothing stores a ``readOnly`` leaf.

No hook, no frame, ever. A client that never receives one renders nothing,
which is the correct display for an application that does not have the
concept.
"""

from __future__ import annotations

from typing import Any

import chainlit.config
from chainlit.account import call_hook
from chainlit.controllers.sessions import UserSessions
from chainlit.protocol.server import AccountBadge

__all__ = ("push_account_badge",)


async def push_account_badge(registry: UserSessions, user: Any, account: Any) -> None:
    """Ask the application for the count and tell every session of the user.

    ``account`` is the caller's job to produce -- the route already holds it,
    the other two read it through the runner's persistence -- because this
    module has no session of its own and inventing one here would be a second
    unit of work beside the request's. It is ``None`` only when the
    application declared no ``@cl.account``.

    Raises whatever the hook raises: two of the three callers are answering
    a request the application made, and a count that could not be computed
    is a 500 there rather than a silently missing badge. The handshake is
    the exception and says so at its call site.
    """
    hook = chainlit.config.config.code.on_account_badge
    if hook is None:
        return

    count = await call_hook(hook, user, account)
    # ``bool`` is an ``int`` in Python and a Struct constructor validates
    # nothing, so a hook answering ``True`` would put ``{"count": 1}`` on
    # the wire and the badge would read "1 unread" forever.
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError(
            f"@cl.on_account_badge must return an int, got {count!r}. The "
            "number is shown as it is; the client never invents one."
        )

    identifier = getattr(user, "identifier", None) if user is not None else None
    for session in registry.sessions_of(identifier):
        session.send(AccountBadge(count=count))
