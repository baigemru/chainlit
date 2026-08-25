import pytest
from fastapi import Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import chainlit as cl
from chainlit.auth import create_jwt
from chainlit.callbacks import _holds_catch_all
from chainlit.config import config
from chainlit.server import app
from chainlit.user import User


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def cleanup_routes():
    """Remove routes added during the test."""
    before = list(app.router.routes)
    yield
    app.router.routes[:] = [route for route in app.router.routes if route in before]


class TestServerRoute:
    def test_route_takes_precedence_over_catch_all(
        self, test_client: TestClient, cleanup_routes
    ):
        @cl.server_route("/custom-json")
        async def custom_json(request: Request):
            return {"custom": True}

        response = test_client.get("/custom-json")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"custom": True}

    def test_route_is_inserted_before_catch_all(self, cleanup_routes):
        @cl.server_route("/custom-position")
        async def custom_position(request: Request):
            return {}

        routes = app.router.routes
        positions = [
            index
            for index, route in enumerate(routes)
            if isinstance(route, APIRoute) and route.path == "/custom-position"
        ]
        assert len(positions) == 1
        # The catch-all is a plain APIRoute here on FastAPI below 0.141 and
        # lives inside an included-router wrapper from 0.141 on; either way the
        # custom route has to come first.
        catch_all = next(
            index for index, route in enumerate(routes) if _holds_catch_all(route)
        )
        assert positions[0] < catch_all

    def test_reregistration_replaces_route(
        self, test_client: TestClient, cleanup_routes
    ):
        @cl.server_route("/custom-reload")
        async def custom_reload_v1(request: Request):
            return {"version": 1}

        # Simulate a watch-mode reload re-executing the user module.
        @cl.server_route("/custom-reload")
        async def custom_reload_v2(request: Request):
            return {"version": 2}

        matching = [
            route
            for route in app.router.routes
            if isinstance(route, APIRoute) and route.path == "/custom-reload"
        ]
        assert len(matching) == 1
        assert test_client.get("/custom-reload").json() == {"version": 2}

    def test_route_with_methods(self, test_client: TestClient, cleanup_routes):
        @cl.server_route("/custom-post", methods=["POST"])
        async def custom_post(request: Request):
            return {"posted": True}

        assert test_client.post("/custom-post").json() == {"posted": True}
        # GET on the same path falls through to the SPA catch-all.
        get_response = test_client.get("/custom-post")
        assert get_response.headers["content-type"].startswith("text/html")

    def test_path_without_leading_slash(self, test_client: TestClient, cleanup_routes):
        @cl.server_route("custom-no-slash")
        async def custom_no_slash(request: Request):
            return {"ok": True}

        assert test_client.get("/custom-no-slash").json() == {"ok": True}


class TestCurrentUser:
    @pytest.fixture
    def auth_enabled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CHAINLIT_AUTH_SECRET", "test-secret" * 8)
        monkeypatch.setenv("CHAINLIT_CUSTOM_AUTH", "true")

    def _request_with_cookie(self, cookie: str = "") -> Request:
        headers = []
        if cookie:
            headers.append((b"cookie", cookie.encode()))
        return Request({"type": "http", "headers": headers})

    async def test_no_cookie_returns_none(self, auth_enabled):
        assert await cl.current_user(self._request_with_cookie()) is None

    async def test_valid_token_returns_user(self, auth_enabled):
        token = create_jwt(User(identifier="user@example.com"))
        request = self._request_with_cookie(f"access_token={token}")

        user = await cl.current_user(request)

        assert user is not None
        assert user.identifier == "user@example.com"

    async def test_invalid_token_returns_none(self, auth_enabled):
        request = self._request_with_cookie("access_token=garbage")
        assert await cl.current_user(request) is None

    async def test_auth_disabled_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CHAINLIT_AUTH_SECRET", "test-secret" * 8)
        monkeypatch.delenv("CHAINLIT_CUSTOM_AUTH", raising=False)
        monkeypatch.setattr(config.code, "password_auth_callback", None)
        monkeypatch.setattr(config.code, "header_auth_callback", None)
        monkeypatch.setattr(config.code, "oauth_callback", None)

        token = create_jwt(User(identifier="user@example.com"))
        request = self._request_with_cookie(f"access_token={token}")

        assert await cl.current_user(request) is None
