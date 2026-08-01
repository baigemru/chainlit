from chainlit.config import HeaderLink, UISettings


class TestHeaderLink:
    def test_minimal_link_without_icon(self):
        link = HeaderLink(name="Buy", url="https://example.com/buy")

        assert link.icon_url is None
        assert link.icon_mask is False
        assert link.authenticated_only is False

    def test_legacy_link_with_icon_still_validates(self):
        link = HeaderLink(
            name="Issues",
            display_name="Report Issue",
            icon_url="https://example.com/icon.svg",
            url="https://example.com/issues",
            target="_blank",
        )

        assert link.icon_url == "https://example.com/icon.svg"

    def test_new_fields(self):
        link = HeaderLink(
            name="Buy",
            url="https://example.com/buy",
            icon_url="/public/buy.svg",
            icon_mask=True,
            authenticated_only=True,
        )

        assert link.icon_mask is True
        assert link.authenticated_only is True

    def test_ui_settings_parses_header_links(self):
        ui = UISettings(
            name="test",
            header_links=[
                {"name": "Buy", "url": "https://example.com/buy"},
                {
                    "name": "Issues",
                    "icon_url": "https://example.com/icon.svg",
                    "url": "https://example.com/issues",
                    "icon_mask": True,
                    "authenticated_only": True,
                },
            ],
        )

        assert ui.header_links is not None
        assert ui.header_links[0].icon_url is None
        assert ui.header_links[1].authenticated_only is True

    def test_ui_settings_forgot_password_url(self):
        assert UISettings(name="test").login_page_forgot_password_url is None
        ui = UISettings(
            name="test",
            login_page_forgot_password_url="https://example.com/reset",
        )
        assert ui.login_page_forgot_password_url == "https://example.com/reset"
