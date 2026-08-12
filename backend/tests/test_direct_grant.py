from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from chainlit.auth import get_configuration, require_login
from chainlit.config import config
from chainlit.oauth_providers import OAuthProvider
from chainlit.server import app
from chainlit.user import User


class DirectGrantProvider(OAuthProvider):
    id = "test"
    env = []

    def __init__(self, token="direct-grant-token"):
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.authorize_url = "https://idp.example.com/auth"
        self.authorize_params = {}
        self._token = token
        self.password_calls = []

    def is_direct_grant_enabled(self) -> bool:
        return True

    async def get_token_with_password(self, username: str, password: str) -> str:
        self.password_calls.append((username, password))
        return self._token

    async def get_user_info(self, token: str):
        raw = {"email": "user@example.com"}
        return (raw, User(identifier="user@example.com"))


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def auth_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHAINLIT_AUTH_SECRET", "test-secret" * 8)


@pytest.fixture
def direct_grant_provider(monkeypatch: pytest.MonkeyPatch, auth_secret):
    provider = DirectGrantProvider()
    monkeypatch.setattr("chainlit.oauth_providers.providers", [provider])
    monkeypatch.setattr(config.code, "password_auth_callback", None)
    monkeypatch.setattr(
        config.code,
        "oauth_callback",
        AsyncMock(return_value=User(identifier="user@example.com")),
    )
    return provider


