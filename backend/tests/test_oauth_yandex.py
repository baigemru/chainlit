import pytest

from chainlit.oauth_providers import (
    KeycloakOAuthProvider,
    OAuthProvider,
    get_oauth_provider_details,
)


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
