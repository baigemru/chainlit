"""The profile rules, stated once here so nobody states them twice.

``device``, ``listed`` and ``default`` decide what a person is offered and
where they land, and the decision is made in the browser. An application
that wants to check its own list -- "is this configuration coherent?" --
used to have to reimplement ``matchesDevice``, ``offeredProfiles`` and
``pickDefaultProfile`` in Python, and the copy was one release away from
disagreeing with the original.

So every case here is the client's behaviour, and the references are exact:
``frontend/src/hooks/use-mobile.tsx`` for ``matchesDevice`` and
``pickDefaultProfile``, ``frontend/src/components/header/ChatProfiles.tsx``
for ``offeredProfiles``. A change on either side that is not made on both
belongs in this file as a failure.
"""

import pytest

from chainlit.types import (
    ChatProfile,
    check_one_default_per_device,
    is_offered,
    matches_device,
    pick_default_profile,
)


def profile(name: str, **kwargs) -> ChatProfile:
    return ChatProfile(name=name, markdown_description=name, **kwargs)


class TestMatchesDevice:
    @pytest.mark.parametrize("device", ["mobile", "pc"])
    def test_an_unlabelled_offer_is_for_everyone(self, device):
        assert matches_device(None, device) is True
        assert matches_device("", device) is True
        assert matches_device("all", device) is True

    def test_a_known_label_is_compared_exactly(self):
        assert matches_device("mobile", "mobile") is True
        assert matches_device("mobile", "pc") is False
        assert matches_device("pc", "pc") is True
        assert matches_device("pc", "mobile") is False

    def test_a_label_nobody_recognises_is_shown(self):
        """Fail-open, and the direction matters: a rule that hid what it did
        not recognise would make the *older* side the one that swallows a
        newer application's offer. Showing one too many beats showing none."""
        assert matches_device("tablet", "pc") is True
        assert matches_device("mobil", "mobile") is True


class TestIsOffered:
    def test_listed_and_matching(self):
        assert is_offered(profile("A"), "pc") is True

    def test_an_unlisted_profile_is_a_door_not_an_offer(self):
        assert is_offered(profile("A", listed=False), "pc") is False

    def test_the_wrong_device_is_not_offered_either(self):
        assert is_offered(profile("A", device="mobile"), "pc") is False


class TestPickDefaultProfile:
    def test_the_offered_default_wins(self):
        profiles = [profile("A"), profile("B", default=True)]

        assert pick_default_profile(profiles, "pc") == "B"

    def test_each_device_gets_its_own(self):
        profiles = [
            profile("Phone", device="mobile", default=True),
            profile("Desk", device="pc", default=True),
        ]

        assert pick_default_profile(profiles, "mobile") == "Phone"
        assert pick_default_profile(profiles, "pc") == "Desk"

    def test_an_unlisted_default_is_never_picked(self):
        """A door is opened by somebody else -- a starter, a handoff, a
        resumed thread -- so it is skipped here even carrying ``default`` and
        even as the only profile this device matches."""
        profiles = [profile("Door", listed=False, default=True), profile("Room")]

        assert pick_default_profile(profiles, "pc") == "Room"

    def test_with_no_default_the_first_offered_one(self):
        profiles = [profile("A", device="mobile"), profile("B"), profile("C")]

        assert pick_default_profile(profiles, "pc") == "B"

    def test_it_always_answers_with_a_name(self):
        """Without a profile the client never opens the socket, so a list in
        which everything is filtered out must still yield one: the device
        filter is dropped first, then ``listed``."""
        assert pick_default_profile([profile("A", device="mobile")], "pc") == "A"
        assert pick_default_profile([profile("A", listed=False)], "pc") == "A"
        assert pick_default_profile([], "pc") is None


class TestCheckOneDefaultPerDevice:
    def test_one_each_is_fine(self):
        check_one_default_per_device(
            [
                profile("Phone", device="mobile", default=True),
                profile("Desk", device="pc", default=True),
                profile("Extra"),
            ]
        )

    def test_a_plain_default_covers_both(self):
        check_one_default_per_device([profile("A", default=True), profile("B")])

    def test_two_for_one_device_is_a_coin_toss(self):
        with pytest.raises(ValueError, match="offered on 'mobile': 2 \\(A, B\\)"):
            check_one_default_per_device(
                [profile("A", default=True), profile("B", default=True)]
            )

    def test_none_for_one_device_is_a_landing_nobody_chose(self):
        with pytest.raises(ValueError, match="offered on 'mobile': 0"):
            check_one_default_per_device(
                [profile("Desk", device="pc", default=True), profile("Other")]
            )

    def test_a_door_that_calls_itself_a_landing_place_is_named(self):
        """Reported separately from the miscount, because it is a mistake
        about what ``listed`` means rather than a wrong number."""
        with pytest.raises(ValueError, match="default=True and listed=False"):
            check_one_default_per_device(
                [profile("Door", listed=False, default=True), profile("Room")]
            )

    def test_an_unknown_label_counts_on_every_device(self):
        """Because the client shows it on every device. Two defaults, one of
        them mislabelled, is exactly the configuration error this catches:
        ``device="mobil"`` does not make a second default safe."""
        with pytest.raises(ValueError, match="offered on 'mobile': 2"):
            check_one_default_per_device(
                [
                    profile("Phone", device="mobile", default=True),
                    profile("Typo", device="mobil", default=True),
                ]
            )
