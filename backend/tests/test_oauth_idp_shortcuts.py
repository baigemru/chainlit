"""Shortcut buttons to one identity provider behind an OAuth broker.

The fork used to carry two of these by name -- VK and Yandex -- as identical
branches on seven layers. The mechanism is one: a provider that brokers other
identity providers takes a hint parameter, and which aliases a deployment
brokers is the deployment's business. So the engine knows ``idp_hint_param``
and the config knows the list.
"""

import pytest

from chainlit import config as chainlit_config
from chainlit.config import IdpShortcut
from chainlit.oauth_providers import (
    KeycloakOAuthProvider,
    OAuthProvider,
    find_idp_shortcut,
    get_oauth_provider_details,
    idp_shortcuts,
)


class BrokerProvider(OAuthProvider):
    """A broker, without a broker."""

    id = "test"
    env: list = []
    idp_hint_param = "kc_idp_hint"

    def __init__(self) -> None:
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.authorize_url = "https://idp.example.com/auth"
        self.authorize_params = {"scope": "openid", "response_type": "code"}


class PlainProvider(OAuthProvider):
    """One that brokers nothing, so no hint could mean anything to it."""

    id = "plain"
    env: list = []


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> list:
    shortcuts = [
        IdpShortcut(id="vk", hint="vkid", label="Sign in with VK"),
        IdpShortcut(
            id="yandex",
            hint="yandex-alias",
            label="Sign in with Yandex",
            icon_url="/public/yandex.svg",
        ),
    ]
    monkeypatch.setattr(chainlit_config.config.ui, "idp_shortcuts", shortcuts)
    return shortcuts


class TestWhichProvidersOfferThem:
    def test_a_broker_offers_every_configured_shortcut(self, configured):
        assert [s.id for s in idp_shortcuts(BrokerProvider())] == ["vk", "yandex"]

    def test_a_provider_that_brokers_nothing_offers_none(self, configured):
        """A hint it ignores would make the button a second login button."""
        assert idp_shortcuts(PlainProvider()) == []

    def test_keycloak_is_a_broker(self):
        assert KeycloakOAuthProvider.idp_hint_param == "kc_idp_hint"

    def test_the_base_provider_is_not(self):
        assert OAuthProvider.idp_hint_param is None

    def test_no_configured_shortcuts_means_no_buttons(self):
        assert idp_shortcuts(BrokerProvider()) == []


class TestLookup:
    def test_a_declared_id_resolves_to_its_hint(self, configured):
        shortcut = find_idp_shortcut(BrokerProvider(), "yandex")
        assert shortcut is not None
        assert shortcut.hint == "yandex-alias"

    def test_an_undeclared_id_resolves_to_nothing(self, configured):
        assert find_idp_shortcut(BrokerProvider(), "facebook") is None

    def test_a_declared_id_on_a_plain_provider_resolves_to_nothing(self, configured):
        assert find_idp_shortcut(PlainProvider(), "vk") is None


class TestProviderDetails:
    def test_the_payload_carries_the_label_and_never_the_hint(
        self, monkeypatch: pytest.MonkeyPatch, configured
    ):
        """The browser is told what to draw and which path to take.

        The alias itself stays on the server: the shortcut id is the path
        segment, and the route looks the hint up from the config.
        """
        monkeypatch.setattr(
            "chainlit.oauth_providers.providers", [BrokerProvider(), PlainProvider()]
        )

        broker, plain = get_oauth_provider_details()

        assert broker["idpShortcuts"] == [
            {
                "id": "vk",
                "label": "Sign in with VK",
                "iconUrl": None,
                "iconUrlLight": None,
                "iconUrlDark": None,
            },
            {
                "id": "yandex",
                "label": "Sign in with Yandex",
                "iconUrl": "/public/yandex.svg",
                "iconUrlLight": "/public/yandex.svg",
                "iconUrlDark": "/public/yandex.svg",
            },
        ]
        assert plain["idpShortcuts"] == []
        assert "vkid" not in repr(broker)
