"""The account page's pure half: what `@cl.account` accepts, and what a
stored object that predates the current Struct decodes to.

No Litestar and no database here — `chainlit.account` imports neither, which
is the point of splitting it from the controller.
"""

from datetime import date
from typing import Annotated, Any, Dict, List, Literal, Optional

import msgspec
import pytest
from msgspec import Meta

from chainlit.account import (
    AccountPage,
    as_account,
    build_page,
    decode_stored,
    register,
    schema_of,
    strip_readonly,
)


class Calculation(msgspec.Struct):
    """Параметры расчёта"""

    margin: Annotated[
        float,
        Meta(ge=0, le=100, title="Маржа, %", extra_json_schema={"x-widget": "slider"}),
    ] = 20
    currency: Annotated[
        Literal["USD", "CNY", "RUB"],
        Meta(title="Валюта", extra_json_schema={"x-enum-labels": {"USD": "Доллар"}}),
    ] = "USD"


class Account(msgspec.Struct):
    calculation: Annotated[Calculation, Meta(title="Расчёт")] = msgspec.field(
        default_factory=Calculation
    )
    notify: Annotated[bool, Meta(title="Уведомления")] = True
    since: Annotated[Optional[date], Meta(title="Since")] = None
    plan: Annotated[str, Meta(title="Тариф", extra_json_schema={"readOnly": True})] = (
        "free"
    )
    portal: Annotated[
        str,
        Meta(
            title="Платёжный кабинет",
            extra_json_schema={"readOnly": True, "x-widget": "link"},
        ),
    ] = "/billing/portal"


class TestRegister:
    def test_a_struct_is_returned_unchanged(self):
        assert register(Account) is Account

    def test_anything_that_is_not_a_struct_is_refused(self):
        class Plain:
            pass

        with pytest.raises(TypeError, match=r"msgspec\.Struct subclass"):
            register(Plain)  # type: ignore[type-var]

        with pytest.raises(TypeError, match=r"msgspec\.Struct subclass"):
            register({"margin": 20})  # type: ignore[arg-type]

    def test_a_field_without_a_default_is_refused_by_its_path(self):
        class NoDefault(msgspec.Struct):
            margin: float

        with pytest.raises(TypeError, match=r"NoDefault\.margin has no default"):
            register(NoDefault)

    def test_a_nested_struct_without_a_default_factory_is_refused(self):
        """A bare annotation on a Struct field has no default either, and the
        page then cannot be drawn for a user who never saved."""

        class Nested(msgspec.Struct):
            calculation: Calculation

        with pytest.raises(TypeError, match=r"Nested\.calculation has no default"):
            register(Nested)

    def test_a_required_field_deep_inside_is_refused_by_its_full_path(self):
        class Inner(msgspec.Struct):
            ratio: float

        class Outer(msgspec.Struct):
            inner: Inner = msgspec.field(default_factory=Inner)

        with pytest.raises(TypeError, match=r"Outer\.inner\.ratio has no default"):
            register(Outer)

    def test_a_struct_reached_through_a_list_is_still_walked(self):
        """The walk follows msgspec's own view of the type, so a wrapper --
        a list, an optional, an Annotated -- is not a place to hide a
        required field."""

        class Row(msgspec.Struct):
            label: str

        class WithRows(msgspec.Struct):
            rows: List[Row] = []
            maybe: Optional[Row] = None

        with pytest.raises(TypeError, match=r"WithRows\.rows\.label has no default"):
            register(WithRows)

    def test_the_renamed_encode_name_is_what_the_path_shows(self):
        """The path has to name the key the client sends, not the attribute."""

        class Renamed(msgspec.Struct, rename="camel"):
            max_margin: float

        with pytest.raises(TypeError, match=r"Renamed\.maxMargin has no default"):
            register(Renamed)


