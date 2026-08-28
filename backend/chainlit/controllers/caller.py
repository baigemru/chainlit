"""Who is calling, read once, the same way, by every route.

``request.user`` is the right way to ask when the authentication middleware
is guaranteed to have run, and it is not guaranteed here: a deployment with
no ``CHAINLIT_AUTH_SECRET`` installs no middleware at all, and the property
then *raises* rather than returns ``None`` (``litestar/connection/base.py:
249``). The routes that answer to both worlds -- settings, the session-affine
uploads, ``/user`` -- read the scope instead, and they read it through this
module so that "nobody is logged in" is spelled one way.

The ownership rule for a live session lives here too, because two
controllers apply it and a rule applied in two places is two rules.
"""

from __future__ import annotations

from typing import Optional, cast

from litestar.exceptions import NotFoundException
from litestar.types import Empty

from chainlit.controllers.sessions import LiveSession
from chainlit.security import AuthedRequest, Identity

__all__ = ("assert_session_owner", "caller", "caller_identifier")


def caller(request: AuthedRequest) -> Optional[Identity]:
    """The authenticated identity, or ``None`` when there is none to have.

    ``None`` covers both the deployment that never installed the middleware
    and the route that opted out of it. The two are indistinguishable from
    the scope, and no route needs to tell them apart: each has decided what
    it does with an anonymous caller before it asks.
    """
    user = request.scope.get("user", Empty)
    if user is Empty or user is None:
        return None
    return cast(Identity, user)


def caller_identifier(request: AuthedRequest) -> Optional[str]:
    """The caller's identifier, or ``None`` without authentication."""
    identity = caller(request)
    return None if identity is None else getattr(identity, "identifier", None)


def assert_session_owner(session: LiveSession, request: AuthedRequest) -> None:
    """Refuse a live session that is not the caller's.

    ``404``, with the same detail an unknown id gets. A session id travels
    in query strings and JSON bodies, so it ends up in logs, referrers and
    pasted URLs; the one thing a stranger holding one must not learn from
    this route is that it is live. A ``401`` or ``403`` here would say so.

    With no authentication there is nobody to compare against and every
    caller is the owner -- the rule the deployment chose by not configuring
    a login.
    """
    identifier = caller_identifier(request)
    if identifier is None:
        return
    if getattr(session.user, "identifier", None) != identifier:
        raise NotFoundException("Session not found")
