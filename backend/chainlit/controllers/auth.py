"""The authentication HTTP surface, as a Litestar controller.

Everything here used to live in ``chainlit/server.py`` behind FastAPI's
``Depends``. Three things change shape rather than merely move:

* **The credential is checked by middleware, not by a dependency.**
  ``chainlit.security`` installs ``JWTCookieAuth`` app-wide, so a handler
  never asks "who is this" -- it reads ``request.user``. Every route below
  except ``/user`` was reachable without a credential on the old stack, so
  each one carries ``opt={"exclude_from_auth": True}``. On an excluded route
  the middleware never runs and ``connection.user`` *raises*
  (``litestar/connection/base.py:249``), which is why none of them touch it.

* **The auth cookie is written by the auth object, not by this module.**
  ``chainlit/auth/cookie.py`` mutated an injected ``Response`` and chunked
  long tokens across numbered cookies. Here ``JWTCookieAuth.login`` mints
  the token and builds the one cookie from its own ``key``/``path``/
  ``samesite``/``secure``/``domain`` -- the same fields its middleware reads
  -- so the writer and the reader cannot disagree. The routes only ever
  lift that cookie onto a redirect, or build its deletion from the same
  fields.

* **The user row is written through the persistence services**, not through
  ``BaseDataLayer``, and is committed by the before-send handler like any
  other write. That took a decision elsewhere: advanced_alchemy's
  ``autocommit`` handler commits only inside ``range(200, 300)`` and *rolls
  back* everything else, so with its default the 302 this callback answers
  with would have discarded the user row. ``persistence/config.py`` sets
  ``commit_on_redirect=True`` for exactly this reason.

``POST /auth/header`` is not ported. The one consumer has no
``header_auth_callback``, and the react-client only calls that route when
``/auth/config`` advertises ``headerAuth``, which this module reports as
``False``. ``POST /login`` *is* ported, both branches: it is not only the
password-auth route, it is also the direct-grant entry point -- the login
form posts to it (``frontend/src/pages/Login.tsx:75-80``) and the branch
below hands the resulting token to the same ``@cl.oauth_callback`` the
redirect flow uses.

The Azure AD hybrid callback (``POST .../azure-ad-hybrid/callback``,
``response_mode=form_post``) is not ported either: no deployment of this
fork uses Azure, and it was the only reason the cookie had to be chunked.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from secrets import compare_digest, token_urlsafe
from typing import Annotated, Any, Dict, Optional

from litestar import Controller, Request, Response, get, post
from litestar.datastructures import Cookie
from litestar.di import NamedDependency, Provide
from litestar.exceptions import (
    ClientException,
    ImproperlyConfiguredException,
    NotAuthorizedException,
    NotFoundException,
)
from litestar.params import (
    FromPath,
    FromQuery,
    JSONBody,
    MultipartBody,
    QueryParameter,
    SkipValidation,
)
from litestar.response import Redirect
from litestar.response.redirect import RedirectStatusType
from litestar.security.jwt import Token
from litestar.status_codes import HTTP_200_OK
from msgspec import Struct
from sqlalchemy.ext.asyncio import AsyncSession

from chainlit.config import config
from chainlit.logger import logger
from chainlit.oauth_providers import (
    get_configured_oauth_providers,
    get_direct_grant_provider,
    get_forgot_password_url,
    get_oauth_provider,
    get_oauth_provider_details,
)
from chainlit.persistence.services import UserService
from chainlit.security import ChainlitAuth, Identity

__all__ = (
    "PUBLIC",
    "STATE_COOKIE_NAME",
    "AuthController",
    "AuthenticatedUser",
    "cleared_auth_cookie",
    "provide_user_service",
    "security_provider",
    "state_cookie",
)

#: Opt key, not a handler parameter: ``exclude_opt_key`` defaults to
#: ``"exclude_from_auth"`` (``litestar/security/jwt/auth.py:619``).
PUBLIC = {"exclude_from_auth": True}

STATE_COOKIE_NAME = "oauth_state"
SESSION_COOKIE_NAME = "X-Chainlit-Session-id"

#: The provider is expected back within minutes; a state cookie that outlived
#: the round trip is a replay window and nothing else.
DEFAULT_STATE_COOKIE_LIFETIME = 3 * 60

LOCAL_HOSTS = ("127.0.0.1", "localhost")

#: The one error code the login page knows how to render.
OAUTH_SIGNIN_ERROR = "oauthSignin"


# --- request and response bodies --------------------------------------------


@dataclass
class PasswordLoginForm:
    """What the login page posts: ``FormData``, so ``multipart/form-data``.

    Litestar enforces the declared media type -- a urlencoded body against a
    ``MultipartBody`` handler is a 400 -- where FastAPI's
    ``OAuth2PasswordRequestForm`` accepted either. The frontend sends
    multipart, so multipart is what this declares.
    """

    username: str
    password: str


@dataclass
class SessionCookieRequest:
    session_id: str


class AuthenticatedUser(Struct, omit_defaults=True, kw_only=True, frozen=True):
    """What ``GET /user`` answers with -- the frontend's ``IUser``.

    ``id`` and ``createdAt`` are omitted rather than nulled when there is no
    data layer, which is what the old route did by returning a bare ``User``.
    """

    identifier: str
    display_name: Optional[str] = None
    metadata: Dict[str, Any] = {}
    id: Optional[str] = None
    createdAt: Optional[str] = None


# --- what the login page is told --------------------------------------------


def is_oauth_enabled() -> bool:
    return (
        bool(config.code.oauth_callback) and len(get_configured_oauth_providers()) > 0
    )


def is_direct_grant_enabled() -> bool:
    return bool(is_oauth_enabled() and get_direct_grant_provider())


def is_password_auth_enabled() -> bool:
    """Either an explicit password callback, or a provider doing direct grant."""
    return config.code.password_auth_callback is not None or is_direct_grant_enabled()


def require_login() -> bool:
    """Whether the app has any way to log a user in.

    ``header_auth_callback`` is deliberately absent from this list, unlike the
    ``chainlit.auth`` version: ``POST /auth/header`` is not ported, so a
    header callback cannot log anybody in on this stack and claiming
    otherwise would put the login page in a state with no way out of it.
    """
    return (
        bool(os.environ.get("CHAINLIT_CUSTOM_AUTH"))
        or is_password_auth_enabled()
        or is_oauth_enabled()
    )


def forgot_password_url() -> Optional[str]:
    return (
        os.environ.get("CHAINLIT_FORGOT_PASSWORD_URL")
        or config.ui.login_page_forgot_password_url
        or get_forgot_password_url()
    )


def auth_configuration() -> Dict[str, Any]:
    return {
        "requireLogin": require_login(),
        "passwordAuth": is_password_auth_enabled(),
        # Constant: the route that would serve it is not ported.
        "headerAuth": False,
        "oauthProviders": get_configured_oauth_providers()
        if is_oauth_enabled()
        else [],
        "oauthProviderDetails": (
            get_oauth_provider_details() if is_oauth_enabled() else []
        ),
        "default_theme": config.ui.default_theme,
        "ui": {
            "login_page_image": config.ui.login_page_image,
            "login_page_image_filter": config.ui.login_page_image_filter,
            "login_page_image_dark_filter": config.ui.login_page_image_dark_filter,
            "forgot_password_url": forgot_password_url(),
        },
    }


# --- cookies ----------------------------------------------------------------


def cleared_auth_cookie(security: ChainlitAuth) -> Cookie:
    """A ``Set-Cookie`` that deletes the auth cookie ``security`` writes.

    Every attribute comes from the auth instance because a deletion only
    takes if it matches the cookie it deletes: a different ``Path`` (or
    ``Domain``) leaves the old one in the jar alongside it. The old writer
    got exactly this wrong -- it wrote at ``/`` and deleted at
    ``CHAINLIT_AUTH_COOKIE_PATH``.
    """
    return Cookie(
        key=security.key,
        value="",
        path=security.path,
        max_age=0,
        httponly=True,
        secure=security.secure,
        samesite=security.samesite,
        domain=security.domain,
    )


def state_cookie_lifetime() -> int:
    raw = os.environ.get("CHAINLIT_STATE_COOKIE_LIFETIME")
    return int(raw) if raw else DEFAULT_STATE_COOKIE_LIFETIME


def _state_cookie(value: str, max_age: int) -> Cookie:
    """The CSRF state, with fixed attributes rather than the auth cookie's.

    It is not a session: it lives for one redirect round trip and is read
    once, by ``oauth_callback``, on the top-level GET the provider sends the
    browser back on. ``SameSite=Lax`` is sent on exactly that navigation, so
    it needs neither the ``None``/``Secure`` pair a cross-origin copilot
    deployment gives the auth cookie nor that cookie's ``Path``. Tying it to
    the auth instance would only make the entry routes -- which do not
    otherwise need one -- depend on it.
    """
    return Cookie(
        key=STATE_COOKIE_NAME,
        value=value,
        path="/",
        max_age=max_age,
        httponly=True,
        samesite="lax",
    )


def state_cookie(state: str) -> Cookie:
    return _state_cookie(state, state_cookie_lifetime())


def cleared_state_cookie() -> Cookie:
    return _state_cookie("", 0)


def state_is_valid(request: Request[Any, Any, Any], state: Optional[str]) -> bool:
    """Compare the provider's ``state`` against the cookie this app wrote.

    Constant time, and a missing cookie is a failure rather than a
    comparison against ``None``. This is the whole of the CSRF defence on the
    callback: without it, an attacker who can make the browser follow a
    callback URL of their choosing logs the victim into the attacker's
    account.
    """
    held = request.cookies.get(STATE_COOKIE_NAME)
    if not held or not state:
        return False
    return compare_digest(held, state)


# --- urls -------------------------------------------------------------------


def root_path() -> str:
    root = os.environ.get("CHAINLIT_ROOT_PATH", "")
    return "" if root == "/" else root


def user_facing_url(request: Request[Any, Any, Any]) -> str:
    """The URL the browser used, as far as this deployment can tell.

    Behind a proxy the request URL is the internal one, and the provider was
    given the external one as ``redirect_uri`` -- they have to match on the
    token exchange, so ``CHAINLIT_URL`` wins when it is set.
    """
    path = request.url.path
    if chainlit_url := os.environ.get("CHAINLIT_URL"):
        base = chainlit_url.split("?", 1)[0].split("#", 1)[0].removesuffix("/")
        return base + path
    return str(request.url).split("?", 1)[0].split("#", 1)[0]


def login_page_redirect(error: str, status_code: RedirectStatusType = 302) -> Redirect:
    """Back to the login page, with the code it renders a message for.

    Relative, where the old route redirected to ``request.url_for("login")``
    -- an absolute URL built from the *internal* host, which is the wrong one
    for every deployment behind a proxy.
    """
    return Redirect(
        path=f"{root_path()}/login",
        query_params={"error": error},
        status_code=status_code,
        cookies=[cleared_state_cookie()],
    )


# --- dependencies -----------------------------------------------------------


def security_provider(auth: Optional[ChainlitAuth]) -> Provide:
    """The ``security`` dependency: the one auth instance, bound once.

    A closure over the instance the plugin was built with, so tokens are
    minted with its secret and cookies with its name. A host that passed
    ``ChainlitPlugin(auth=...)`` its own -- with its own secret, expiry or
    ``retrieve_user_handler`` -- gets that one here, or the cookie the login
    route writes is not the cookie the middleware accepts.
    """
    return Provide(lambda: auth, sync_to_thread=False)


async def provide_user_service(
    db_session: NamedDependency[Optional[AsyncSession]] = None,
) -> Optional[UserService]:
    """The user table, when there is one.

    Depends on ``db_session`` rather than on the ``users`` service the
    persistence plugin registers: that keeps this controller working with no
    data layer at all (the default Chainlit has always had), and it does not
    shadow the app-level ``users`` provider for anybody else. Constructing
    the service against a session someone else owns is the same thing
    ``Persistence.bind`` does.
    """
    return UserService(session=db_session) if db_session is not None else None


# --- persistence ------------------------------------------------------------


async def _save_user(
    user_service: UserService, identifier: str, metadata: Optional[Dict[str, Any]]
) -> Any:
    return await user_service.save(identifier=identifier, metadata=metadata or {})


async def _persist_login(
    user_service: Optional[UserService], identity: Identity
) -> Optional[Any]:
    """Write the user row, and never let that failure block the login.

    Identical to the old ``_authenticate_user``: persistence is best effort,
    an authenticated user with an unwritable row still gets a session.
    """
    if user_service is None:
        return None
    try:
        return await _save_user(user_service, identity.identifier, identity.metadata)
    except Exception:
        logger.exception("Error creating user")
        return None


def _identity(principal: Any) -> Identity:
    """One shape for what the callbacks return and what a bearer token says.

    ``@cl.password_auth_callback``/``@cl.oauth_callback`` answer with a
    ``chainlit.user.User`` (identifier at the top level); ``/auth/jwt``
    decodes a stock ``Token`` (identifier in ``sub``, the rest in
    ``extras``). Both are normalised here so the minting below has one
    caller shape and the token claims are written from one place.
    """
    if isinstance(principal, Token):
        return Identity(
            identifier=principal.sub,
            display_name=principal.extras.get("display_name"),
            metadata=principal.extras.get("metadata") or {},
        )
    return Identity(
        identifier=getattr(principal, "identifier", ""),
        display_name=getattr(principal, "display_name", None),
        metadata=getattr(principal, "metadata", None) or {},
    )


# --- the controller ---------------------------------------------------------


class AuthController(Controller):
    """Register this on the app to get Chainlit's authentication routes.

    Declares no dependencies on purpose. Both ``security`` and
    ``user_service`` are bound by ``ChainlitPlugin`` at the *application*
    layer, with ``setdefault``, so a host that wants its own keeps it.
    Declaring them here instead would make them unoverridable: Litestar
    resolves a dependency at the closest layer that declares it, and nothing
    is closer than the controller a handler lives on.
    """

    path = "/"
    tags = ["auth"]

    # --- config ---

    @get("/auth/config", opt=PUBLIC)
    async def auth_config(self) -> Dict[str, Any]:
        """What the login page needs to draw itself."""
        return auth_configuration()

    # --- issuing a session ---

    async def _authenticate(
        self,
        security: Optional[ChainlitAuth],
        user_service: Optional[UserService],
        principal: Any,
        *,
        redirect_to_callback: bool = False,
    ) -> Response[Any]:
        """Persist, mint, and hand the browser its cookie.

        ``JWTCookieAuth.login`` does the minting and builds the cookie from
        the instance's own fields -- the same ones its middleware reads.
        The redirect branch lifts that cookie onto a ``Redirect`` rather
        than building a second one, for the same reason.
        """
        if not principal:
            raise NotAuthorizedException(detail="credentialssignin")
        if security is None:
            raise ImproperlyConfiguredException(
                "Authentication is not configured: no JWT secret, so no session "
                "can be issued. Run `chainlit create-secret`."
            )

        identity = _identity(principal)
        await _persist_login(user_service, identity)

        issued = security.login(
            identifier=identity.identifier,
            token_extras={
                "display_name": identity.display_name,
                "metadata": identity.metadata,
            },
            response_body={"success": True},
            response_status_code=HTTP_200_OK,
        )
        if redirect_to_callback:
            return Redirect(
                path=f"{root_path()}/login/callback",
                query_params={"success": "True"},
                status_code=302,
                cookies=[*issued.cookies, cleared_state_cookie()],
            )
        return issued

    @post("/login", status_code=HTTP_200_OK, opt=PUBLIC)
    async def login(
        self,
        data: MultipartBody[PasswordLoginForm],
        security: SkipValidation[NamedDependency[Optional[ChainlitAuth]]] = None,
        user_service: SkipValidation[NamedDependency[Optional[UserService]]] = None,
    ) -> Response[Any]:
        """Password login, either against a callback or against the provider.

        The second branch is the direct grant: the credentials go to the
        OAuth provider's token endpoint and the resulting token goes through
        the *same* ``@cl.oauth_callback`` as the redirect flow, so an app
        that denies a login in that callback denies it here too. A refusal
        the provider raises (``accountnotsetup``, ``credentialssignin``) is
        deliberately not caught: the login page reads the ``detail`` off the
        error response to decide what to do next.
        """
        if config.code.password_auth_callback:
            user = await config.code.password_auth_callback(
                data.username, data.password
            )
        elif (provider := get_direct_grant_provider()) and config.code.oauth_callback:
            token = await provider.get_token_with_password(data.username, data.password)
            raw_user_data, default_user = await provider.get_user_info(token)
            user = await config.code.oauth_callback(
                provider.id, token, raw_user_data, default_user
            )
        else:
            raise ClientException(detail="No auth_callback defined")

        return await self._authenticate(security, user_service, user)

    @post("/auth/jwt", status_code=HTTP_200_OK, opt=PUBLIC)
    async def jwt_auth(
        self,
        request: Request[Any, Any, Any],
        security: SkipValidation[NamedDependency[Optional[ChainlitAuth]]] = None,
        user_service: SkipValidation[NamedDependency[Optional[UserService]]] = None,
    ) -> Response[Any]:
        """Exchange a bearer token this app could have minted for a cookie.

        Excluded from auth on purpose. The middleware also reads
        ``Authorization``, so leaving it enforced would work by accident for
        a well-formed token and answer 401 -- rather than this route's own
        error -- for every other case.
        """
        header = request.headers.get("Authorization")
        if not header:
            raise NotAuthorizedException(detail="Authorization header missing")

        scheme, _, encoded = header.partition(" ")
        if scheme.lower() != "bearer" or not encoded:
            raise NotAuthorizedException(
                detail="Invalid authentication scheme. Please use Bearer"
            )
        if security is None:
            raise ImproperlyConfiguredException(
                "Authentication is not configured: no JWT secret, so no token "
                "can be verified. Run `chainlit create-secret`."
            )

        token = security.token_cls.decode(
            encoded_token=encoded,
            secret=security.token_secret,
            algorithm=security.algorithm,
        )
        return await self._authenticate(security, user_service, token)

    @post("/logout", status_code=HTTP_200_OK, opt=PUBLIC)
    async def logout(
        self,
        request: Request[Any, Any, Any],
        security: SkipValidation[NamedDependency[Optional[ChainlitAuth]]] = None,
    ) -> Any:
        """Drop the cookie, then let the app have its say.

        Public: a browser holding an expired or malformed cookie is exactly
        the one that most needs to be able to clear it. With no auth
        configured there is no cookie to clear, and nothing is written.
        """
        response: Response[Any] = Response(
            content={"success": True},
            cookies=[cleared_auth_cookie(security)] if security is not None else [],
        )
        if config.code.on_logout:
            # A callback used as a notification hook returns nothing. FastAPI
            # merged the injected response's headers in regardless; here the
            # return value *is* the response, so a ``None`` would throw the
            # cookie deletion away and leave the browser logged in.
            result = await config.code.on_logout(request, response)
            if result is not None:
                return result
        return response

    # --- oauth ---

    @staticmethod
    def _provider_or_raise(provider_id: str) -> Any:
        if config.code.oauth_callback is None:
            raise ClientException(detail="No oauth_callback defined")
        if (provider := get_oauth_provider(provider_id)) is None:
            raise NotFoundException(detail=f"Provider {provider_id} not found")
        return provider

    @staticmethod
    def _authorize_redirect(
        provider: Any,
        authorize_url: str,
        redirect_uri: str,
        extra_params: Optional[Dict[str, str]] = None,
    ) -> Redirect:
        """Off to the provider, with a fresh state in a cookie.

        The state is only meaningful because the callback compares it to this
        cookie; the two belong together and are written in the same file for
        that reason.
        """
        # URL- and cookie-safe by construction. ``chainlit.secret``'s
        # alphabet includes punctuation that has to be percent-encoded in the
        # redirect and quoted in the ``Set-Cookie``, which is two encodings to
        # get right for no benefit -- the value is opaque either way.
        state = token_urlsafe(32)
        params = {
            "client_id": provider.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            **provider.authorize_params,
            **(extra_params or {}),
        }
        return Redirect(
            path=f"{authorize_url}?{urllib.parse.urlencode(params)}",
            status_code=302,
            cookies=[state_cookie(state)],
        )

    def _sibling_callback_uri(
        self, request: Request[Any, Any, Any], suffix: str
    ) -> str:
        """``/auth/oauth/x/<suffix>`` -> ``/auth/oauth/x/callback``.

        The alternate entry points differ only in which IdP the provider
        should jump straight to; they all come back to the one callback.
        """
        base = user_facing_url(request).rstrip("/").removesuffix(suffix).rstrip("/")
        return f"{base}/callback"

    @get("/auth/oauth/{provider_id:str}", opt=PUBLIC)
    async def oauth_login(
        self,
        request: Request[Any, Any, Any],
        provider_id: FromPath[str],
        login_hint: FromQuery[Optional[str]] = None,
    ) -> Redirect:
        """Redirect to the provider's own login page."""
        provider = self._provider_or_raise(provider_id)
        return self._authorize_redirect(
            provider,
            provider.authorize_url,
            f"{user_facing_url(request).rstrip('/')}/callback",
            extra_params={"login_hint": login_hint} if login_hint else None,
        )

    @get("/auth/oauth/{provider_id:str}/register", opt=PUBLIC)
    async def oauth_register(
        self, request: Request[Any, Any, Any], provider_id: FromPath[str]
    ) -> Redirect:
        """Redirect to the provider's registration page."""
        provider = self._provider_or_raise(provider_id)
        if (
            not provider.registration_url
            or not provider.is_registration_button_enabled()
        ):
            raise NotFoundException(
                detail=f"Registration is not enabled for provider {provider_id}"
            )
        return self._authorize_redirect(
            provider,
            provider.registration_url,
            self._sibling_callback_uri(request, "/register"),
        )

    @get("/auth/oauth/{provider_id:str}/vk", opt=PUBLIC)
    async def oauth_vk_login(
        self, request: Request[Any, Any, Any], provider_id: FromPath[str]
    ) -> Redirect:
        """Straight to VK, skipping the provider's own login page."""
        provider = self._provider_or_raise(provider_id)
        if not provider.is_vk_button_enabled():
            raise NotFoundException(
                detail=f"VK login is not enabled for provider {provider_id}"
            )
        return self._authorize_redirect(
            provider,
            provider.authorize_url,
            self._sibling_callback_uri(request, "/vk"),
            extra_params={"kc_idp_hint": provider.get_vk_idp_hint()},
        )

    @get("/auth/oauth/{provider_id:str}/yandex", opt=PUBLIC)
    async def oauth_yandex_login(
        self, request: Request[Any, Any, Any], provider_id: FromPath[str]
    ) -> Redirect:
        """Straight to Yandex, skipping the provider's own login page."""
        provider = self._provider_or_raise(provider_id)
        if not provider.is_yandex_button_enabled():
            raise NotFoundException(
                detail=f"Yandex login is not enabled for provider {provider_id}"
            )
        return self._authorize_redirect(
            provider,
            provider.authorize_url,
            self._sibling_callback_uri(request, "/yandex"),
            extra_params={"kc_idp_hint": provider.get_yandex_idp_hint()},
        )

    @get("/auth/oauth/{provider_id:str}/callback", opt=PUBLIC)
    async def oauth_callback(
        self,
        request: Request[Any, Any, Any],
        provider_id: FromPath[str],
        security: SkipValidation[NamedDependency[Optional[ChainlitAuth]]] = None,
        user_service: SkipValidation[NamedDependency[Optional[UserService]]] = None,
        error: FromQuery[Optional[str]] = None,
        code: FromQuery[Optional[str]] = None,
        # ``state`` is a reserved kwarg name in Litestar (it means the app
        # state), so the wire name is declared and the parameter renamed.
        oauth_state: Annotated[Optional[str], QueryParameter(name="state")] = None,
    ) -> Response[Any]:
        """Where every OAuth entry point comes back to."""
        if config.code.oauth_callback is None:
            raise ClientException(detail="No oauth_callback defined")
        if (provider := get_oauth_provider(provider_id)) is None:
            raise NotFoundException(detail=f"Provider {provider_id} not found")

        if error:
            logger.warning("OAuth provider %s returned error: %s", provider_id, error)
            return login_page_redirect(OAUTH_SIGNIN_ERROR)

        if not code or not state_is_valid(request, oauth_state):
            return login_page_redirect(OAUTH_SIGNIN_ERROR)

        try:
            token = await provider.get_token(code, user_facing_url(request))
            raw_user_data, default_user = await provider.get_user_info(token)
            user = await config.code.oauth_callback(
                provider_id, token, raw_user_data, default_user
            )
        except Exception:
            logger.exception("OAuth callback error")
            return login_page_redirect(OAUTH_SIGNIN_ERROR)

        if not user:
            # The app said no. Nothing is minted and nothing is written.
            return login_page_redirect(OAUTH_SIGNIN_ERROR)

        return await self._authenticate(
            security, user_service, user, redirect_to_callback=True
        )

    # --- who am i ---

    @get("/user")
    async def current_user(
        self,
        request: Request[Any, Any, Any],
        user_service: SkipValidation[NamedDependency[Optional[UserService]]] = None,
    ) -> Optional[AuthenticatedUser]:
        """The signed-in user, joined with what the data layer knows.

        Not excluded from auth, so the middleware has already rejected a
        request with no usable cookie. ``scope`` is read rather than
        ``request.user`` for the one case the middleware is not installed at
        all -- an app with no authentication -- where the property would
        raise and the old route answered ``null``.

        Reads, and only writes when the row is missing. An unconditional
        upsert here would push the metadata frozen into the token at login
        back over whatever has been written since.
        """
        identity = request.scope.get("user")
        if identity is None:
            return None

        identifier = identity.identifier
        display_name = identity.display_name
        record = None

        if user_service is not None:
            try:
                record = await user_service.get_by_identifier(identifier)
                if record is None:
                    record = await _save_user(
                        user_service, identifier, identity.metadata
                    )
            except Exception:
                logger.exception("Unable to read the user from the data layer")

        if record is None:
            return AuthenticatedUser(
                identifier=identifier,
                display_name=display_name,
                metadata=identity.metadata or {},
            )
        return AuthenticatedUser(
            id=record.id,
            identifier=record.identifier,
            # Ephemeral: the token carries it, the row does not.
            display_name=display_name,
            metadata=record.metadata or {},
            createdAt=record.created_at,
        )

    # --- session affinity ---

    @post("/set-session-cookie", status_code=HTTP_200_OK, opt=PUBLIC)
    async def set_session_cookie(
        self, request: Request[Any, Any, Any], data: JSONBody[SessionCookieRequest]
    ) -> Response[Any]:
        """Pin the websocket session id, for load balancers doing affinity.

        ``SameSite=None`` off localhost because the copilot embeds this app
        cross-origin, and that requires ``Secure``.
        """
        client = request.client
        is_local = bool(client and client.host in LOCAL_HOSTS)
        return Response(
            content={"message": "Session cookie set"},
            cookies=[
                Cookie(
                    key=SESSION_COOKIE_NAME,
                    value=data.session_id,
                    path="/",
                    httponly=True,
                    secure=not is_local,
                    samesite="lax" if is_local else "none",
                )
            ],
        )