class TestSchema:
    def test_the_root_is_a_ref_into_defs(self):
        schema = schema_of(Account)

        assert schema["$ref"] == "#/$defs/Account"
        assert set(schema["$defs"]) == {"Account", "Calculation"}

    def test_meta_reaches_the_schema_as_json_schema_keywords(self):
        properties = schema_of(Account)["$defs"]["Calculation"]["properties"]

        assert properties["margin"]["title"] == "Маржа, %"
        assert properties["margin"]["minimum"] == 0
        assert properties["margin"]["maximum"] == 100
        assert properties["margin"]["x-widget"] == "slider"
        assert properties["currency"]["enum"] == ["CNY", "RUB", "USD"]
        assert properties["currency"]["x-enum-labels"] == {"USD": "Доллар"}

    def test_a_nested_struct_is_a_ref_so_the_client_can_make_it_a_tab(self):
        account = schema_of(Account)["$defs"]["Account"]["properties"]

        assert account["calculation"]["$ref"] == "#/$defs/Calculation"
        assert account["plan"]["readOnly"] is True
        assert account["portal"]["x-widget"] == "link"
        assert account["since"]["anyOf"] == [
            {"type": "string", "format": "date"},
            {"type": "null"},
        ]


class TestBuildPage:
    def test_the_page_carries_schema_values_and_the_readonly_flag(self):
        page = build_page(Account(), readonly=True, message="Saved")

        assert isinstance(page, AccountPage)
        assert page.schema["$ref"] == "#/$defs/Account"
        assert page.values == {
            "calculation": {"margin": 20, "currency": "USD"},
            "notify": True,
            "since": None,
            "plan": "free",
            "portal": "/billing/portal",
        }
        assert page.readonly is True
        assert page.message == "Saved"

    def test_a_date_is_rendered_as_an_iso_string(self):
        page = build_page(Account(since=date(2026, 9, 19)), readonly=False)

        assert page.values["since"] == "2026-09-19"


class TestAsAccount:
    def test_an_instance_is_passed_through(self):
        account = Account(notify=False)

        assert as_account(account, Account, hook="a hook") is account

    def test_a_mapping_is_refused_because_it_would_store_defaults(self):
        """The return is stored as well as shown, and a conversion fills every
        key the mapping omits with the field's default -- so a hook meaning to
        change one thing would write the defaults over everything saved."""
        with pytest.raises(TypeError, match=r"msgspec\.structs\.replace"):
            as_account({"calculation": {"margin": 30}}, Account, hook="a hook")

    def test_none_is_refused_and_the_message_says_what_to_return(self):
        """The hook is handed the stored values now, so "fall back to the
        store" has nothing left to mean -- and a missing `return` would blank
        the page it was meant to fill."""
        with pytest.raises(TypeError, match=r"return account"):
            as_account(None, Account, hook="a hook")

    def test_the_message_names_the_hook_that_misbehaved(self):
        """Two hooks reach this, and «returned None» is no use without saying
        which."""
        with pytest.raises(TypeError, match=r"@cl\.on_account_load"):
            as_account(None, Account, hook="@cl.on_account_load")


class Card(msgspec.Struct):
    title: str = ""
    price: Annotated[float, Meta(extra_json_schema={"readOnly": True})] = 0.0


class Feed(msgspec.Struct):
    cards: List[Card] = []
    unread: Annotated[int, Meta(extra_json_schema={"readOnly": True})] = 0
    seen: bool = False


class Derived(msgspec.Struct):
    """One of each shape a `readOnly` leaf can hide in."""

    feed: Feed = msgspec.field(default_factory=Feed)
    balance: Annotated[Optional[float], Meta(extra_json_schema={"readOnly": True})] = (
        None
    )
    notify: bool = True
    quota: Annotated[Feed, Meta(extra_json_schema={"readOnly": True})] = msgspec.field(
        default_factory=Feed
    )