class TestDirectGrantLogin:
    def test_login_uses_direct_grant_and_unified_hook(
        self, test_client: TestClient, direct_grant_provider: DirectGrantProvider
    ):
        response = test_client.post(
            "/login", data={"username": "user@example.com", "password": "pw"}
        )

        assert response.status_code == 200, response.json()
        assert response.json()["success"] is True
        assert "access_token" in response.cookies

        assert direct_grant_provider.password_calls == [("user@example.com", "pw")]
        config.code.oauth_callback.assert_awaited_once_with(
            "test",
            "direct-grant-token",
            {"email": "user@example.com"},
            User(identifier="user@example.com"),
        )

    def test_invalid_credentials_return_credentialssignin(
        self,
        test_client: TestClient,
        direct_grant_provider: DirectGrantProvider,
        monkeypatch: pytest.MonkeyPatch,
    ):
        async def fail(username, password):
            raise HTTPException(status_code=401, detail="credentialssignin")

        monkeypatch.setattr(direct_grant_provider, "get_token_with_password", fail)

        response = test_client.post(
            "/login", data={"username": "user@example.com", "password": "bad"}
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "credentialssignin"

    def test_password_auth_callback_takes_precedence(
        self,
        test_client: TestClient,
        direct_grant_provider: DirectGrantProvider,
        monkeypatch: pytest.MonkeyPatch,
    ):
        password_callback = AsyncMock(return_value=User(identifier="cb@example.com"))
        monkeypatch.setattr(config.code, "password_auth_callback", password_callback)

        response = test_client.post(
            "/login", data={"username": "cb@example.com", "password": "pw"}
        )

        assert response.status_code == 200
        password_callback.assert_awaited_once_with("cb@example.com", "pw")
        assert direct_grant_provider.password_calls == []
        config.code.oauth_callback.assert_not_awaited()

    def test_login_400_without_any_callback(
        self, test_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("chainlit.oauth_providers.providers", [])
        monkeypatch.setattr(config.code, "password_auth_callback", None)
        monkeypatch.setattr(config.code, "oauth_callback", None)

        response = test_client.post(
            "/login", data={"username": "user@example.com", "password": "pw"}
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "No auth_callback defined"

    def test_direct_grant_enables_password_auth_and_login(
        self, direct_grant_provider: DirectGrantProvider
    ):
        assert require_login() is True
        assert get_configuration()["passwordAuth"] is True


class TestKeycloakDirectGrant:
    @pytest.fixture
    def keycloak_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_KEYCLOAK_CLIENT_ID", "kc-client")
        monkeypatch.setenv("OAUTH_KEYCLOAK_CLIENT_SECRET", "kc-secret")
        monkeypatch.setenv("OAUTH_KEYCLOAK_REALM", "myrealm")
        monkeypatch.setenv("OAUTH_KEYCLOAK_BASE_URL", "https://kc.example.com")

    def test_registration_url_derived(self, keycloak_env):
        from chainlit.oauth_providers import KeycloakOAuthProvider

        provider = KeycloakOAuthProvider()
        assert provider.registration_url == (
            "https://kc.example.com/realms/myrealm/protocol/openid-connect/registrations"
        )

    def test_forgot_password_url_derived_when_enabled(
        self, keycloak_env, monkeypatch: pytest.MonkeyPatch
    ):
        from chainlit.oauth_providers import KeycloakOAuthProvider

        assert KeycloakOAuthProvider().forgot_password_url is None

        monkeypatch.setenv("OAUTH_KEYCLOAK_FORGOT_PASSWORD", "true")
        provider = KeycloakOAuthProvider()
        assert provider.forgot_password_url == (
            "https://kc.example.com/realms/myrealm/login-actions/reset-credentials"
            "?client_id=kc-client"
        )

    def test_direct_grant_flag(self, keycloak_env, monkeypatch: pytest.MonkeyPatch):
        from chainlit.oauth_providers import KeycloakOAuthProvider

        assert KeycloakOAuthProvider().is_direct_grant_enabled() is False

        monkeypatch.setenv("OAUTH_KEYCLOAK_DIRECT_GRANT", "true")
        assert KeycloakOAuthProvider().is_direct_grant_enabled() is True

    async def test_get_token_with_password_invalid_grant(
        self, keycloak_env, monkeypatch: pytest.MonkeyPatch
    ):
        import httpx

        from chainlit.oauth_providers import KeycloakOAuthProvider

        provider = KeycloakOAuthProvider()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid_grant"})

        transport = httpx.MockTransport(handler)
        original_client = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *args, **kwargs: original_client(transport=transport),
        )

        with pytest.raises(HTTPException) as exc_info:
            await provider.get_token_with_password("user", "bad")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "credentialssignin"

    async def test_get_token_with_password_success(
        self, keycloak_env, monkeypatch: pytest.MonkeyPatch
    ):
        import urllib.parse

        import httpx

        from chainlit.oauth_providers import KeycloakOAuthProvider

        provider = KeycloakOAuthProvider()
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode()
            seen["data"] = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
            return httpx.Response(200, json={"access_token": "ropc-token"})

        transport = httpx.MockTransport(handler)
        original_client = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *args, **kwargs: original_client(transport=transport),
        )

        token = await provider.get_token_with_password("user", "pw")

        assert token == "ropc-token"
        assert seen["data"]["grant_type"] == "password"
        assert seen["data"]["username"] == "user"
        assert seen["data"]["client_id"] == "kc-client"


class TestKeycloakDirectGrantErrors:
    """Error mapping of KeycloakOAuthProvider.get_token_with_password."""

    def _provider(self, monkeypatch: pytest.MonkeyPatch):
        from chainlit.oauth_providers import KeycloakOAuthProvider

        monkeypatch.setenv("OAUTH_KEYCLOAK_BASE_URL", "https://kc.example.com")
        monkeypatch.setenv("OAUTH_KEYCLOAK_REALM", "realm")
        monkeypatch.setenv("OAUTH_KEYCLOAK_CLIENT_ID", "client-id")
        monkeypatch.setenv("OAUTH_KEYCLOAK_CLIENT_SECRET", "client-secret")
        return KeycloakOAuthProvider()

    def _mock_token_response(self, monkeypatch, status_code, json_body):
        import httpx

        async def post(self, url, data=None, **kwargs):
            return httpx.Response(
                status_code,
                json=json_body,
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", post)

    async def test_pending_required_actions_map_to_accountnotsetup(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        provider = self._provider(monkeypatch)
        self._mock_token_response(
            monkeypatch,
            400,
            {
                "error": "invalid_grant",
                "error_description": "Account is not fully set up",
            },
        )

        with pytest.raises(HTTPException) as exc_info:
            await provider.get_token_with_password("user@example.com", "pw")

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "accountnotsetup"

    async def test_wrong_credentials_still_map_to_credentialssignin(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        provider = self._provider(monkeypatch)
        self._mock_token_response(
            monkeypatch,
            401,
            {
                "error": "invalid_grant",
                "error_description": "Invalid user credentials",
            },
        )

        with pytest.raises(HTTPException) as exc_info:
            await provider.get_token_with_password("user@example.com", "bad")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "credentialssignin"
