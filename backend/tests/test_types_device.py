"""The device labels are only useful if they survive ``to_dict``.

``/project/settings`` hands the frontend whatever ``asdict`` produces, so a
field that exists on the dataclass but never reaches the dict is invisible to
the client — these assertions are on the serialised form for that reason.
"""

from chainlit.types import ChatProfile, Starter


class TestStarterDevice:
    def test_defaults_reach_serialisation(self):
        as_dict = Starter(label="Find a supplier", message="find me one").to_dict()

        assert as_dict["device"] == "all"
        assert as_dict["profile"] is None
        assert as_dict["highlight"] is False

    def test_explicit_values_reach_serialisation(self):
        as_dict = Starter(
            label="Switch",
            message="",
            device="mobile",
            profile="Catalog",
            highlight=True,
        ).to_dict()

        assert as_dict["device"] == "mobile"
        assert as_dict["profile"] == "Catalog"
        assert as_dict["highlight"] is True


class TestChatProfileDevice:
    def test_default_is_shown_everywhere(self):
        profile = ChatProfile(name="Catalog", markdown_description="Search it")

        assert profile.to_dict()["device"] == "all"

    def test_explicit_device(self):
        profile = ChatProfile(
            name="Catalog", markdown_description="Search it", device="pc"
        )

        assert profile.to_dict()["device"] == "pc"

    def test_device_and_listed_are_two_different_axes(self):
        """`device` says *where* a profile is offered, `listed` says whether
        it is offered at all. A door profile that only makes sense on a
        desktop sets both, and neither implies the other — a client that
        collapsed them would put doors back in the switcher on a phone."""
        desktop_door = ChatProfile(
            name="Archive",
            markdown_description="Reached through a starter",
            device="pc",
            listed=False,
        ).to_dict()

        assert desktop_door["device"] == "pc"
        assert desktop_door["listed"] is False
        assert (
            ChatProfile(
                name="Entry", markdown_description="d", device="mobile"
            ).to_dict()["listed"]
            is True
        )
