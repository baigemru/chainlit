import pytest

from chainlit.oauth_providers import (
    OAuthProvider,
    get_oauth_provider_details,
)


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


class TestRenamedProviderEnvPrefix:
    """A provider renamed via OAUTH_*_NAME keeps its class env prefix.

    Regression: flags used to be resolved only through the id-derived prefix,
    so with OAUTH_KEYCLOAK_NAME=pandapoisk the OAUTH_KEYCLOAK_* flags were
    silently ignored while the credentials still used OAUTH_KEYCLOAK_*.
    """

    class RenamedProvider(StubProvider):
        id = "pandapoisk"
        env_prefix = "KEYCLOAK"

    def test_class_prefix_flag_works_after_rename(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("OAUTH_KEYCLOAK_REGISTRATION_BUTTON", "true")
        assert self.RenamedProvider().is_registration_button_enabled() is True

    def test_id_prefix_flag_still_accepted(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_PANDAPOISK_REGISTRATION_BUTTON", "true")
        assert self.RenamedProvider().is_registration_button_enabled() is True

    def test_class_prefix_wins_over_id_prefix(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_KEYCLOAK_LOGIN_BUTTON", "false")
        monkeypatch.setenv("OAUTH_PANDAPOISK_LOGIN_BUTTON", "true")
        assert self.RenamedProvider().is_login_button_enabled() is False

    def test_icon_url_resolved_through_class_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("OAUTH_KEYCLOAK_ICON_URL", "/public/kc.svg")
        assert self.RenamedProvider().get_icon_url() == "/public/kc.svg"
        assert self.RenamedProvider().get_icon_url("dark") == "/public/kc.svg"


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
                "vkEnabled": False,
                "yandexEnabled": False,
                "iconUrl": None,
                "iconUrlLight": None,
                "iconUrlDark": None,
            }
        ]

    def test_provider_details_custom_icons(self, monkeypatch: pytest.MonkeyPatch):
        provider = StubProvider()
        monkeypatch.setattr("chainlit.oauth_providers.providers", [provider])
        monkeypatch.setenv("OAUTH_TEST_ICON_URL", "/public/test.svg")
        monkeypatch.setenv("OAUTH_TEST_ICON_URL_DARK", "/public/test_dark.svg")

        details = get_oauth_provider_details()[0]
        assert details["iconUrl"] == "/public/test.svg"
        # Themed variant falls back to the theme-agnostic icon.
        assert details["iconUrlLight"] == "/public/test.svg"
        assert details["iconUrlDark"] == "/public/test_dark.svg"
