"""``ChainlitPlugin`` — Chainlit as something a host application registers.

The old way to embed Chainlit was ``mount_chainlit(app, target)``: it built a
second FastAPI application, mounted it as a sub-app, and left the host with
two middleware stacks, two exception hierarchies and a documented ordering
problem. It is being deleted, and it has no successor factory either — this
plugin is the whole integration surface::

    app = Litestar(
        route_handlers=[...the host's own routes...],
        plugins=[ChainlitPlugin(config)],
    )

There is one application. Everything Chainlit needs — its routes, its
dependencies, its auth middleware, the SPA fallback, the static assets, its
startup bootstrap and the persistence plugin — is contributed by
``on_app_init`` into the host's own ``AppConfig``. Registering the
advanced_alchemy plugin from in here, rather than telling the host to list it
too, is the point: an ordering mistake between the two is then unreachable
rather than merely documented.

The same reasoning is why there is no ``create_app`` factory. A second name
for one wiring path is a second thing to keep in step with the first, and the
embedded path is the one every deployment actually uses.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Callable,
    Optional,
    Sequence,
    Union,
)

from litestar import Litestar, Request, Response, Router
from litestar.config.app import AppConfig
from litestar.di import Provide
from litestar.enums import MediaType
from litestar.exceptions import NotFoundException, ServiceUnavailableException
from litestar.exceptions.responses import create_exception_response
from litestar.plugins import InitPlugin
from litestar.response import File
from litestar.static_files import create_static_files_router
from litestar.stores.registry import StoreRegistry
from litestar.types import Empty, EmptyType

import chainlit.config
from chainlit.config import APP_ROOT, FILES_DIRECTORY, ChainlitConfig, CodeSettings
from chainlit.controllers import FRONTEND_DIST
from chainlit.controllers.auth import (
    AuthController,
    provide_user_service,
    security_provider,
)
from chainlit.controllers.files import FilesController
from chainlit.controllers.project import ProjectController
from chainlit.runner import (
    DEFAULT_SESSION_TIMEOUT,
    ApplicationRunner,
    ThreadStoreAdapter,
)
from chainlit.security import ChainlitAuth, chainlit_auth, get_auth_secret
from chainlit.transit_store import (
    SWEEP_INTERVAL_SECONDS,
    TRANSIT_STORE_NAME,
    TransitStore,
    transit_sweeper,
)
from chainlit.ws.connection import make_websocket_handler
from chainlit.ws.registry import SessionRegistry

if TYPE_CHECKING:
    from chainlit.persistence.config import Persistence

__all__ = (
    "DEFAULT_MAX_UPLOAD_MB",
    "ChainlitPlugin",
    "frontend_dist",
    "make_spa_fallback",
    "max_request_body_size",
)

# The built React app, copied here by ``pnpm build`` / the wheel build.

# The one file the SPA fallback serves.
INDEX_HTML = "index.html"

# The value the shipped ``config.toml`` template writes under
# ``[features.spontaneous_file_upload]``. The settings type defaults
# ``max_size_mb`` to ``None`` rather than to this, so an app whose config
# predates the key resolves to it here.
DEFAULT_MAX_UPLOAD_MB = 500

# A multipart upload is the declared file plus its part headers, the
# boundaries and any other fields in the same form. Litestar's limit is on the
# whole request body, so a limit set to exactly the file size would reject a
# file of exactly the allowed size.
MULTIPART_HEADROOM_BYTES = 1 << 20

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def frontend_dist() -> Path:
    """Where the built frontend lives inside the installed package."""
    return FRONTEND_DIST


def max_request_body_size(config: Optional[ChainlitConfig] = None) -> int:
    """The request body limit Chainlit's own routes run under, in bytes.

    Litestar caps request bodies at 10MB by default
    (``litestar/app.py:208``); FastAPI had no equivalent, so leaving the
    default in place would turn every upload over 10MB into a silent 413 on a
    stack where it used to work. The number comes from the one place the
    project already states an upload size —
    ``[features.spontaneous_file_upload] max_size_mb`` — so raising the
    documented limit raises the enforced one.

    A host application that wants the same limit on *its* routes has to pass
    this to ``Litestar(request_max_body_size=...)`` itself: ``Litestar``
    forwards its own constructor argument to the router layer and never reads
    ``AppConfig.request_max_body_size`` (``litestar/app.py:374`` vs
    ``:479``), so no plugin can raise the app-wide limit.
    """
    features = config.features if config else None
    upload = features.spontaneous_file_upload if features else None
    max_size_mb = (upload.max_size_mb if upload else None) or DEFAULT_MAX_UPLOAD_MB
    return max_size_mb * 1024 * 1024 + MULTIPART_HEADROOM_BYTES


def _wants_html(request: Request[Any, Any, Any]) -> bool:
    """Whether this looks like a browser navigating, not a client calling.

    A browser navigation always announces ``text/html`` in ``Accept``; a
    ``fetch``/XHR announces ``application/json`` or the ``*/*`` that httpx and
    the frontend's own client send. That single header is the whole rule — no
    list of API path prefixes, which would be a second thing to keep in sync
    and which gets the SPA's own routes wrong (``/login`` is both a POST
    endpoint and a page).
    """
    if request.method not in ("GET", "HEAD"):
        return False
    return "text/html" in request.headers.get("accept", "")


def _not_found(request: Request[Any, Any, Any], exc: Exception) -> Response[Any]:
    """Litestar's own 404 body, asked for explicitly."""
    return create_exception_response(request=request, exc=exc)


def make_spa_fallback(
    dist: Path,
) -> Callable[[Request[Any, Any, Any], Exception], Response[Any]]:
    """Build the ``NotFoundException`` handler that serves the SPA.

    A client-side route (``/thread/<id>``, ``/element/<id>``) matches no
    handler on the server, so it arrives here. ``html_mode=True`` on a static
    files router is *not* this: it serves ``index.html`` only when the path
    resolves to a *directory*, and otherwise looks for a ``404.html``
    (``litestar/static_files/base.py:111-142``). An exception handler on
    ``NotFoundException`` is the working recipe.

    The other half matters as much: a genuine 404 from an API route must stay
    a 404 with a JSON body. A fallback that answers every miss with the SPA
    turns every client bug into a silent 200 of HTML.
    """
    index = dist / INDEX_HTML

    def spa_fallback(request: Request[Any, Any, Any], exc: Exception) -> Response[Any]:
        if _wants_html(request) and index.is_file():
            return File(
                path=index,
                media_type=MediaType.HTML,
                content_disposition_type="inline",
                # The document that bootstraps the app must not be a 404: the
                # browser is at a real client-side route.
                status_code=200,
            )
        return _not_found(request, exc)

    return spa_fallback


class ChainlitPlugin(InitPlugin):
    """Contribute Chainlit to a host application's ``AppConfig``.

    Args:
        config: The ``ChainlitConfig`` the app is running (the
            ``chainlit.config`` module global, or one the host built). Used to
            derive defaults and to run the startup checks; every derived value
            can be passed explicitly instead, and everything works with
            ``config=None``.
        persistence: The database wiring. ``None`` runs Chainlit without a
            data layer, which is the default it has always had.
        auth: The JWT config. ``None`` runs with no authentication middleware
            at all — in which case ``connection.user`` *raises*, and handlers
            must not touch it. Left unset, authentication is on exactly when
            ``CHAINLIT_AUTH_SECRET`` is in the environment.
        frontend_dir: Override the built frontend location.
        public_dir: The app's own ``public/`` directory -- avatars, logos,
            custom element sources. Defaults to ``APP_ROOT/public``.
        transit: The profile-switch handover store.
        transit_sweep_interval: How often unclaimed transit records are
            reaped. Nothing else reaps them -- see ``chainlit.transit_store``.
        request_max_body_size: The limit for Chainlit's own routes. Defaults
            to :func:`max_request_body_size` of ``config``.
        configure_logging: Call ``logging.basicConfig``. Off by default: a
            library that reconfigures root logging behind its host's back is
            rude. The CLI turns it on.
    """

    __slots__ = (
        "_auth",
        "_config",
        "_configure_logging",
        "_frontend_dir",
        "_persistence",
        "_public_dir",
        "_request_max_body_size",
        "_runner",
        "_sessions",
        "_transit",
        "_transit_sweep_interval",
    )

    def __init__(
        self,
        config: Optional[ChainlitConfig] = None,
        *,
        persistence: Optional["Persistence"] = None,
        auth: Union[ChainlitAuth, EmptyType, None] = Empty,
        frontend_dir: Optional[Path] = None,
        public_dir: Optional[Path] = None,
        transit: Optional[TransitStore] = None,
        transit_sweep_interval: float = SWEEP_INTERVAL_SECONDS,
        request_max_body_size: Union[int, EmptyType, None] = Empty,
        configure_logging: bool = False,
    ) -> None:
        self._config = config
        self._persistence = persistence
        self._auth = self._resolve_auth(auth, config)
        self._frontend_dir = frontend_dir if frontend_dir is not None else FRONTEND_DIST
        self._public_dir = (
            public_dir if public_dir is not None else Path(APP_ROOT) / "public"
        )
        self._transit = transit if transit is not None else TransitStore()
        #: One registry per plugin instance, never a module global. A
        #: process-wide one would be the old ``ws_sessions_id`` again, and
        #: two applications in one interpreter -- which is what two tests
        #: are -- would see each other's sessions.
        self._sessions = SessionRegistry()
        self._runner = ApplicationRunner(
            config if config is not None else chainlit.config.config,
            registry=self._sessions,
            persistence=persistence,
            transit=self._transit,
            session_timeout=float(
                (config.project.session_timeout if config and config.project else None)
                or DEFAULT_SESSION_TIMEOUT
            ),
        )
        self._transit_sweep_interval = transit_sweep_interval
        self._request_max_body_size: Union[int, None] = (
            max_request_body_size(config)
            if request_max_body_size is Empty
            else request_max_body_size  # type: ignore[assignment]
        )
        self._configure_logging = configure_logging

    @staticmethod
    def _resolve_auth(
        auth: Union[ChainlitAuth, EmptyType, None], config: Optional[ChainlitConfig]
    ) -> Optional[ChainlitAuth]:
        """``Empty`` means "on when the deployment has a secret"; ``None``, off."""
        if auth is not Empty:
            return auth  # type: ignore[return-value]
        if not get_auth_secret():
            return None
        project = config.project if config else None
        timeout = project.user_session_timeout if project else None
        return chainlit_auth(
            default_token_expiration=timedelta(seconds=timeout) if timeout else None
        )

    @property
    def persistence(self) -> Optional["Persistence"]:
        return self._persistence

    @property
    def auth(self) -> Optional[ChainlitAuth]:
        return self._auth

    @property
    def transit(self) -> TransitStore:
        return self._transit

    @property
    def frontend_dir(self) -> Path:
        return self._frontend_dir

    @property
    def request_max_body_size(self) -> Union[int, None]:
        return self._request_max_body_size

    def route_handlers(self) -> Sequence[Any]:
        """Chainlit's own routes.

        Gathered under one router on purpose: that router is the layer that
        owns Chainlit's body-size limit and its 404 behaviour, so a host
        embedding Chainlit inherits neither app-wide.
        """
        handlers: list[Any] = [
            AuthController,
            ProjectController,
            FilesController,
            self._websocket(),
        ]
        # The app's own static files. Public by definition -- the login page
        # shows the logo, and custom element sources are fetched before any
        # user exists. The directory need not exist: the router resolves it
        # and answers 404, which is what an app with no ``public/`` wants.
        handlers.append(
            create_static_files_router(
                path="/public",
                directories=[self._public_dir],
                name="chainlit_public",
                html_mode=False,
                opt={"exclude_from_auth": True},
            )
        )
        assets = self._frontend_dir / "assets"
        if assets.is_dir():
            handlers.append(
                create_static_files_router(
                    path="/assets",
                    directories=[assets],
                    name="chainlit_assets",
                    # Not an SPA fallback: html_mode only helps directories.
                    html_mode=False,
                    # The bundle is public by construction -- it ships in the
                    # wheel, and the login page is written in it. Behind auth,
                    # a logged-out browser gets index.html and then a 401 for
                    # every script it references: a white page with no way in.
                    # ``exclude_from_auth`` is an opt key, not a parameter.
                    opt={"exclude_from_auth": True},
                )
            )
        return handlers

    def _bind_absent_services(self, app_config: AppConfig) -> None:
        """Answer 503 for the routes that need a database there isn't.

        Litestar resolves dependencies at registration, so a handler asking
        for ``threads`` with nothing bound is a startup failure -- which
        would mean an application with no persistence could not mount the
        routes that do not need any. ``/project/settings`` is one of those,
        and the frontend cannot draw itself without it.

        So the routes are always mounted and the missing halves say what is
        actually wrong. "Service unavailable" is the truth here; a 500 would
        blame the request, and refusing to start would take a whole
        application down over a feature it never asked for.
        """
        if self._persistence is not None:
            return

        # One provider per name, never one shared: Litestar refuses the same
        # ``Provide`` object under two keys, on the grounds that an override
        # has to name the key it overrides.
        def refuse(service: str) -> Provide:
            def unavailable() -> Any:
                raise ServiceUnavailableException(
                    f"This route needs {service}, and no data layer is configured."
                )

            return Provide(unavailable, sync_to_thread=False)

        for name in ("users", "threads", "steps", "elements", "feedbacks"):
            app_config.dependencies.setdefault(name, refuse(name))

    @property
    def runner(self) -> ApplicationRunner:
        return self._runner

    def _websocket(self) -> Any:
        """The one socket, at the path the client already speaks to.

        Everything the socket needs from the application comes from the
        runner: how to build a session, what arriving means, what to do when
        the socket goes. The handshake decides; the runner reacts.
        """
        runner = self._runner
        return make_websocket_handler(
            registry=self._sessions,
            make_session=runner.make_session,
            thread_store=(
                ThreadStoreAdapter(self._persistence)
                if self._persistence is not None
                else None
            ),
            on_arrival=runner.on_arrival,
            on_ready=runner.on_ready,
            on_disconnect=runner.on_disconnect,
        )

    def on_app_init(self, app_config: AppConfig) -> AppConfig:
        if self._persistence is not None:
            # Appending a plugin here is seen by the init loop: Litestar
            # iterates ``config.plugins`` lazily while calling ``on_app_init``
            # (``litestar/app.py:395-398``), which is how advanced_alchemy's
            # own ``SQLAlchemyPlugin`` installs its two sub-plugins.
            app_config.plugins.append(self._persistence.plugin())
            app_config.dependencies.update(self._persistence.dependencies())

        # The two session-affine routes ask for this by name, through a
        # protocol they declare themselves. Both are required dependencies,
        # so forgetting to bind one is a registration error rather than a
        # surprise at request time.
        # setdefault throughout: a host that has already bound one of these
        # names keeps its own. A plugin that overwrote them would be
        # deciding something about the application it was added to.
        app_config.dependencies.setdefault(
            "sessions",
            Provide(lambda: self._sessions, sync_to_thread=False, use_cache=True),
        )
        app_config.dependencies.setdefault(
            "persistence_enabled",
            Provide(lambda: self._persistence is not None, sync_to_thread=False),
        )
        app_config.dependencies.setdefault("security", security_provider(self._auth))
        app_config.dependencies.setdefault(
            "user_service", Provide(provide_user_service)
        )
        self._bind_absent_services(app_config)

        if self._auth is not None:
            # AbstractSecurityConfig.on_app_init inserts the authentication
            # middleware at position 0 and adds the OpenAPI security scheme.
            # The middleware's default scopes are {http, websocket}
            # (``litestar/middleware/authentication.py:66``), so this is also
            # what authenticates the websocket upgrade — the browser cannot
            # set an Authorization header on one, and the cookie it can set.
            app_config = self._auth.on_app_init(app_config)

        if handlers := self.route_handlers():
            app_config.route_handlers.append(
                Router(
                    path="/",
                    route_handlers=handlers,
                    request_max_body_size=self._request_max_body_size,
                    # Chainlit's own routes never fall back to the SPA. A miss
                    # under /assets is a stale bundle reference, and answering
                    # it with index.html hands the browser a syntax error
                    # instead of a 404.
                    exception_handlers={NotFoundException: _not_found},
                )
            )

        # setdefault, not assignment: a host that wants its own 404 page keeps
        # it, and re-registering the plugin cannot clobber it either.
        app_config.exception_handlers.setdefault(
            NotFoundException, make_spa_fallback(self._frontend_dir)
        )

        self._register_transit_store(app_config)
        app_config.lifespan.append(self._lifespan)

        return app_config

    def _register_transit_store(self, app_config: AppConfig) -> None:
        """Put the transit store in the app's registry under its own name.

        ``AppConfig.stores`` is any of three things depending on what the host
        passed to ``Litestar(stores=...)``, and none of them may be replaced:
        a host with its own stores must keep them.
        """
        store = self._transit.store
        if app_config.stores is None:
            app_config.stores = {TRANSIT_STORE_NAME: store}
        elif isinstance(app_config.stores, StoreRegistry):
            app_config.stores.register(TRANSIT_STORE_NAME, store, allow_override=True)
        else:
            app_config.stores[TRANSIT_STORE_NAME] = store

    @asynccontextmanager
    async def _lifespan(self, app: Litestar) -> AsyncIterator[None]:
        """Everything that used to happen only when the CLI was the entry point.

        The CLI ran these before handing the app to uvicorn, so an embedded
        host got none of them — which is why the one consumer had to redo
        ``logging.basicConfig`` by hand after finding its ``logger.info``
        calls going nowhere. They belong to the application, so they belong
        here, where both entry points reach them.

        Startup, not ``on_app_init``: an embedded host may import the module
        that registers ``@cl.on_message`` after it has built the plugin, and
        checking the callbacks before they exist would fail the honest case.
        """
        self.bootstrap()
        code = self._code()
        if startup := getattr(code, "on_app_startup", None):
            await startup()
        try:
            async with transit_sweeper(self._transit, self._transit_sweep_interval):
                yield
        finally:
            if shutdown := getattr(code, "on_app_shutdown", None):
                await shutdown()
            # The spool is per process: nothing in it outlives the server.
            shutil.rmtree(FILES_DIRECTORY, ignore_errors=True)

    def bootstrap(self) -> None:
        """Run the startup checks and side effects, in order of severity."""
        if self._configure_logging:
            logging.basicConfig(
                level=logging.INFO,
                stream=sys.stdout,
                format=LOG_FORMAT,
                datefmt=LOG_DATE_FORMAT,
            )
        self._assert_app()
        self._assert_auth_secret()
        self._init_markdown()

    def _code(self) -> Optional[CodeSettings]:
        return self._config.code if self._config else None

    def _assert_app(self) -> None:
        """An app with no entry point serves a chat window that does nothing."""
        code = self._code()
        if code is None:
            return
        # `on_audio_chunk` used to count as an entry point. It no longer
        # does: audio is retired, so an app whose only callback is that one
        # now serves a window that can never say anything.
        if not any(
            getattr(code, name, None) for name in ("on_chat_start", "on_message")
        ):
            raise RuntimeError(
                "You need to configure at least one of on_chat_start or "
                "on_message callback"
            )

    def _assert_auth_secret(self) -> None:
        """An app that requires a login and has no auth wiring cannot serve one.

        The old ``ensure_jwt_secret`` asked whether a secret was in the
        environment. That is the wrong question here: a host that builds
        ``chainlit_auth(token_secret=...)`` itself is correctly configured
        with no environment variable at all. The invariant is that login is
        required and no authentication middleware got installed.

        ``require_login()`` itself is not reused: ``chainlit.auth`` is still
        on FastAPI, and importing it from the new stack would drag it back
        in. The same question is asked of the callbacks directly.
        """
        if self._auth is not None:
            return
        code = self._code()
        # `header_auth_callback` is deliberately absent: `POST /auth/header`
        # is not ported, and `/auth/config` reports `headerAuth: false`, so
        # counting it here would demand a login from an app that has no way
        # to perform one.
        requires_login = bool(os.environ.get("CHAINLIT_CUSTOM_AUTH")) or any(
            getattr(code, name, None)
            for name in ("password_auth_callback", "oauth_callback")
        )
        if requires_login:
            raise ValueError(
                "You must provide a JWT secret in the environment to use "
                "authentication. Run `chainlit create-secret` to generate one."
            )

    def _init_markdown(self) -> None:
        if self._config is None or (root := self._config.root) is None:
            return
        from chainlit.markdown import init_markdown

        init_markdown(root)
