"""The switch that decides whether the composer offers the way back.

Off by default and overridable only upwards, which is the same statement
twice: ``_overlay`` counts a field as set when it differs from its class
default, so a profile can turn a ``False`` on and could never turn a ``True``
off. The value travels to the client as a plain field of ``UISettings``
(``controllers/project.py`` dumps the section with ``msgspec.to_builtins``,
and ``Settings`` has no ``omit_defaults``), so ``false`` is on the wire rather
than absent from it -- the client is never left guessing a default of its own.
"""

import msgspec

from chainlit.config import ChainlitConfig, ChainlitConfigOverrides, UISettings


class TestDefault:
    def test_a_deployment_that_never_asked_does_not_get_it(self):
        assert UISettings(name="test").show_parent_thread_button is False

    def test_a_toml_table_turns_it_on(self):
        ui = msgspec.convert(
            {"name": "test", "show_parent_thread_button": True},
            type=UISettings,
        )

        assert ui.show_parent_thread_button is True

    def test_a_section_without_the_key_still_decodes(self):
        assert (
            msgspec.convert({"name": "test"}, type=UISettings).show_parent_thread_button
            is False
        )


class TestOverride:
    def test_a_profile_switches_it_on(self, test_config: ChainlitConfig):
        effective = test_config.with_overrides(
            ChainlitConfigOverrides(
                ui=UISettings(name="Handoff", show_parent_thread_button=True)
            )
        )

        assert effective.ui.show_parent_thread_button is True
        # The process-wide section is untouched: the next profile to ask for
        # the config must not inherit this one's button.
        assert test_config.ui.show_parent_thread_button is False

    def test_a_profile_that_says_nothing_keeps_the_base(
        self, test_config: ChainlitConfig
    ):
        effective = test_config.with_overrides(
            ChainlitConfigOverrides(ui=UISettings(name="Plain"))
        )

        assert effective.ui.show_parent_thread_button is False


class TestWire:
    def test_the_key_is_on_the_wire_even_when_off(self):
        payload = msgspec.to_builtins(UISettings(name="test"))

        assert payload["show_parent_thread_button"] is False
