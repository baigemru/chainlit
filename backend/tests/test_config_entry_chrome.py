"""The two switches that decide what the entry screen and the header show.

``welcome_avatar`` and ``header_wordmark`` are plain fields of ``UISettings``,
so the decode path is the contract: a TOML table has to produce the same
values the constructor does, and ``controllers/project.py`` dumps the section
with ``msgspec.to_builtins`` over a ``Settings`` that has no ``omit_defaults``
-- both keys are therefore on the wire even when they carry their default,
and the client never guesses one of its own.

Their defaults point in opposite directions and that is deliberate, because
``_overlay`` counts a field as set when it differs from its class default: a
profile can hide the avatar it does not want and can turn the wordmark on, and
neither can be undone from a profile once the base section has moved it.
"""

import msgspec

from chainlit.config import ChainlitConfig, ChainlitConfigOverrides, UISettings


class TestWelcomeAvatar:
    def test_a_deployment_that_said_nothing_gets_the_picture(self):
        assert UISettings(name="test").welcome_avatar is True

    def test_a_toml_key_turns_it_off(self):
        ui = msgspec.convert({"name": "test", "welcome_avatar": False}, type=UISettings)

        assert ui.welcome_avatar is False

    def test_a_section_without_the_key_still_decodes(self):
        assert msgspec.convert({"name": "test"}, type=UISettings).welcome_avatar is True

    def test_a_profile_hides_its_own_avatar(self, test_config: ChainlitConfig):
        effective = test_config.with_overrides(
            ChainlitConfigOverrides(ui=UISettings(name="Bare", welcome_avatar=False))
        )

        assert effective.ui.welcome_avatar is False
        # The process-wide section is untouched: the next profile to ask for
        # the config must not inherit this one's bare screen.
        assert test_config.ui.welcome_avatar is True

    def test_a_profile_that_says_nothing_keeps_the_base(
        self, test_config: ChainlitConfig
    ):
        effective = test_config.with_overrides(
            ChainlitConfigOverrides(ui=UISettings(name="Plain"))
        )

        assert effective.ui.welcome_avatar is True


class TestHeaderWordmark:
    def test_upstream_header_opens_with_its_buttons(self):
        assert UISettings(name="test").header_wordmark is False

    def test_a_toml_key_turns_it_on(self):
        ui = msgspec.convert({"name": "test", "header_wordmark": True}, type=UISettings)

        assert ui.header_wordmark is True

    def test_a_section_without_the_key_still_decodes(self):
        assert (
            msgspec.convert({"name": "test"}, type=UISettings).header_wordmark is False
        )

    def test_a_profile_switches_it_on(self, test_config: ChainlitConfig):
        effective = test_config.with_overrides(
            ChainlitConfigOverrides(ui=UISettings(name="Branded", header_wordmark=True))
        )

        assert effective.ui.header_wordmark is True
        assert test_config.ui.header_wordmark is False


class TestWire:
    def test_both_keys_are_on_the_wire_carrying_their_defaults(self):
        payload = msgspec.to_builtins(UISettings(name="test"))

        assert payload["welcome_avatar"] is True
        assert payload["header_wordmark"] is False

    def test_the_wordmark_is_not_in_the_phone_header_by_default(self):
        # It has no overflow rendering, so the only way a phone shows it is a
        # `mobile_header` that names it -- and the default must not.
        assert "wordmark" not in UISettings(name="test").mobile_header
