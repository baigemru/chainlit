"""``ChainlitPlugin`` is the whole integration surface.

``mount_chainlit`` built a second application and mounted it; the plugin
contributes into the host's own ``AppConfig``. These tests are written the
way a host writes its app -- ``Litestar(route_handlers=[...],
plugins=[ChainlitPlugin(...)])`` -- so they exercise the path the consumer
uses rather than a parallel one.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from litestar import Litestar, Request, get, post
from litestar.exceptions import NotFoundException
from litestar.testing import TestClient, create_test_client

from chainlit.persistence.config import Persistence
from chainlit.plugin import (
    DEFAULT_MAX_UPLOAD_MB,
    ChainlitPlugin,
    max_request_body_size,
)


@get("/host/hello", exclude_from_auth=True)
async def host_hello() -> dict:
    return {"host": "hello"}


def start(app: Litestar) -> None:
    """Run the app's lifespan, with the startup error it raised, unwrapped.

    anyio's task group re-raises a lifespan failure inside a
    ``BaseExceptionGroup``; a test that asserted on the group would say
    nothing about which check fired.
    """
    try:
        with TestClient(app):
            pass
    except BaseExceptionGroup as group:
        errors = list(group.exceptions)
        while errors and isinstance(errors[0], BaseExceptionGroup):
            errors = list(errors[0].exceptions)
        raise errors[0] from None


def _config(**kwargs) -> SimpleNamespace:
    """A stand-in for ``ChainlitConfig``.

    The real one is pydantic and reads ``.chainlit/config.toml`` from disk on
    import; the plugin only ever reads attributes off it, so a namespace says
    what the test is about without dragging a file tree in.
    """
    defaults = dict(
        code=SimpleNamespace(on_message=lambda m: None),
        features=None,
        project=None,
        root=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# --- the mount_chainlit replacement -----------------------------------------


def test_a_host_route_and_a_chainlit_route_coexist(frontend_dir: Path):
    with create_test_client(
        route_handlers=[host_hello],
        plugins=[ChainlitPlugin(frontend_dir=frontend_dir)],
    ) as client:
        assert client.get("/host/hello").json() == {"host": "hello"}
        assert client.get("/assets/app.js").status_code == 200


def test_the_plugin_adds_no_sub_application(frontend_dir: Path):
    """One app, one middleware stack. The route is registered on the host's
    own router, not behind an ASGI mount."""
    app = Litestar(
        route_handlers=[host_hello], plugins=[ChainlitPlugin(frontend_dir=frontend_dir)]
    )

    paths = {route.path for route in app.routes}
    assert "/host/hello" in paths
    assert "/assets/{file_path:path}" in paths


# --- persistence -------------------------------------------------------------


@pytest.fixture
def persistence() -> Persistence:
    return Persistence.from_url("sqlite+aiosqlite:///:memory:")


def test_the_plugin_registers_the_persistence_plugin(persistence: Persistence):
    """The host lists one plugin, not two. Listing only ``ChainlitPlugin`` and
    forgetting the advanced_alchemy one would import cleanly and fail at the
    first request that touched a service."""
    app = Litestar(plugins=[ChainlitPlugin(persistence=persistence)])

    from advanced_alchemy.extensions.litestar import SQLAlchemyInitPlugin

    assert app.plugins.get(SQLAlchemyInitPlugin)


def test_the_plugin_contributes_the_service_dependencies(persistence: Persistence):
    names = set()

    @get("/thing", exclude_from_auth=True)
    async def thing() -> dict:
        return {}

    app = Litestar(
        route_handlers=[thing], plugins=[ChainlitPlugin(persistence=persistence)]
    )
    names = set(app.dependencies)

    assert {"users", "threads", "steps", "elements", "feedbacks"} <= names
    # Contributed by the advanced_alchemy plugin the ChainlitPlugin registered.
    assert "db_session" in names


def test_without_persistence_the_database_routes_refuse_rather_than_break():
    """No data layer is Chainlit's default and has to stay startable.

    The service names *are* bound, which reads backwards until you see
    why: Litestar resolves dependencies at registration, so a handler
    asking for ``threads`` with nothing bound is a startup failure -- and
    ``/project/settings`` lives on the same controller, so the whole
    frontend would fail to load over a feature the application never asked
    for. They are bound to a refusal instead, and the refusal says what is
    actually wrong.
    """
    app = Litestar(plugins=[ChainlitPlugin()])

    assert "db_session" not in app.dependencies

    with create_test_client(plugins=[ChainlitPlugin()]) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/project/threads", json={}).status_code == 503


# --- request_max_body_size ---------------------------------------------------


class _PluginWithUpload(ChainlitPlugin):
    """Stands in for the upload route the API track will add.

    It goes through ``route_handlers`` -- the same seam the real one will use
    -- so the assertion is about the router's layer, not about a handler
    configured by the test.
    """

    def route_handlers(self):
        @post("/upload", exclude_from_auth=True)
        async def upload(request: Request) -> dict:
            return {"size": len(await request.body())}

        return [*super().route_handlers(), upload]


def test_a_body_over_the_limit_is_refused(frontend_dir: Path):
    plugin = _PluginWithUpload(frontend_dir=frontend_dir, request_max_body_size=1024)
    with create_test_client(plugins=[plugin]) as client:
        under = client.post("/upload", content=b"x" * 1000)
        over = client.post("/upload", content=b"x" * 2000)

    assert under.status_code == 201
    assert over.status_code == 413


def test_the_default_limit_follows_the_configured_upload_size():
    """Litestar caps bodies at 10MB app-wide and FastAPI had no analogue, so
    an unset limit turns every upload over 10MB into a silent 413."""
    assert max_request_body_size(None) > 10_000_000
    assert max_request_body_size(None) == DEFAULT_MAX_UPLOAD_MB * 1024 * 1024 + (
        1 << 20
    )

    config = _config(
        features=SimpleNamespace(spontaneous_file_upload=SimpleNamespace(max_size_mb=7))
    )
    assert max_request_body_size(config) == 7 * 1024 * 1024 + (1 << 20)
    assert ChainlitPlugin(config).request_max_body_size == 7 * 1024 * 1024 + (1 << 20)


def test_the_limit_leaves_room_for_multipart_framing():
    """The limit is on the whole request body: a file of exactly the allowed
    size arrives with boundaries and part headers around it."""
    config = _config(
        features=SimpleNamespace(spontaneous_file_upload=SimpleNamespace(max_size_mb=1))
    )
    assert max_request_body_size(config) > 1 * 1024 * 1024


def test_the_plugin_cannot_raise_the_app_wide_limit(frontend_dir: Path):
    """Stated as a test because it contradicts the obvious reading of the API:
    ``Litestar.__init__`` forwards its own ``request_max_body_size`` argument
    to the router layer and never reads ``AppConfig.request_max_body_size``
    (``litestar/app.py:374`` vs ``:479``). A host that wants Chainlit's limit
    on its own routes has to pass it to ``Litestar`` itself."""

    @post("/host/upload", exclude_from_auth=True)
    async def host_upload(request: Request) -> dict:
        return {"size": len(await request.body())}

    plugin = ChainlitPlugin(frontend_dir=frontend_dir, request_max_body_size=1024)
    with create_test_client(route_handlers=[host_upload], plugins=[plugin]) as client:
        # Well over the plugin's 1024 and well under Litestar's 10MB default.
        assert client.post("/host/upload", content=b"x" * 50_000).status_code == 201

    with TestClient(
        Litestar(
            route_handlers=[host_upload],
            plugins=[ChainlitPlugin(frontend_dir=frontend_dir)],
            request_max_body_size=1024,
        )
    ) as client:
        assert client.post("/host/upload", content=b"x" * 50_000).status_code == 413


# --- bootstrap ---------------------------------------------------------------


def test_an_app_with_no_entry_point_refuses_to_start():
    """The CLI checked this and an embedded host did not, so an embedded app
    with no callbacks served a chat window that silently did nothing."""
    plugin = ChainlitPlugin(_config(code=SimpleNamespace()))

    with pytest.raises(RuntimeError, match="on_chat_start"):
        start(Litestar(plugins=[plugin]))


def test_login_without_a_secret_refuses_to_start(monkeypatch):
    monkeypatch.delenv("CHAINLIT_AUTH_SECRET", raising=False)
    plugin = ChainlitPlugin(
        _config(
            code=SimpleNamespace(
                on_message=lambda m: None, password_auth_callback=lambda *a: None
            )
        ),
        auth=None,
    )

    with pytest.raises(ValueError, match="JWT secret"):
        start(Litestar(plugins=[plugin]))


def test_an_app_without_login_starts_without_a_secret(monkeypatch):
    monkeypatch.delenv("CHAINLIT_AUTH_SECRET", raising=False)
    start(Litestar(plugins=[ChainlitPlugin(_config())]))


def test_the_readme_is_created_at_startup(tmp_path: Path):
    """``init_markdown`` ran in the CLI only, so an embedded deployment never
    got its chainlit.md."""
    start(Litestar(plugins=[ChainlitPlugin(_config(root=str(tmp_path)))]))

    assert (tmp_path / "chainlit.md").is_file()


def test_root_logging_is_left_alone_unless_asked(monkeypatch):
    """A library that reconfigures root logging behind its host's back is
    rude; one that never configures it drops the host's ``logger.info``."""
    calls = []
    monkeypatch.setattr("logging.basicConfig", lambda **kwargs: calls.append(kwargs))

    start(Litestar(plugins=[ChainlitPlugin(_config())]))
    assert calls == []

    with TestClient(
        Litestar(plugins=[ChainlitPlugin(_config(), configure_logging=True)])
    ):
        pass
    assert len(calls) == 1
    assert calls[0]["level"] == 20  # logging.INFO


def test_the_bootstrap_runs_at_startup_not_at_construction():
    """An embedded host may import the module that registers
    ``@cl.on_message`` after it has built the plugin."""
    code = SimpleNamespace()
    plugin = ChainlitPlugin(_config(code=code))
    app = Litestar(plugins=[plugin])  # no raise

    code.on_message = lambda m: None
    start(app)


# --- the SPA fallback is contributed, not assumed ----------------------------


def test_the_fallback_is_registered_on_the_host_config(frontend_dir: Path):
    app = Litestar(plugins=[ChainlitPlugin(frontend_dir=frontend_dir)])
    assert NotFoundException in app.exception_handlers
