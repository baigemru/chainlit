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
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Dict, Literal, Optional, cast

from litestar import Request
from litestar.connection import ASGIConnection
from litestar.datastructures import State
from litestar.exceptions import NotAuthorizedException
from litestar.middleware.authentication import AuthenticationResult
from litestar.security.jwt import (
    JWTCookieAuth,
    JWTCookieAuthenticationMiddleware,
    Token,
)

__all__ = (
    "AUTH_SECRET_ENV",
    "AuthedRequest",
    "ChainlitAuth",
    "Identity",
    "StaleTokenError",
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


class StaleTokenError(NotAuthorizedException):
    """A token arrived by cookie and did not validate.

    Every browser that used the pre-rebuild deployment still carries its old
    ``access_token``: signed by pyjwt without a ``sub`` claim, alive for
    weeks, refused here. Left in the jar it pins the user to the login page
    for as long as it lives, so this case gets its own exception — the
    handler the plugin registers answers it with the same 401 *plus* the
    ``Set-Cookie`` deletions that take the stale cookie (and any legacy
    chunks) out of the jar. A missing token stays a plain
    ``NotAuthorizedException``: there is nothing to clear.
    """


class CutoverAuthMiddleware(JWTCookieAuthenticationMiddleware):
    """The stock cookie middleware, with undecodable tokens told apart.

    Stock also means the stock carrier order: ``Authorization`` header
    first, cookie second. A proxy that injects a header value of its own
    breaks that order for every browser (seen 30.08.2026, a leftover
    ``header_up Authorization`` in the Caddyfile) — the fix belongs in the
    proxy, not here.
    """

    async def authenticate_token(
        self, encoded_token: str, connection: ASGIConnection[Any, Any, Any, Any]
    ) -> AuthenticationResult:
        try:
            return await super().authenticate_token(encoded_token, connection)
        except StaleTokenError:
            raise
        except NotAuthorizedException as error:
            # The cookie is the only carrier a browser has; an Authorization
            # header with a cookie alongside is nothing this app's clients
            # send, so a failed token plus a present cookie is a stale jar.
            if self.auth_cookie_key in connection.cookies:
                raise StaleTokenError(detail="Invalid token") from error
            raise


@dataclass
class ChainlitAuth(JWTCookieAuth[Identity, Token]):
    """``JWTCookieAuth`` with Chainlit's ``connection.user`` type pinned.

    Nothing is overridden; two defaults are filled in. ``retrieve_user_handler``
    defaults to :func:`identity_from_token` so ``connection.user`` has the
    shape the rest of the package reads (``identifier``, ``display_name``,
    ``metadata``), and ``key`` defaults to the cookie name Chainlit has
    always used. Build it with :func:`chainlit_auth` to read the
    deployment's settings, or construct it directly with whatever
    ``key``/``path``/``samesite`` the host wants — every cookie the auth
    routes write is derived from this instance, so the two cannot disagree.
    """

    retrieve_user_handler: Callable[
        [Token, ASGIConnection[Any, Any, Any, Any]], Any
    ] = identity_from_token
    key: str = "access_token"
    # The cutover middleware, so a stale legacy cookie raises
    # ``StaleTokenError`` and the plugin's handler can clear it.
    authentication_middleware_class: type[JWTCookieAuthenticationMiddleware] = (
        CutoverAuthMiddleware
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
