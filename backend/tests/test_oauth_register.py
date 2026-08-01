import urllib.parse
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from chainlit.config import config
from chainlit.oauth_providers import (
    OAuthProvider,
    get_oauth_provider_details,
)
from chainlit.server import app


class StubProvider(OAuthProvider):
    id = "test"
    env = []

    def __init__(self, registration_url="https://idp.example.com/registrations"):
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.authorize_url = "https://idp.example.com/auth"
        self.registration_url = registration_url
        self.authorize_params = {
            "scope": "openid",
            "response_type": "code",
        }


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch):
    provider = StubProvider()
    monkeypatch.setattr(
        "chainlit.server.get_oauth_provider",
        lambda provider_id: provider if provider_id == provider.id else None,
    )
    monkeypatch.setattr(config.code, "oauth_callback", AsyncMock())
    return provider


class TestEnvFlags:
    def test_login_button_defaults_to_true(self):
        assert StubProvider().is_login_button_enabled() is True

    def test_login_button_env_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_TEST_LOGIN_BUTTON", "false")
        assert StubProvider().is_login_button_enabled() is False

    def test_registration_button_defaults_to_false(self):
        assert StubProvider().is_registration_button_enabled() is False

    def test_registration_button_env_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_TEST_REGISTRATION_BUTTON", "true")
        assert StubProvider().is_registration_button_enabled() is True

    def test_registration_button_requires_registration_url(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("OAUTH_TEST_REGISTRATION_BUTTON", "true")
        provider = StubProvider(registration_url=None)
        assert provider.is_registration_button_enabled() is False


class TestProviderDetails:
    def test_provider_details_shape(self, monkeypatch: pytest.MonkeyPatch):
        provider = StubProvider()
        monkeypatch.setattr("chainlit.oauth_providers.providers", [provider])
        monkeypatch.setenv("OAUTH_TEST_REGISTRATION_BUTTON", "true")

        assert get_oauth_provider_details() == [
            {
                "id": "test",
                "loginEnabled": True,
                "registrationEnabled": True,
            }
        ]

    def test_auth_config_exposes_details_and_legacy_list(
        self,
        test_client: TestClient,
        stub_provider: StubProvider,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr("chainlit.oauth_providers.providers", [stub_provider])
        monkeypatch.setenv("OAUTH_TEST_REGISTRATION_BUTTON", "true")

        response = test_client.get("/auth/config")
        assert response.status_code == 200
        data = response.json()

        # Backward compat: legacy field stays a list of provider id strings.
        assert data["oauthProviders"] == ["test"]
        assert data["oauthProviderDetails"] == [
            {
                "id": "test",
                "loginEnabled": True,
                "registrationEnabled": True,
            }
        ]


class TestOAuthRegisterRoute:
    def test_register_redirects_to_registration_url(
        self,
        test_client: TestClient,
        stub_provider: StubProvider,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("OAUTH_TEST_REGISTRATION_BUTTON", "true")

        response = test_client.get("/auth/oauth/test/register", follow_redirects=False)

        assert response.status_code == 307
        location = response.headers["location"]
        assert location.startswith("https://idp.example.com/registrations")

        params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        redirect_uri = params["redirect_uri"][0]
        # The callback must be the shared login callback, not /register/callback.
        assert redirect_uri.endswith("/auth/oauth/test/callback")
        assert "/register/callback" not in redirect_uri
        assert params["client_id"] == ["client-id"]
        assert params["state"]
        assert "oauth_state" in response.cookies

    def test_register_404_when_disabled(
        self, test_client: TestClient, stub_provider: StubProvider
    ):
        # OAUTH_TEST_REGISTRATION_BUTTON not set -> registration disabled.
        response = test_client.get("/auth/oauth/test/register", follow_redirects=False)
        assert response.status_code == 404

    def test_register_404_for_unknown_provider(
        self, test_client: TestClient, stub_provider: StubProvider
    ):
        response = test_client.get(
            "/auth/oauth/unknown/register", follow_redirects=False
        )
        assert response.status_code == 404

    def test_register_400_without_oauth_callback(
        self,
        test_client: TestClient,
        stub_provider: StubProvider,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(config.code, "oauth_callback", None)
        response = test_client.get("/auth/oauth/test/register", follow_redirects=False)
        assert response.status_code == 400

    def test_login_route_still_redirects_to_authorize_url(
        self, test_client: TestClient, stub_provider: StubProvider
    ):
        response = test_client.get("/auth/oauth/test", follow_redirects=False)

        assert response.status_code == 307
        location = response.headers["location"]
        assert location.startswith(stub_provider.authorize_url)

        params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        assert params["redirect_uri"][0].endswith("/auth/oauth/test/callback")
