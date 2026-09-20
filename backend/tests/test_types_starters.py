"""What the welcome screen is described with, on the serialised side.

`/project/settings` hands the client whatever `to_dict` produces, so a field
that exists on the dataclass and never reaches the dict is a field the client
cannot see. Every assertion here is therefore on `to_dict()`, not on the
attribute — and the exact-dict cases below are the ones that catch a key
quietly renamed on this side while the client keeps reading the old name.

Nothing here asserts that a bad combination is refused, because nothing
refuses it: `layout`, `device`, `href`-with-`profile` and `listed` with
`default` are all advertised as given. A server that vetoed them would be the
older half of a deployment rejecting the newer half's configuration.
"""

from chainlit.types import ChatProfile, Starter, StarterCategory


class TestStarterDefaults:
    def test_a_bare_starter_carries_every_new_key_as_nothing(self):
        as_dict = Starter(label="Find a supplier", message="find me one").to_dict()

        assert as_dict["description"] is None
        assert as_dict["caption"] is None
        assert as_dict["disabled"] is False
        assert as_dict["href"] is None

    def test_the_keys_that_were_already_there_did_not_move(self):
        """The old shape, exactly. A starter is the one payload an older
        frontend reads key by key."""
        as_dict = Starter(label="Ask", message="ask it", icon="i").to_dict()

        assert {k: as_dict[k] for k in ("label", "message", "command", "icon")} == {
            "label": "Ask",
            "message": "ask it",
            "command": None,
            "icon": "i",
        }
        assert as_dict["device"] == "all"
        assert as_dict["profile"] is None
        assert as_dict["highlight"] is False


class TestStarterExplicitValues:
    def test_a_described_plate_serialises_whole(self):
        as_dict = Starter(
            label="Through a buyer's eyes",
            message="",
            description="Measures the item and reports back.",
            caption="~3 min",
            highlight=True,
        ).to_dict()

        assert as_dict["description"] == "Measures the item and reports back."
        assert as_dict["caption"] == "~3 min"
        assert as_dict["highlight"] is True
        assert as_dict["disabled"] is False

    def test_a_disabled_starter_is_still_a_starter(self):
        """Serialised in full: it is shown and refuses the click, so
        everything the client draws it from has to be on the wire."""
        as_dict = Starter(
            label="Set up a subscription",
            message="",
            description="Not wired up yet.",
            disabled=True,
        ).to_dict()

        assert as_dict["disabled"] is True
        assert as_dict["label"] == "Set up a subscription"
        assert as_dict["description"] == "Not wired up yet."

    def test_an_href_starter_reaches_the_client(self):
        as_dict = Starter(
            label="Your items", message="", href="/account?tab=items"
        ).to_dict()

        assert as_dict["href"] == "/account?tab=items"

    def test_nothing_refuses_an_href_that_also_names_a_profile(self):
        """The precedence is the client's (`href`, then `profile`, then
        `message`); this side only has to carry all three so the client can
        apply it."""
        as_dict = Starter(
            label="Both", message="say it", profile="Archive", href="/account"
        ).to_dict()

        assert as_dict["href"] == "/account"
        assert as_dict["profile"] == "Archive"
        assert as_dict["message"] == "say it"


class TestStarterCategory:
    def test_the_default_layout_is_tiles(self):
        as_dict = StarterCategory(label="Quick").to_dict()

        assert as_dict["layout"] == "tiles"
        assert as_dict["description"] is None
        assert as_dict["collapsible"] is False
        assert as_dict["starters"] == []

    def test_an_unknown_layout_is_served_as_written(self):
        """Not validated, for the reason `device` is not: a layout name a
        newer application uses has to reach an older client, which degrades
        it to `tiles` itself."""
        as_dict = StarterCategory(label="Odd", layout="mosaic").to_dict()

        assert as_dict["layout"] == "mosaic"

    def test_a_folded_section_serialises_whole(self):
        as_dict = StarterCategory(
            label="Errands",
            description="The long tail.",
            layout="rows",
            collapsible=True,
            starters=[Starter(label="One", message="one", caption="free")],
        ).to_dict()

        assert as_dict["description"] == "The long tail."
        assert as_dict["layout"] == "rows"
        assert as_dict["collapsible"] is True
        # Nested starters are serialised by the same `asdict`, so a field
        # added to `Starter` has to arrive here too.
        assert as_dict["starters"][0]["caption"] == "free"
        assert as_dict["starters"][0]["href"] is None


class TestChatProfile:
    def test_a_profile_is_listed_unless_it_says_otherwise(self):
        as_dict = ChatProfile(
            name="Catalog", markdown_description="Search it"
        ).to_dict()

        assert as_dict["listed"] is True
        assert as_dict["composer_hint"] is None

    def test_a_door_profile_says_so_on_the_wire(self):
        """`listed=False` is advertised, not acted on: the client is what
        leaves it out of the switcher and out of the default it picks."""
        as_dict = ChatProfile(
            name="Archive",
            markdown_description="Reached through a starter.",
            listed=False,
        ).to_dict()

        assert as_dict["listed"] is False
        assert as_dict["name"] == "Archive"

    def test_a_door_profile_may_still_claim_the_default_and_nothing_stops_it(self):
        """An application configuration error — a landing place nobody can
        land in — and the fork does not arbitrate it. The assertion records
        that on purpose: if a check is ever added, it belongs on the client
        that picks the default, and this test is where the decision changed."""
        as_dict = ChatProfile(
            name="Nowhere",
            markdown_description="Misconfigured",
            default=True,
            listed=False,
        ).to_dict()

        assert as_dict["default"] is True
        assert as_dict["listed"] is False

    def test_the_composer_hint_reaches_the_client(self):
        as_dict = ChatProfile(
            name="Entry",
            markdown_description="Start here",
            composer_hint="**Enter** — a quick search.",
        ).to_dict()

        assert as_dict["composer_hint"] == "**Enter** — a quick search."