class TestStripReadonly:
    def test_a_top_level_readonly_leaf_goes_back_to_its_default(self):
        kept = strip_readonly(Derived(balance=1250.0, notify=False))

        assert kept.balance is None
        assert kept.notify is False

    def test_nothing_to_strip_hands_the_instance_straight_back(self):
        """Identity, not equality: the comparison the engine makes before it
        writes is cheap only if an untouched value stays the same object."""
        account = Derived(notify=False)

        assert strip_readonly(account) is account

    def test_a_readonly_leaf_inside_a_nested_struct_is_reset(self):
        kept = strip_readonly(Derived(feed=Feed(unread=9, seen=True)))

        assert kept.feed.unread == 0
        assert kept.feed.seen is True

    def test_a_readonly_leaf_inside_a_list_of_structs_is_reset(self):
        """A card has no stored twin to take a value from -- the list is
        rewritten whole -- so the default is the one rule that works here as
        well as at the top level."""
        account = Derived(
            feed=Feed(cards=[Card(title="Кружка", price=12.5), Card(title="Чай")])
        )

        kept = strip_readonly(account)

        assert [(card.title, card.price) for card in kept.feed.cards] == [
            ("Кружка", 0.0),
            ("Чай", 0.0),
        ]

    def test_a_readonly_struct_field_is_reset_whole(self):
        kept = strip_readonly(Derived(quota=Feed(unread=3, seen=True)))

        assert kept.quota == Feed()

    def test_the_argument_is_not_mutated(self):
        """The caller goes on to show the values it passed in; stripping is
        for the copy that gets stored."""
        account = Derived(balance=99.0, feed=Feed(cards=[Card(price=5.0)]))

        strip_readonly(account)

        assert account.balance == 99.0
        assert account.feed.cards[0].price == 5.0

    def test_a_stripped_value_is_a_fixed_point(self):
        """What the engine stores must strip to itself, or every load would
        look like a change and write again."""
        once = strip_readonly(Derived(balance=1.0, feed=Feed(unread=4)))

        assert strip_readonly(once) is once


class TestDecodeStored:
    def test_an_empty_object_is_the_defaults(self):
        assert decode_stored({}, Account) == Account()

    def test_stored_values_are_merged_over_the_defaults(self):
        account = decode_stored(
            {"calculation": {"margin": 35, "currency": "CNY"}, "notify": False},
            Account,
        )

        assert account.calculation == Calculation(margin=35, currency="CNY")
        assert account.notify is False
        assert account.plan == "free"

    def test_a_key_the_struct_no_longer_declares_is_dropped(self, caplog):
        account = decode_stored({"retired": 1, "notify": False}, Account)

        assert account.notify is False
        assert "retired" in caplog.text

    def test_a_value_that_no_longer_fits_falls_back_to_the_default(self, caplog):
        """The field was retyped under the app's feet. Refusing the whole
        document would take the page away from everyone who saved before."""
        account = decode_stored({"notify": "maybe", "plan": "pro"}, Account)

        assert account.notify is True
        assert account.plan == "pro"
        assert "notify" in caplog.text

    def test_an_out_of_range_stored_value_is_dropped_too(self):
        account = decode_stored({"calculation": {"margin": 300}}, Account)

        assert account.calculation.margin == 20

    def test_an_iso_string_still_decodes_into_a_date(self):
        account = decode_stored({"since": "2026-09-19"}, Account)

        assert account.since == date(2026, 9, 19)

    def test_something_that_is_not_an_object_is_the_defaults(self):
        for stored in ([], "nope", None, 7):
            assert decode_stored(stored, Account) == Account()

    def test_stored_keys_are_matched_on_the_encoded_name(self):
        """``to_builtins`` writes the encoded name, so the read has to look
        for the same one or a renamed Struct round-trips to its defaults."""

        class Renamed(msgspec.Struct, rename="camel"):
            max_margin: float = 20

        stored: Dict[str, Any] = msgspec.to_builtins(Renamed(max_margin=35))

        assert stored == {"maxMargin": 35}
        assert decode_stored(stored, Renamed).max_margin == 35
