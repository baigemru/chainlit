"""The pop-up a phone gets about the desktop version.

The section travels to the client as a plain field of ``UISettings``
(``project.py`` dumps it with ``msgspec.to_builtins``), so the decode path is
the contract: a TOML table has to produce the same values the constructor
does, and the shipped example has to be one of those tables.
"""

import tomllib

import msgspec
import pytest

from chainlit.config import DEFAULT_CONFIG_STR, MobileNotice, UISettings


class TestMobileNotice:
    def test_absent_section_is_none(self):
        # No table means no notice; an app opts in, it is not opted out of.
        assert msgspec.convert({"name": "test"}, type=UISettings).mobile_notice is None

    def test_a_table_fills_every_default(self):
        ui = msgspec.convert(
            {"name": "test", "mobile_notice": {"enabled": True}},
            type=UISettings,
        )

        assert ui.mobile_notice == MobileNotice(
            enabled=True,
            mode="dialog",
            title="",
            text="The full application is available on a computer.",
            link_url="/?device=pc",
            link_label="Open the full version",
            dismiss_label="Stay here",
            frequency="session",
        )

    def test_an_empty_table_is_disabled(self):
        ui = msgspec.convert({"name": "test", "mobile_notice": {}}, type=UISettings)

        assert ui.mobile_notice is not None
        assert ui.mobile_notice.enabled is False

    def test_an_unknown_mode_is_refused(self):
        # The client branches on the two it can render; a third would reach
        # the browser as a notice that never appears.
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({"mode": "banner"}, type=MobileNotice)

    def test_an_unknown_frequency_is_refused(self):
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({"frequency": "daily"}, type=MobileNotice)

    def test_every_key_reaches_the_client_payload(self):
        payload = msgspec.to_builtins(
            UISettings(
                name="test",
                mobile_notice=MobileNotice(enabled=True, title="Full version"),
            )
        )

        assert payload["mobile_notice"] == {
            "enabled": True,
            "mode": "dialog",
            "title": "Full version",
            "text": "The full application is available on a computer.",
            "link_url": "/?device=pc",
            "link_label": "Open the full version",
            "dismiss_label": "Stay here",
            "frequency": "session",
        }


def _example_block() -> str:
    """The ``[UI.mobile_notice]`` example from the template, uncommented."""
    lines = DEFAULT_CONFIG_STR.splitlines()
    start = lines.index("# [UI.mobile_notice]")
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

        assert "mobile_notice" not in parsed["UI"]

    def test_the_example_decodes(self):
        table = tomllib.loads(_example_block())["UI"]["mobile_notice"]

        notice = msgspec.convert(table, type=MobileNotice)

        assert notice.enabled is True
        assert notice.mode == "dialog"
        assert notice.frequency == "session"
        assert notice.link_url == "/?device=pc"
