"""`[UI.account]`, and what leaves `UISettings` with it.

The section travels to the client as a plain field of `UISettings`
(`project.py` dumps it with `msgspec.to_builtins`), so the decode path is the
contract: a TOML table has to produce the same values the constructor does,
and the shipped example has to be one of those tables.
"""

import tomllib

import msgspec
import pytest

from chainlit.config import DEFAULT_CONFIG_STR, AccountSection, UISettings


class TestAccountSection:
    def test_absent_section_is_none(self):
        # No table means no row in the user menu; an app opts in.
        assert msgspec.convert({"name": "test"}, type=UISettings).account is None

    def test_a_table_fills_every_default(self):
        ui = msgspec.convert({"name": "test", "account": {}}, type=UISettings)

        assert ui.account == AccountSection(enabled=True, title="")

    def test_a_present_table_is_enabled_unless_it_says_otherwise(self):
        """Unlike `mobile_notice`: writing the table *is* the opt-in here, so
        the default has to be on or the row would never appear."""
        ui = msgspec.convert({"name": "test", "account": {}}, type=UISettings)
        assert ui.account is not None
        assert ui.account.enabled is True

        off = msgspec.convert(
            {"name": "test", "account": {"enabled": False}}, type=UISettings
        )
        assert off.account is not None
        assert off.account.enabled is False

    def test_a_wrong_type_is_refused(self):
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({"enabled": "yes"}, type=AccountSection)

    def test_every_key_reaches_the_client_payload(self):
        payload = msgspec.to_builtins(
            UISettings(name="test", account=AccountSection(title="Личный кабинет"))
        )

        assert payload["account"] == {"enabled": True, "title": "Личный кабинет"}


class TestTheSchemaIsNotConfigured:
    """The form the client draws is derived from the `@cl.account` Struct and
    added to `/project/settings` by the controller. It must not be reachable
    from a TOML table: a `schema` key a deployment could write would be a
    second description of a shape the Struct already describes, free to
    disagree with it — which is exactly what the retired widget layer was.
    """

    def test_the_section_has_no_schema_field(self):
        fields = {field.name for field in msgspec.structs.fields(AccountSection)}

        assert "schema" not in fields

    def test_a_toml_table_that_sets_one_is_refused(self):
        """A `schema` written into the TOML is a second description of a
        shape the Struct already describes, free to disagree with it. The
        decoder says so by name rather than dropping it."""
        with pytest.raises(msgspec.ValidationError, match="schema"):
            msgspec.convert(
                {
                    "name": "test",
                    "account": {
                        "title": "Кабинет",
                        "schema": {"properties": {"x": {}}},
                    },
                },
                type=UISettings,
            )


class TestRetiredKeys:
    def test_the_chat_settings_keys_are_gone(self):
        """The widget layer they configured is deleted, and a config that
        still carries them is refused by name rather than half-read."""
        fields = {field.name for field in msgspec.structs.fields(UISettings)}

        assert "chat_settings_location" not in fields
        assert "default_chat_settings_open" not in fields

        with pytest.raises(msgspec.ValidationError, match="chat_settings_location"):
            msgspec.convert(
                {"name": "test", "chat_settings_location": "sidebar"},
                type=UISettings,
            )

    def test_user_menu_links_is_gone(self):
        """The user menu is name, the account row, logout. A link under the
        icon was the stand-in for the account page that now exists."""
        fields = {field.name for field in msgspec.structs.fields(UISettings)}

        assert "user_menu_links" not in fields
        assert not hasattr(UISettings(name="test"), "user_menu_links")

        with pytest.raises(msgspec.ValidationError, match="user_menu_links"):
            msgspec.convert(
                {"name": "test", "user_menu_links": [{"name": "x", "url": "y"}]},
                type=UISettings,
            )

    def test_the_config_module_no_longer_exports_the_link_type(self):
        import chainlit.config as config_module

        assert not hasattr(config_module, "UserMenuLink")


def _example_block() -> str:
    """The `[UI.account]` example from the template, uncommented."""
    lines = DEFAULT_CONFIG_STR.splitlines()
    start = lines.index("# [UI.account]")
    block = []
    for line in lines[start:]:
        if not line.startswith("# "):
            break
        block.append(line.removeprefix("# "))
    return "\n".join(block)


class TestShippedExample:
    def test_the_template_is_still_toml(self):
        # The example is commented out, so it must not change what the
        # template parses to for anyone who never touches it.
        parsed = tomllib.loads(DEFAULT_CONFIG_STR)

        assert "account" not in parsed["UI"]
        assert "user_menu_links" not in parsed["UI"]
        assert "chat_settings_location" not in parsed["UI"]

    def test_the_example_decodes(self):
        table = tomllib.loads(_example_block())["UI"]["account"]

        section = msgspec.convert(table, type=AccountSection)

        assert section.enabled is True
        assert section.title == "Account"

    def test_the_template_no_longer_documents_the_retired_keys(self):
        assert "user_menu_links" not in DEFAULT_CONFIG_STR
        assert "chat_settings_location" not in DEFAULT_CONFIG_STR
        assert "default_chat_settings_open" not in DEFAULT_CONFIG_STR
