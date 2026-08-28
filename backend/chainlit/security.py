"""JWT authentication for the Litestar app.

The old stack hand-rolled this: a ``SecurityBase`` subclass that read the
cookie, a pair of FastAPI dependencies, and ``pyjwt`` calls in
``chainlit/auth/jwt.py``. Litestar ships the whole shape as
:class:`~litestar.security.jwt.JWTCookieAuth`, and — unlike a dependency —
it is middleware, so it populates ``connection.user`` in the **websocket**
scope too (``AbstractAuthenticationMiddleware.scopes`` defaults to
``{HTTP, WEBSOCKET}``). That matters here: the browser cannot put an
``Authorization`` header on an upgrade request, so the cookie is the only
carrier the socket has, and the socket is where this fork spends its time.

What is preserved from the old stack, because live browsers hold these
cookies:

* the cookie name (``CHAINLIT_AUTH_COOKIE_NAME``, default ``access_token``),
  its ``SameSite``/``Secure``/``Path`` settings, and the chunking scheme
  (``access_token_0``, ``access_token_1``, ...) used for tokens over 3000
  characters;
* the payload claims ``identifier``, ``display_name`` and ``metadata``, at
  the top level of the JWT rather than nested under ``extras``.

What is added: ``sub``, which :class:`~litestar.security.jwt.Token` requires
and the old token did not carry. Tokens minted before the switchover are
still accepted — :meth:`ChainlitToken.decode_payload` backfills ``sub`` from
``identifier``. Tokens minted here are *not* readable by the old stack's
``decode_jwt`` (``User(**dict)`` chokes on ``sub``/``extras``), so the
cutover is per-deployment atomic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, Literal, Optional, cast

from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.middleware.authentication import AuthenticationResult
from litestar.security.jwt import (
    JWTCookieAuth,
    JWTCookieAuthenticationMiddleware,
    Token,
)

__all__ = (
    "AUTH_SECRET_ENV",
    "ChainlitAuth",
    "ChainlitToken",
    "CookieSettings",
    "Identity",
    "cookie_settings",
    "get_auth_secret",
    "token_from_cookies",
)

AUTH_SECRET_ENV = "CHAINLIT_AUTH_SECRET"

# The old cookie writer split anything longer than this across numbered
# cookies; the reader here has to know the same number to be able to put one
# back together.
COOKIE_CHUNK_SIZE = 3000

SameSite = Literal["lax", "strict", "none"]


def get_auth_secret() -> Optional[str]:
    """The HS256 secret, or ``None`` when the deployment has not set one."""
    return os.environ.get(AUTH_SECRET_ENV)


@dataclass(frozen=True)
class CookieSettings:
    """How the auth cookie is written, read at call time rather than import.

    The old module froze these at import, which made them untestable and made
    the value depend on which module got imported first.
    """

    name: str = "access_token"
    path: str = "/"
    samesite: SameSite = "lax"
    secure: bool = False


def cookie_settings() -> CookieSettings:
    """Read the cookie settings out of the environment.

    ``CHAINLIT_ROOT_PATH`` is deliberately not consulted. The old code read
    ``os.environ.get(root_path, "/")`` — an environment lookup *keyed by the
    root path*, which is a typo for ``root_path`` and yields ``"/"`` for
    every deployment that does not happen to have an environment variable
    named after its own mount point. Reproducing the typo is not
    compatibility; honouring the intent would change the cookie ``Path`` for
    deployments whose browsers hold a cookie written at ``/``. Set
    ``CHAINLIT_AUTH_COOKIE_PATH`` to say it explicitly.
    """
    samesite = os.environ.get("CHAINLIT_COOKIE_SAMESITE", "lax")
    if samesite not in ("lax", "strict", "none"):
        raise ValueError(
            "Invalid value for CHAINLIT_COOKIE_SAMESITE. "
            "Must be one of 'lax', 'strict' or 'none'."
        )
    return CookieSettings(
        name=os.environ.get("CHAINLIT_AUTH_COOKIE_NAME", "access_token"),
        path=os.environ.get("CHAINLIT_AUTH_COOKIE_PATH", "/"),
        samesite=cast(SameSite, samesite),
        # SameSite=None is only honoured on a Secure cookie.
        secure=samesite == "none",
    )


def token_from_cookies(cookies: Dict[str, str], name: str) -> Optional[str]:
    """Reassemble the token from the cookie jar, chunked or not."""
    if value := cookies.get(name):
        return value.split(" ")[-1]

    chunks = []
    index = 0
    while (chunk := cookies.get(f"{name}_{index}")) is not None:
        chunks.append(chunk)
        index += 1
    joined = "".join(chunks)
    return joined.split(" ")[-1] if joined else None


@dataclass(frozen=True)
class Identity:
    """Who the connection belongs to, as far as the token can say.

    This is what ``connection.user`` holds. It is deliberately not
    ``chainlit.user.User``: that type is still pydantic/dataclasses_json and
    is being ported. When it lands, swap it in behind
    ``ChainlitAuth.retrieve_user_handler`` — the data layer lookup that turns
    an identity into a ``PersistedUser`` belongs in that handler and nowhere
    else.
    """

    identifier: str
    display_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChainlitToken(Token):
    """The old wire payload, expressed as a Litestar token.

    ``identifier``/``display_name``/``metadata`` are real fields rather than
    ``extras`` entries so that :meth:`Token.encode` — which serialises with
    ``dataclasses.asdict`` — keeps writing them at the top level of the
    payload, where every browser cookie already in the wild has them.
    """

    identifier: str = ""
    display_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # `sub` is what Litestar requires and `identifier` is what Chainlit
        # has always written. They are the same value; whichever the caller
        # supplied fills in the other.
        if not self.identifier:
            self.identifier = self.sub
        elif not self.sub:
            self.sub = self.identifier
        super().__post_init__()

    @classmethod
    def decode_payload(
        cls,
        encoded_token: str,
        secret: str,
        algorithms: list[str],
        issuer: Any = None,
        audience: Any = None,
        options: Any = None,
    ) -> Any:
        """Decode, then backfill ``sub`` for tokens minted before the switch.

        ``Token.decode`` requires a ``sub`` claim and raises
        ``NotAuthorizedException`` without one. Every cookie issued by the
        FastAPI stack lacks it. Backfilling here rather than overriding
        ``decode`` keeps signature verification, expiry and the ``extras``
        gathering exactly as Litestar wrote them.
        """
        payload = super().decode_payload(
            encoded_token=encoded_token,
            secret=secret,
            algorithms=algorithms,
            issuer=issuer,
            audience=audience,
            options=options,
        )
        if isinstance(payload, dict) and not payload.get("sub"):
            if not (identifier := payload.get("identifier")):
                raise NotAuthorizedException("Invalid token")
            payload["sub"] = identifier
        return payload


class ChainlitCookieAuthMiddleware(JWTCookieAuthenticationMiddleware):
    """Cookie JWT middleware that can read a chunked cookie.

    Litestar's own reads a single cookie. Chainlit splits tokens longer than
    3000 characters across ``<name>_0``, ``<name>_1``, ... — which is not an
    edge case: it exists because OAuth providers hand back enough metadata to
    blow past the 4KB per-cookie limit. Without this, exactly those users get
    a 401 on the new stack.
    """

    async def authenticate_request(
        self, connection: ASGIConnection[Any, Any, Any, Any]
    ) -> AuthenticationResult:
        encoded_token = (
            connection.headers.get(self.auth_header, "").partition(" ")[-1]
            or token_from_cookies(dict(connection.cookies), self.auth_cookie_key)
            or ""
        )
        if not encoded_token:
            raise NotAuthorizedException(
                "No JWT token found in request header or cookies"
            )
        return await self.authenticate_token(
            encoded_token=encoded_token, connection=connection
        )


async def identity_from_token(
    token: Token, connection: ASGIConnection[Any, Any, Any, Any]
) -> Identity:
    """Default ``retrieve_user_handler``: trust the signed token, nothing more.

    No data layer lookup. The token is signed with the deployment's secret,
    so it is a sufficient answer to "who is this"; turning that into a
    ``PersistedUser`` is a persistence concern and is wired in by whoever
    passes their own handler.
    """
    identifier = getattr(token, "identifier", "") or token.sub
    return Identity(
        identifier=identifier,
        display_name=getattr(token, "display_name", None),
        metadata=getattr(token, "metadata", None) or {},
    )


@dataclass
class ChainlitAuth(JWTCookieAuth[Identity, ChainlitToken]):
    """``JWTCookieAuth`` carrying Chainlit's cookie and payload shape.

    Build it with :func:`chainlit_auth`; the dataclass is public so a host
    app can subclass or replace ``retrieve_user_handler``.
    """

    def mint(
        self,
        identifier: str,
        display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        expires_in: Optional[timedelta] = None,
    ) -> str:
        """Encode a token for ``identifier``.

        ``JWTCookieAuth.login`` cannot carry ``display_name``/``metadata`` —
        it does not forward ``**kwargs`` to ``create_token`` — so the login
        route mints here and sets the cookie itself.
        """
        return self.create_token(
            identifier=identifier,
            token_expiration=expires_in,
            display_name=display_name,
            metadata=metadata or {},
        )


def chainlit_auth(
    token_secret: Optional[str] = None,
    *,
    default_token_expiration: Optional[timedelta] = None,
    exclude: Optional[list[str]] = None,
    retrieve_user_handler: Any = identity_from_token,
) -> ChainlitAuth:
    """Build the auth config from the environment.

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
    cookie = cookie_settings()
    return ChainlitAuth(
        token_secret=secret,
        token_cls=ChainlitToken,
        retrieve_user_handler=retrieve_user_handler,
        authentication_middleware_class=ChainlitCookieAuthMiddleware,
        key=cookie.name,
        path=cookie.path,
        samesite=cookie.samesite,
        secure=cookie.secure or None,
        exclude=exclude,
        default_token_expiration=default_token_expiration or timedelta(days=1),
    )
