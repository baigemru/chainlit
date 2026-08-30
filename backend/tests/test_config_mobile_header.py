"""The two knobs that decide what a phone's header keeps.

Both travel to the client as plain fields of ``UISettings`` (``project.py``
dumps the section with ``msgspec.to_builtins``), so the decode path is the
contract: a TOML table has to produce the same values the constructor does.
"""

import msgspec

from chainlit.config import DEFAULT_MOBILE_HEADER, HeaderLink, UISettings


class TestMobileHeader:
    def test_default_keeps_the_three_a_phone_needs(self):
        assert UISettings(name="test").mobile_header == [
            "new_chat",
            "chat_profiles",
            "user_nav",
        ]

    def test_default_is_not_shared_between_sections(self):
        # A default handing out the module-level list would let one section's
        # edit rewrite the constant, so the copy is taken before the edit.
        expected = list(DEFAULT_MOBILE_HEADER)

        first = UISettings(name="test")
        first.mobile_header.append("theme")

        assert UISettings(name="test").mobile_header == expected
        assert DEFAULT_MOBILE_HEADER == expected

    def test_toml_list_replaces_the_default(self):
        ui = msgspec.convert(
            {"name": "test", "mobile_header": ["new_chat"]},
            type=UISettings,
        )

        assert ui.mobile_header == ["new_chat"]

    def test_empty_list_is_not_the_default(self):
        # An app that wants a bare header says so; it must not read as "unset".
        ui = msgspec.convert({"name": "test", "mobile_header": []}, type=UISettings)

        assert ui.mobile_header == []

    def test_section_without_the_key_still_decodes(self):
        ui = msgspec.convert({"name": "test"}, type=UISettings)

        assert ui.mobile_header == DEFAULT_MOBILE_HEADER


class TestCollapseOnMobile:
    def test_a_link_collapses_unless_told_otherwise(self):
        assert HeaderLink(name="Issues", url="/issues").collapse_on_mobile is True

    def test_a_link_can_be_pinned_to_the_header(self):
        ui = msgspec.convert(
            {
                "name": "test",
                "header_links": [
                    {"name": "Balance", "url": "/balance", "collapse_on_mobile": False},
                    {"name": "Issues", "url": "/issues"},
                ],
            },
            type=UISettings,
        )

        assert ui.header_links is not None
        assert ui.header_links[0].collapse_on_mobile is False
        assert ui.header_links[1].collapse_on_mobile is True

    def test_both_fields_reach_the_client_payload(self):
        payload = msgspec.to_builtins(
            UISettings(
                name="test",
                mobile_header=["user_nav"],
                header_links=[HeaderLink(name="Balance", url="/balance")],
            )
        )

        assert payload["mobile_header"] == ["user_nav"]
        assert payload["header_links"][0]["collapse_on_mobile"] is True
