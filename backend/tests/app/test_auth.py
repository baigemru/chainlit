"""JWT authentication, on a cookie.

The hand-rolled ``SecurityBase`` subclass and the two FastAPI dependencies
are replaced by stock ``JWTCookieAuth``. Two things make that more than a
translation: it is middleware, so it also runs in the **websocket** scope --
which is the only place a Chainlit session actually lives, and where the
browser cannot set an ``Authorization`` header -- and the token is a stock
``Token``, so what the old stack minted (no ``sub``, claims at the top
level) is refused and every browser logs in once more at cutover.
"""

import jwt as pyjwt
import pytest
from litestar import Request, WebSocket, get, websocket_listener
from litestar.security.jwt import Token
from litestar.testing import create_test_client

from chainlit.plugin import ChainlitPlugin
from chainlit.security import ChainlitAuth, Identity, chainlit_auth

SECRET = "test-secret-not-a-real-one-but-long-enough-for-hs256"
COOKIE = "access_token"
BROWSER = {"accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


@get("/whoami")
async def whoami(request: Request) -> dict:
    user: Identity = request.user
    return {"identifier": user.identifier, "display_name": user.display_name}


@get("/public", exclude_from_auth=True)
async def public() -> dict:
    return {"public": True}


@get("/probe")
async def probe() -> dict:
    """No opt key: this one is public only if the ``exclude`` pattern says so."""
    return {"ok": True}


@get("/public/touching-user", exclude_from_auth=True)
async def public_touching_user(request: Request) -> dict:
    return {"identifier": request.user.identifier}


@websocket_listener("/probe-ws")
async def ws(data: str, socket: WebSocket) -> dict:
    user: Identity = socket.user
    return {"identifier": user.identifier}


def _auth(**kwargs):
    return chainlit_auth(token_secret=SECRET, **kwargs)


def _client(**kwargs):
    auth = kwargs.pop("auth", None) or _auth()
    return create_test_client(
        route_handlers=[whoami, public, public_touching_user, probe, ws],
        plugins=[ChainlitPlugin(auth=auth)],
        debug=False,
        **kwargs,
    )


def _token(auth=None, identifier="ada", **extras) -> str:
    return (auth or _auth()).create_token(identifier=identifier, token_extras=extras)


def _legacy_token(**claims) -> str:
    """A token in exactly the shape ``chainlit/auth/jwt.py`` minted."""
    from datetime import UTC, datetime, timedelta

    payload = {
        "identifier": "ada",
        "display_name": "Ada",
        "metadata": {"role": "admin"},
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "iat": datetime.now(UTC),
    }
    payload.update(claims)
    return pyjwt.encode(payload, SECRET, algorithm="HS256")


# --- the cookie is the credential -------------------------------------------


def test_a_valid_cookie_populates_the_user():
    with _client() as client:
        client.cookies.set(COOKIE, _token(display_name="Ada"))
        response = client.get("/whoami")

    assert response.status_code == 200
    assert response.json() == {"identifier": "ada", "display_name": "Ada"}


def test_no_cookie_is_a_401():
    with _client() as client:
        assert client.get("/whoami").status_code == 401


def test_a_cookie_signed_with_another_secret_is_a_401():
    other = pyjwt.encode(
        {"sub": "ada", "identifier": "ada", "exp": 9999999999},
        "a-different-secret-of-a-perfectly-adequate-length",
        algorithm="HS256",
    )
    with _client() as client:
        client.cookies.set(COOKIE, other)
        assert client.get("/whoami").status_code == 401


def test_an_expired_cookie_is_a_401():
    expired = pyjwt.encode(
        {"sub": "ada", "identifier": "ada", "exp": 1000000000},
        SECRET,
        algorithm="HS256",
    )
    with _client() as client:
        client.cookies.set(COOKIE, expired)
        assert client.get("/whoami").status_code == 401


def test_an_excluded_path_needs_no_credentials():
    with _client() as client:
        assert client.get("/public").json() == {"public": True}


def test_a_regex_excluded_path_needs_no_credentials():
    """``exclude`` takes regex patterns and ``exclude_from_auth`` is an opt
    key: two separate mechanisms, and both have to reach the middleware."""
    with _client() as client:
        assert client.get("/probe").status_code == 401

    with _client(auth=_auth(exclude=["^/probe"])) as client:
        assert client.get("/probe").json() == {"ok": True}


def test_a_public_handler_must_not_touch_the_user():
    """On an excluded path the middleware never ran, so ``request.user``
    *raises* rather than returning ``None`` -- a 500, not an anonymous user.
    Worth a test because the obvious reading of the API is the opposite."""
    with _client() as client:
        assert client.get("/public/touching-user").status_code == 500


# --- the token ---------------------------------------------------------------


def test_a_token_minted_by_the_old_stack_is_refused():
    """No ``sub`` claim: ``Token.decode`` requires one. The cost is one
    re-login per browser at cutover, which the owner accepted over keeping
    a backfill for a payload nothing mints any more."""
    with _client() as client:
        client.cookies.set(COOKIE, _legacy_token())
        assert client.get("/whoami").status_code == 401


def test_the_extras_round_trip_through_the_middleware():
    """``display_name`` and ``metadata`` ride in ``extras``, and the
    ``retrieve_user_handler`` has to put them back on ``connection.user``:
    ``/user`` and the session both read them from there."""

    @get("/me")
    async def me(request: Request) -> dict:
        user: Identity = request.user
        return {
            "identifier": user.identifier,
            "display_name": user.display_name,
            "metadata": user.metadata,
        }

    with create_test_client(
        route_handlers=[me], plugins=[ChainlitPlugin(auth=_auth())], debug=False
    ) as client:
        client.cookies.set(
            COOKIE, _token(display_name="Ada", metadata={"role": "admin"})
        )
        assert client.get("/me").json() == {
            "identifier": "ada",
            "display_name": "Ada",
            "metadata": {"role": "admin"},
        }


def test_claims_at_the_top_level_are_read_as_extras():
    """Stock ``Token.decode`` sweeps unknown claims into ``extras``; a token
    a host minted by hand with ``sub`` plus top-level claims still works."""
    with _client() as client:
        client.cookies.set(COOKIE, _legacy_token(sub="ada"))
        assert client.get("/whoami").json() == {
            "identifier": "ada",
            "display_name": "Ada",
        }


# --- the cookie ---------------------------------------------------------------


def test_the_middleware_reads_the_cookie_the_instance_names():
    """One auth instance, one cookie name. A host that passes
    ``ChainlitAuth(key="foo")`` is read at ``foo`` -- not at whatever the
    environment says."""
    auth = ChainlitAuth(token_secret=SECRET, key="foo")
    with _client(auth=auth) as client:
        client.cookies.set("access_token", _token(auth))
        assert client.get("/whoami").status_code == 401
        client.cookies.clear()
        client.cookies.set("foo", _token(auth))
        assert client.get("/whoami").status_code == 200


def test_the_cookie_name_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("CHAINLIT_AUTH_COOKIE_NAME", "chainlit_token")
    assert _auth().key == "chainlit_token"


def test_the_cookie_path_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("CHAINLIT_AUTH_COOKIE_PATH", "/chat")
    assert _auth().path == "/chat"


def test_samesite_none_forces_a_secure_cookie(monkeypatch):
    monkeypatch.setenv("CHAINLIT_COOKIE_SAMESITE", "none")
    auth = _auth()
    assert auth.samesite == "none"
    assert auth.secure is True


def test_a_nonsense_samesite_is_refused(monkeypatch):
    monkeypatch.setenv("CHAINLIT_COOKIE_SAMESITE", "sometimes")
    with pytest.raises(ValueError, match="CHAINLIT_COOKIE_SAMESITE"):
        _auth()


def test_a_chunked_cookie_is_not_reassembled():
    """The old writer split long tokens across ``<name>_0``, ``<name>_1``.
    Nothing writes those any more and nothing reads them: a browser still
    holding them is logged out, not half logged in."""
    token = _token(metadata={"blob": "x" * 4000})
    with _client() as client:
        for index in range(0, len(token), 3000):
            client.cookies.set(f"{COOKIE}_{index // 3000}", token[index : index + 3000])
        assert client.get("/whoami").status_code == 401


# --- the websocket scope -----------------------------------------------------


def test_the_cookie_authenticates_the_websocket():
    """The reason the cookie is the carrier at all: a browser cannot put an
    Authorization header on an upgrade request. ``JWTCookieAuth`` runs in the
    websocket scope because ``AbstractAuthenticationMiddleware.scopes``
    defaults to ``{http, websocket}``."""
    with _client() as client:
        client.cookies.set(COOKIE, _token())
        with client.websocket_connect("/probe-ws") as socket:
            socket.send_text("ping")
            assert socket.receive_json() == {"identifier": "ada"}


def test_an_unauthenticated_websocket_is_refused():
    from litestar.exceptions import WebSocketDisconnect

    def connect(client) -> None:
        with client.websocket_connect("/probe-ws") as socket:
            socket.send_text("ping")
            socket.receive_json()

    with _client() as client, pytest.raises(WebSocketDisconnect, match="No JWT token"):
        connect(client)


# --- no auth at all ----------------------------------------------------------


def test_without_a_secret_no_middleware_is_installed(monkeypatch):
    """Chainlit's default has always been no authentication."""
    monkeypatch.delenv("CHAINLIT_AUTH_SECRET", raising=False)
    plugin = ChainlitPlugin()
    assert plugin.auth is None


def test_a_secret_in_the_environment_turns_authentication_on(monkeypatch):
    monkeypatch.setenv("CHAINLIT_AUTH_SECRET", SECRET)
    plugin = ChainlitPlugin()
    assert plugin.auth is not None
    assert plugin.auth.token_cls is Token


def test_authentication_can_be_switched_off_explicitly(monkeypatch):
    monkeypatch.setenv("CHAINLIT_AUTH_SECRET", SECRET)
    assert ChainlitPlugin(auth=None).auth is None


def test_the_app_starts_and_serves_without_authentication(monkeypatch, frontend_dir):
    monkeypatch.delenv("CHAINLIT_AUTH_SECRET", raising=False)
    with create_test_client(
        route_handlers=[public], plugins=[ChainlitPlugin(frontend_dir=frontend_dir)]
    ) as client:
        assert client.get("/public").status_code == 200


def test_litestar_is_the_one_deciding_the_token_expiry():
    """``Token`` refuses to be built with an expiry in the past, which is the
    check the hand-rolled ``create_jwt`` never made."""
    from datetime import UTC, datetime, timedelta

    with pytest.raises(Exception):  # noqa: PT011
        Token(sub="ada", exp=datetime.now(UTC) - timedelta(hours=1))


# --- auth on, and a browser that has not logged in yet -----------------------


def test_an_unrouted_browser_path_is_not_behind_auth(frontend_dir):
    """The login page is *in* the SPA, so the document that carries it cannot
    require a login.

    It works because Litestar builds the middleware stack per route handler:
    a path that matches no route raises ``NotFoundException`` in the ASGI
    router before any handler's middleware runs. Worth stating, because
    ``AbstractSecurityConfig.on_app_init`` inserts the middleware at
    ``app_config.middleware[0]``, which reads like a global gate.
    """
    with create_test_client(
        plugins=[ChainlitPlugin(auth=_auth(), frontend_dir=frontend_dir)], debug=False
    ) as client:
        assert client.get("/", headers=BROWSER).status_code == 200
        assert client.get("/thread/abc", headers=BROWSER).status_code == 200


def test_the_frontend_bundle_is_not_behind_auth(frontend_dir):
    """A route that *does* match is gated, and the assets router matches
    everything under /assets. Behind auth a logged-out browser gets
    index.html and then a 401 for every script it references -- a white page
    with no way in."""
    with create_test_client(
        plugins=[ChainlitPlugin(auth=_auth(), frontend_dir=frontend_dir)], debug=False
    ) as client:
        assert client.get("/assets/app.js").status_code == 200


def test_a_gated_route_is_still_gated(frontend_dir):
    """The control for the two above: auth is on, and it bites where it should."""
    with create_test_client(
        route_handlers=[whoami],
        plugins=[ChainlitPlugin(auth=_auth(), frontend_dir=frontend_dir)],
        debug=False,
    ) as client:
        assert client.get("/whoami").status_code == 401


def test_a_host_that_builds_its_own_auth_needs_no_environment(monkeypatch):
    """The startup check asks whether authentication is wired, not whether an
    environment variable is set: a host passing ``chainlit_auth(token_secret=
    ...)`` is correctly configured with no ``CHAINLIT_AUTH_SECRET`` at all."""
    from types import SimpleNamespace

    from litestar import Litestar

    from .test_plugin import start

    monkeypatch.delenv("CHAINLIT_AUTH_SECRET", raising=False)
    config = SimpleNamespace(
        code=SimpleNamespace(
            on_message=lambda m: None, password_auth_callback=lambda *a: None
        ),
        features=None,
        project=None,
        root=None,
    )

    start(Litestar(plugins=[ChainlitPlugin(config, auth=_auth())]))


def test_a_secret_in_the_environment_is_not_authentication(monkeypatch):
    """The check is about the middleware, not the variable.

    Login configured, ``auth=None``, and a secret sitting in the environment
    is the shape that looks configured and is not: nothing would ever read
    that secret, and every request would arrive unauthenticated.
    """
    from types import SimpleNamespace

    from litestar import Litestar

    from .test_plugin import start

    monkeypatch.setenv("CHAINLIT_AUTH_SECRET", SECRET)
    config = SimpleNamespace(
        code=SimpleNamespace(
            on_message=lambda m: None, password_auth_callback=lambda *a: None
        ),
        features=None,
        project=None,
        root=None,
    )

    with pytest.raises(ValueError, match="JWT secret"):
        start(Litestar(plugins=[ChainlitPlugin(config, auth=None)]))
