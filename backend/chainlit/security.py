"""JWT authentication for the Litestar app.

The old stack hand-rolled this: a ``SecurityBase`` subclass that read the
cookie, a pair of FastAPI dependencies, ``pyjwt`` calls in
``chainlit/auth/jwt.py``, and a cookie writer that split tokens across
numbered cookies. Litestar ships the whole shape as
:class:`~litestar.security.jwt.JWTCookieAuth`, and — unlike a dependency —
it is middleware, so it populates ``connection.user`` in the **websocket**
scope too (``AbstractAuthenticationMiddleware.scopes`` defaults to
``{HTTP, WEBSOCKET}``). That matters here: the browser cannot put an
``Authorization`` header on an upgrade request, so the cookie is the only
carrier the socket has, and the socket is where this fork spends its time.

Nothing of the old wire format is preserved. The token is a stock
:class:`~litestar.security.jwt.Token`: ``sub`` is the identifier, and
``display_name``/``metadata`` ride in ``extras``. The cookie is one cookie.
The chunking scheme existed for one provider's oversized tokens, and that
provider is not configured anywhere this fork runs; every cookie minted by
the old stack lacks ``sub`` and is refused, which costs each browser exactly
one login at cutover.

One thing *is* overridden, and it is about the websocket. Middleware runs
before the handler's ``accept()``, and a refusal before an accept is not a
close code: per the ASGI spec uvicorn turns a pre-accept ``websocket.close``
into an HTTP 403 rejection, and the browser's WebSocket API never exposes an
HTTP status. The tab sees a bare 1006 with ``opened=false`` -- the same
thing an unreachable server looks like -- so it backs off and reconnects,
forever. A night of that is thousands of ``"WebSocket /ws" 403`` pairs from
tabs whose cookie expired while the page stayed open. So
:class:`WebSocketAwareJWTCookieMiddleware` accepts the upgrade first and
*then* closes it with 4401, which the client already treats as terminal:
one handshake bought, a retry loop ended, and the user sent to the login
page instead of a spinner. HTTP is untouched -- there a 401 is already
something the caller can read.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Dict, Literal, Optional, cast

from litestar import Request, WebSocket
from litestar.connection import ASGIConnection
from litestar.datastructures import State
from litestar.enums import ScopeType
from litestar.exceptions import NotAuthorizedException
from litestar.middleware._utils import should_bypass_middleware
from litestar.security.jwt import JWTCookieAuth, Token
from litestar.security.jwt.middleware import JWTCookieAuthenticationMiddleware
from litestar.types import Receive, Scope, Send

from chainlit.protocol.codec import CloseCode, ErrorCode
from chainlit.ws.outbound import FORCE_CLOSE_GRACE

__all__ = (
    "AUTH_SECRET_ENV",
    "AuthedRequest",
    "ChainlitAuth",
    "Identity",
    "WebSocketAwareJWTCookieMiddleware",
    "chainlit_auth",
    "get_auth_secret",
    "identity_from_token",
)

AUTH_SECRET_ENV = "CHAINLIT_AUTH_SECRET"

SameSite = Literal["lax", "strict", "none"]


def get_auth_secret() -> Optional[str]:
    """The HS256 secret, or ``None`` when the deployment has not set one."""
    return os.environ.get(AUTH_SECRET_ENV)


@dataclass(frozen=True)
class Identity:
    """Who the connection belongs to, as far as the token can say.

    This is what ``connection.user`` holds. It is deliberately not
    ``chainlit.user.User``: that type is still pydantic/dataclasses_json and
    is being ported. When it lands, swap it in behind
    ``retrieve_user_handler`` — the data layer lookup that turns an identity
    into a ``PersistedUser`` belongs in that handler and nowhere else.
    """

    identifier: str
    display_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


#: The request every route handler receives, typed for what the middleware
#: leaves on it: ``request.user`` is an :class:`Identity` and ``request.auth``
#: the decoded :class:`~litestar.security.jwt.Token`. Both are only *there*
#: when the middleware ran. On a route that opts out with
#: ``opt={"exclude_from_auth": True}``, and in a deployment with no
#: authentication at all, the properties raise ``ImproperlyConfiguredException``
#: rather than return ``None`` (``litestar/connection/base.py:249``) — so a
#: handler that may run in either world reads the scope through
#: ``chainlit.controllers.caller`` instead of touching the properties.
#: A ``type`` statement rather than ``TypeAlias``: Litestar unwraps
#: ``TypeAliasType`` when it parses a handler signature (``litestar/typing.py``,
#: ``is_type_alias_type``), so the handler still sees ``Request``.
type AuthedRequest = Request[Identity, Token, State]


async def identity_from_token(
    token: Token, connection: ASGIConnection[Any, Any, Any, Any]
) -> Identity:
    """Default ``retrieve_user_handler``: trust the signed token, nothing more.

    No data layer lookup. The token is signed with the deployment's secret,
    so it is a sufficient answer to "who is this"; turning that into a
    ``PersistedUser`` is a persistence concern and is wired in by whoever
    passes their own handler.
    """
    return Identity(
        identifier=token.sub,
        display_name=token.extras.get("display_name"),
        metadata=token.extras.get("metadata") or {},
    )


class WebSocketAwareJWTCookieMiddleware(JWTCookieAuthenticationMiddleware):
    """Refuse a websocket with a close code rather than an HTTP status.

    The HTTP path is the base class, untouched: a 401 is an answer the
    caller can read, and ``useApi`` already logs the user out on one.

    On an upgrade there is no such answer. Litestar's exception middleware
    answers a ``NotAuthorizedException`` with ``websocket.close``, and
    because the handler has not accepted yet, uvicorn is obliged to turn
    that into an HTTP 403 rejection — which the browser's WebSocket API
    does not surface. So this accepts the upgrade itself and closes it
    with 4401, the code the client's ``TERMINAL_CLOSE_CODES`` already
    names. ``accept()`` on a socket in the ``init`` state consumes the
    ``websocket.connect`` event; nothing else on the scope is touched, and
    ``self.app`` is never called, so the route handler does not run and
    never sees a connection that was refused.

    Only ``NotAuthorizedException``. A misconfigured secret, a
    ``retrieve_user_handler`` that raises, anything else — all still
    propagate to the exception middleware and close 4500, because a
    server that is broken must not tell a client its credentials are bad:
    the client's answer to that is to log the user out.
    """

    __slots__ = ()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != ScopeType.WEBSOCKET or should_bypass_middleware(
            exclude_http_methods=self.exclude_http_methods,
            exclude_opt_key=self.exclude_opt_key,
            exclude_path_pattern=self.exclude,
            scope=scope,
            scopes=self.scopes,
        ):
            await super().__call__(scope, receive, send)
            return

        try:
            result = await self.authenticate_request(ASGIConnection(scope))
        except NotAuthorizedException:
            socket: WebSocket[Any, Any, Any] = WebSocket(scope, receive, send)
            await socket.accept()
            # The enum name, not the JWT library's message: a browser caps
            # ``reason`` at 123 bytes, and what went wrong with the token is
            # the server's business either way. Bounded like every other
            # goodbye in this fork: on the ``websockets`` implementation
            # ``close`` awaits the closing handshake, up to ten seconds
            # against a peer that has stopped answering.
            try:
                await asyncio.wait_for(
                    socket.close(
                        code=CloseCode.UNAUTHENTICATED,
                        reason=ErrorCode.UNAUTHENTICATED,
                    ),
                    FORCE_CLOSE_GRACE,
                )
            except TimeoutError:
                pass
            return

        scope["user"] = result.user
        scope["auth"] = result.auth
        await self.app(scope, receive, send)


@dataclass
class ChainlitAuth(JWTCookieAuth[Identity, Token]):
    """``JWTCookieAuth`` with Chainlit's ``connection.user`` type pinned.

    Two defaults are filled in and one class is swapped.
    ``retrieve_user_handler`` defaults to :func:`identity_from_token` so
    ``connection.user`` has the shape the rest of the package reads
    (``identifier``, ``display_name``, ``metadata``), and ``key`` defaults
    to the cookie name Chainlit has always used. The middleware is
    :class:`WebSocketAwareJWTCookieMiddleware`, which answers a refused
    upgrade with close 4401 instead of an HTTP 403 no browser can read.
    Build it with :func:`chainlit_auth` to read the deployment's settings,
    or construct it directly with whatever ``key``/``path``/``samesite``
    the host wants — every cookie the auth routes write is derived from
    this instance, so the two cannot disagree.
    """

    retrieve_user_handler: Callable[
        [Token, ASGIConnection[Any, Any, Any, Any]], Any
    ] = identity_from_token
    key: str = "access_token"
    authentication_middleware_class: type[JWTCookieAuthenticationMiddleware] = field(
        default=WebSocketAwareJWTCookieMiddleware
    )


def chainlit_auth(
    token_secret: Optional[str] = None,
    *,
    default_token_expiration: Optional[timedelta] = None,
    exclude: Optional[list[str]] = None,
    retrieve_user_handler: Any = identity_from_token,
) -> ChainlitAuth:
    """Build the auth config from the environment.

    The cookie settings are read here, at call time, and nowhere else: the
    old module froze them at import, which made them untestable, and a
    second reader in the routes would have let ``ChainlitPlugin(auth=
    ChainlitAuth(key="foo"))`` write ``foo`` while the middleware read
    ``access_token``.

    ``CHAINLIT_ROOT_PATH`` is deliberately not consulted for the cookie
    path. The old code read ``os.environ.get(root_path, "/")`` — an
    environment lookup *keyed by the root path*, a typo that yields ``"/"``
    for every real deployment. Set ``CHAINLIT_AUTH_COOKIE_PATH`` to say it
    explicitly.

    ``exclude`` takes regex patterns; a route can also opt out one at a time
    with ``opt={"exclude_from_auth": True}`` — an *opt key*, not a handler
    parameter. On an excluded path the middleware never runs, so
    ``connection.user`` **raises** ``ImproperlyConfiguredException`` rather
    than returning ``None``: a public handler must not touch it.
    """
    secret = token_secret if token_secret is not None else get_auth_secret()
    if not secret:
        raise ValueError(
            "You must provide a JWT secret in the environment to use "
            "authentication. Run `chainlit create-secret` to generate one."
        )
    samesite = os.environ.get("CHAINLIT_COOKIE_SAMESITE", "lax")
    if samesite not in ("lax", "strict", "none"):
        raise ValueError(
            "Invalid value for CHAINLIT_COOKIE_SAMESITE. "
            "Must be one of 'lax', 'strict' or 'none'."
        )
    return ChainlitAuth(
        token_secret=secret,
        retrieve_user_handler=retrieve_user_handler,
        key=os.environ.get("CHAINLIT_AUTH_COOKIE_NAME", "access_token"),
        path=os.environ.get("CHAINLIT_AUTH_COOKIE_PATH", "/"),
        samesite=cast(SameSite, samesite),
        # SameSite=None is only honoured on a Secure cookie.
        secure=True if samesite == "none" else None,
        exclude=exclude,
        default_token_expiration=default_token_expiration or timedelta(days=1),
    )
