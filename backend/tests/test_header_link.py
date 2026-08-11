from chainlit.config import HeaderLink, UISettings, UserMenuLink


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

    def test_label_url_fields(self):
        assert HeaderLink(name="x", url="y").label_url is None
        assert HeaderLink(name="x", url="y").label_refresh_interval is None

        link = HeaderLink(
            name="Balance",
            display_name="Баланс",
            url="/billing/balance",
            label_url="/billing/balance",
            label_refresh_interval=60,
            authenticated_only=True,
        )

        assert link.label_url == "/billing/balance"
        assert link.label_refresh_interval == 60

    def test_per_theme_icons(self):
        link = HeaderLink(
            name="Buy",
            url="https://example.com/buy",
            icon_url="/public/buy.svg",
            icon_url_light="/public/buy_light.svg",
            icon_url_dark="/public/buy_dark.svg",
        )

        assert link.icon_url_light == "/public/buy_light.svg"
        assert link.icon_url_dark == "/public/buy_dark.svg"
        assert HeaderLink(name="x", url="y").icon_url_light is None

    def test_user_menu_link_theme_fields(self):
        link = UserMenuLink(
            name="Account",
            url="https://example.com/account",
            icon_url_light="/public/account_light.svg",
            icon_url_dark="/public/account_dark.svg",
            icon_mask=True,
        )

        assert link.icon_url is None
        assert link.icon_mask is True
        assert UserMenuLink(name="x", url="y").icon_mask is False

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
