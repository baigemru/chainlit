import pytest

from chainlit.oauth_providers import (
    KeycloakOAuthProvider,
    OAuthProvider,
    get_oauth_provider_details,
)


class StubProvider(OAuthProvider):
    id = "test"
    env = []

    def __init__(self, vk_idp_hint="vkid"):
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.authorize_url = "https://idp.example.com/auth"
        self.authorize_params = {
            "scope": "openid",
            "response_type": "code",
        }
        self._vk_idp_hint = vk_idp_hint

    def get_vk_idp_hint(self):
        return self._vk_idp_hint


class TestEnvFlags:
    def test_vk_button_defaults_to_false(self):
        assert StubProvider().is_vk_button_enabled() is False

    def test_vk_button_env_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_TEST_VK_BUTTON", "true")
        assert StubProvider().is_vk_button_enabled() is True

    def test_vk_button_requires_idp_hint(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_TEST_VK_BUTTON", "true")
        provider = StubProvider(vk_idp_hint=None)
        assert provider.is_vk_button_enabled() is False

    def test_base_provider_has_no_idp_hint(self):
        class Bare(OAuthProvider):
            id = "bare"
            env = []

        assert Bare().get_vk_idp_hint() is None


class TestKeycloakIdpHint:
    def test_default_alias(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_KEYCLOAK_BASE_URL", "https://kc.example.com")
        monkeypatch.setenv("OAUTH_KEYCLOAK_REALM", "realm")
        assert KeycloakOAuthProvider().get_vk_idp_hint() == "vkid"

    def test_alias_env_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_KEYCLOAK_BASE_URL", "https://kc.example.com")
        monkeypatch.setenv("OAUTH_KEYCLOAK_REALM", "realm")
        monkeypatch.setenv("OAUTH_KEYCLOAK_VK_IDP_ALIAS", "vk-custom")
        assert KeycloakOAuthProvider().get_vk_idp_hint() == "vk-custom"


class TestProviderDetails:
    def test_provider_details_include_vk_flag(self, monkeypatch: pytest.MonkeyPatch):
        provider = StubProvider()
        monkeypatch.setattr("chainlit.oauth_providers.providers", [provider])
        monkeypatch.setenv("OAUTH_TEST_VK_BUTTON", "true")

        details = get_oauth_provider_details()[0]
        assert details["vkEnabled"] is True
