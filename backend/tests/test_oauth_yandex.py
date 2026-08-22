import urllib.parse
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from chainlit.config import config
from chainlit.oauth_providers import (
    KeycloakOAuthProvider,
    OAuthProvider,
    get_oauth_provider_details,
)
from chainlit.server import app


class StubProvider(OAuthProvider):
    id = "test"
    env = []

    def __init__(self, yandex_idp_hint="yandex"):
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.authorize_url = "https://idp.example.com/auth"
        self.authorize_params = {
            "scope": "openid",
            "response_type": "code",
        }
        self._yandex_idp_hint = yandex_idp_hint

    def get_yandex_idp_hint(self):
        return self._yandex_idp_hint


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
    def test_yandex_button_defaults_to_false(self):
        assert StubProvider().is_yandex_button_enabled() is False

    def test_yandex_button_env_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_TEST_YANDEX_BUTTON", "true")
        assert StubProvider().is_yandex_button_enabled() is True

    def test_yandex_button_requires_idp_hint(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_TEST_YANDEX_BUTTON", "true")
        provider = StubProvider(yandex_idp_hint=None)
        assert provider.is_yandex_button_enabled() is False

    def test_base_provider_has_no_idp_hint(self):
        class Bare(OAuthProvider):
            id = "bare"
            env = []

        assert Bare().get_yandex_idp_hint() is None


class TestKeycloakIdpHint:
    def test_default_alias(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_KEYCLOAK_BASE_URL", "https://kc.example.com")
        monkeypatch.setenv("OAUTH_KEYCLOAK_REALM", "realm")
        assert KeycloakOAuthProvider().get_yandex_idp_hint() == "yandex"

    def test_alias_env_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_KEYCLOAK_BASE_URL", "https://kc.example.com")
        monkeypatch.setenv("OAUTH_KEYCLOAK_REALM", "realm")
        monkeypatch.setenv("OAUTH_KEYCLOAK_YANDEX_IDP_ALIAS", "yandex-custom")
        assert KeycloakOAuthProvider().get_yandex_idp_hint() == "yandex-custom"


class TestProviderDetails:
    def test_provider_details_include_yandex_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        provider = StubProvider()
        monkeypatch.setattr("chainlit.oauth_providers.providers", [provider])
        monkeypatch.setenv("OAUTH_TEST_YANDEX_BUTTON", "true")

        details = get_oauth_provider_details()[0]
        assert details["yandexEnabled"] is True


class TestOAuthYandexRoute:
    def test_yandex_redirects_with_idp_hint(
        self,
        test_client: TestClient,
        stub_provider: StubProvider,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("OAUTH_TEST_YANDEX_BUTTON", "true")

        response = test_client.get("/auth/oauth/test/yandex", follow_redirects=False)

        assert response.status_code == 307
        location = response.headers["location"]
        assert location.startswith(stub_provider.authorize_url)

        params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        assert params["kc_idp_hint"] == ["yandex"]
        redirect_uri = params["redirect_uri"][0]
        # The callback must be the shared login callback, not /yandex/callback.
        assert redirect_uri.endswith("/auth/oauth/test/callback")
        assert "/yandex/callback" not in redirect_uri
        assert params["client_id"] == ["client-id"]
        assert params["state"]
        assert "oauth_state" in response.cookies

    def test_yandex_does_not_mutate_shared_authorize_params(
        self,
        test_client: TestClient,
        stub_provider: StubProvider,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("OAUTH_TEST_YANDEX_BUTTON", "true")

        test_client.get("/auth/oauth/test/yandex", follow_redirects=False)

        assert "kc_idp_hint" not in stub_provider.authorize_params

    def test_yandex_404_when_disabled(
        self, test_client: TestClient, stub_provider: StubProvider
    ):
        # OAUTH_TEST_YANDEX_BUTTON not set -> Yandex login disabled.
        response = test_client.get("/auth/oauth/test/yandex", follow_redirects=False)
        assert response.status_code == 404

    def test_yandex_404_for_unknown_provider(
        self, test_client: TestClient, stub_provider: StubProvider
    ):
        response = test_client.get("/auth/oauth/unknown/yandex", follow_redirects=False)
        assert response.status_code == 404

    def test_yandex_400_without_oauth_callback(
        self,
        test_client: TestClient,
        stub_provider: StubProvider,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(config.code, "oauth_callback", None)
        response = test_client.get("/auth/oauth/test/yandex", follow_redirects=False)
        assert response.status_code == 400

    def test_login_route_has_no_idp_hint(
        self, test_client: TestClient, stub_provider: StubProvider
    ):
        response = test_client.get("/auth/oauth/test", follow_redirects=False)

        assert response.status_code == 307
        params = urllib.parse.parse_qs(
            urllib.parse.urlparse(response.headers["location"]).query
        )
        assert "kc_idp_hint" not in params
