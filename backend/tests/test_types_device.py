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
