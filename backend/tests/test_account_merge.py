"""`merge_account`: the save is the user's edit, not the user's document.

The document has two writers -- the page a person is looking at, and whatever
the application drops into the same row from a background arrival -- and the
two meet whenever a tab is open while something lands. Every case below is one
of those meetings, written as the three documents the merge is given: what the
page showed (`base`), what came back from the form (`posted`), and what the row
holds when the write happens (`current`).

No database and no request: the rule is in `chainlit.account` precisely so it
can be exercised as a function of three values.
"""

from typing import Annotated, Any, Dict, List

import msgspec
from msgspec import Meta

from chainlit.account import merge_account

KEY = Meta(extra_json_schema={"x-key": True})


class Calculation(msgspec.Struct):
    cny_rate: str = ""
    margin: float = 20.0


class Pair(msgspec.Struct):
    """A card the user edits and an arrival measures. Named by `pair_id`."""

    pair_id: Annotated[str, KEY] = ""
    note: str = ""
    last_measured: str = ""


class Items(msgspec.Struct):
    pairs: List[Pair] = []
    shop: str = ""


class Entry(msgspec.Struct):
    """A feed row with no declared name: the shape panda ships today."""

    title: str = ""
    run_id: str = ""
    seen: bool = False


class Feed(msgspec.Struct):
    entries: List[Entry] = []


class NamedEntry(msgspec.Struct):
    entry_id: Annotated[str, KEY] = ""
    title: str = ""
    seen: bool = False


class NamedFeed(msgspec.Struct):
    entries: List[NamedEntry] = []


class Account(msgspec.Struct):
    calculation: Calculation = msgspec.field(default_factory=Calculation)
    items: Items = msgspec.field(default_factory=Items)
    feed: Feed = msgspec.field(default_factory=Feed)
    named: NamedFeed = msgspec.field(default_factory=NamedFeed)
    tags: List[str] = []


def values(account: msgspec.Struct) -> Dict[str, Any]:
    return msgspec.to_builtins(account)


# --------------------------------------------------------------------------
# The two races this exists for
# --------------------------------------------------------------------------


def test_an_arrival_survives_a_save_that_changed_something_else() -> None:
    """Scenario (a): the arrival appended a feed row and measured a card while
    the user was changing the rate. All three changes are in the result."""
    base = Account(
        calculation=Calculation(cny_rate="11.0"),
        items=Items(pairs=[Pair(pair_id="p1", note="кружки", last_measured="T0")]),
        feed=Feed(entries=[Entry(title="старое", run_id="r0", seen=True)]),
    )
    posted = msgspec.convert(values(base), Account)
    posted.calculation.cny_rate = "12.0"

    current = msgspec.convert(values(base), Account)
    current.feed.entries.append(Entry(title="замер", run_id="r1", seen=False))
    current.items.pairs[0].last_measured = "T1"

    merged = merge_account(base, posted, current)

    assert merged.calculation.cny_rate == "12.0"
    assert [entry.run_id for entry in merged.feed.entries] == ["r0", "r1"]
    assert merged.items.pairs[0].last_measured == "T1"
    assert merged.items.pairs[0].note == "кружки"


def test_an_arrival_survives_a_save_inside_the_same_list() -> None:
    """Scenario (b): the user marked the first feed row seen while the arrival
    put a new row *above* it. The flag lands on the row they were looking at,
    and the new row is still there -- which is what an index would have got
    wrong, since the arrival moved every index under it."""
    base = Account(feed=Feed(entries=[Entry(title="старое", run_id="r0")]))
    posted = Account(feed=Feed(entries=[Entry(title="старое", run_id="r0", seen=True)]))
    current = Account(
        feed=Feed(
            entries=[
                Entry(title="замер", run_id="r1"),
                Entry(title="старое", run_id="r0"),
            ]
        )
    )

    merged = merge_account(base, posted, current)

    assert [(e.run_id, e.seen) for e in merged.feed.entries] == [
        ("r1", False),
        ("r0", True),
    ]


def test_the_same_race_on_a_list_that_declares_x_key() -> None:
    """Scenario (b) again, with the element named: same answer, and it no
    longer depends on the arrival having left the row untouched."""
    base = Account(named=NamedFeed(entries=[NamedEntry(entry_id="e0", title="старое")]))
    posted = Account(
        named=NamedFeed(entries=[NamedEntry(entry_id="e0", title="старое", seen=True)])
    )
    current = Account(
        named=NamedFeed(
            entries=[
                NamedEntry(entry_id="e1", title="замер"),
                NamedEntry(entry_id="e0", title="старое, переписанное"),
            ]
        )
    )

    merged = merge_account(base, posted, current)

    assert [(e.entry_id, e.seen) for e in merged.named.entries] == [
        ("e1", False),
        ("e0", True),
    ]
    # The arrival's own edit to that row is kept: the user did not touch it.
    assert merged.named.entries[1].title == "старое, переписанное"


# --------------------------------------------------------------------------
# The quiet cases: a save that changed nothing must write nothing
# --------------------------------------------------------------------------


def test_a_save_of_what_was_shown_changes_nothing() -> None:
    base = Account(
        calculation=Calculation(cny_rate="11.0"),
        feed=Feed(entries=[Entry(title="старое", run_id="r0")]),
    )
    posted = msgspec.convert(values(base), Account)
    current = Account(
        calculation=Calculation(cny_rate="11.0"),
        feed=Feed(
            entries=[
                Entry(title="замер", run_id="r1"),
                Entry(title="старое", run_id="r0"),
            ]
        ),
    )

    assert values(merge_account(base, posted, current)) == values(current)


def test_a_leaf_the_user_cleared_back_to_its_default_is_written() -> None:
    """The user is not merged away by agreeing with the stored default: what
    they changed is decided against the page they were shown."""
    base = Account(items=Items(shop="старый"))
    posted = Account(items=Items(shop=""))
    current = Account(items=Items(shop="старый"), calculation=Calculation(margin=30.0))

    merged = merge_account(base, posted, current)

    assert merged.items.shop == ""
    assert merged.calculation.margin == 30.0


# --------------------------------------------------------------------------
# Lists: what a declared name buys, and what it costs not to have one
# --------------------------------------------------------------------------


def test_a_named_element_the_user_removed_is_removed() -> None:
    base = Account(items=Items(pairs=[Pair(pair_id="p1"), Pair(pair_id="p2")]))
    posted = Account(items=Items(pairs=[Pair(pair_id="p2")]))
    current = Account(
        items=Items(
            pairs=[
                Pair(pair_id="p1", last_measured="T1"),
                Pair(pair_id="p2"),
                Pair(pair_id="p3", note="из фона"),
            ]
        )
    )

    merged = merge_account(base, posted, current)

    assert [pair.pair_id for pair in merged.items.pairs] == ["p2", "p3"]


def test_a_named_element_the_user_added_is_appended() -> None:
    base = Account(items=Items(pairs=[Pair(pair_id="p1")]))
    posted = Account(items=Items(pairs=[Pair(pair_id="p1"), Pair(pair_id="new")]))
    current = Account(
        items=Items(pairs=[Pair(pair_id="p0", note="из фона"), Pair(pair_id="p1")])
    )

    merged = merge_account(base, posted, current)

    assert [pair.pair_id for pair in merged.items.pairs] == ["p0", "p1", "new"]


def test_a_named_element_the_store_lost_is_not_resurrected() -> None:
    """The user was editing a card another writer has since deleted. Their
    change goes nowhere: re-adding it would undo the deletion."""
    base = Account(items=Items(pairs=[Pair(pair_id="p1", note="было")]))
    posted = Account(items=Items(pairs=[Pair(pair_id="p1", note="стало")]))
    current = Account(items=Items(pairs=[]))

    assert merge_account(base, posted, current).items.pairs == []


def test_an_unnamed_list_the_user_restructured_is_taken_whole() -> None:
    """Without `x-key` a length change is unreadable -- an insertion and an
    edit look the same -- so the save wins the whole list, and the arrival
    inside it is lost. This is the case `x-key` exists to fix."""
    base = Account(feed=Feed(entries=[Entry(run_id="r0")]))
    posted = Account(feed=Feed(entries=[Entry(run_id="r0"), Entry(run_id="added")]))
    current = Account(feed=Feed(entries=[Entry(run_id="r1"), Entry(run_id="r0")]))

    merged = merge_account(base, posted, current)

    assert [entry.run_id for entry in merged.feed.entries] == ["r0", "added"]


def test_an_unnamed_element_both_writers_edited_keeps_the_stored_one() -> None:
    """Nothing in the store looks like what the page showed, so the engine
    cannot say which row the user meant and does not guess."""
    base = Account(feed=Feed(entries=[Entry(title="было", run_id="r0")]))
    posted = Account(feed=Feed(entries=[Entry(title="правка", run_id="r0")]))
    current = Account(feed=Feed(entries=[Entry(title="из фона", run_id="r0")]))

    merged = merge_account(base, posted, current)

    assert [entry.title for entry in merged.feed.entries] == ["из фона"]


def test_a_list_of_scalars_is_one_value() -> None:
    base = Account(tags=["a", "b"])
    posted = Account(tags=["a"])
    current = Account(tags=["a", "b", "c"])

    assert merge_account(base, posted, current).tags == ["a"]
    assert merge_account(
        base, msgspec.convert(values(base), Account), current
    ).tags == [
        "a",
        "b",
        "c",
    ]


# --------------------------------------------------------------------------
# Hidden leaves
# --------------------------------------------------------------------------


class Hidden(msgspec.Struct):
    title: str = ""
    seen: Annotated[bool, Meta(extra_json_schema={"x-widget": "hidden"})] = False


class HiddenFeed(msgspec.Struct):
    entries: List[Hidden] = []
    note: str = ""


def test_a_hidden_leaf_the_application_wrote_survives_a_save_from_an_older_page() -> (
    None
):
    """`seen` is the application's, written by the load hook of a later
    request while this page was open; the form carried the old value back
    untouched because it never drew it. Unchanged between `base` and
    `posted` is exactly "not the user's edit", so the store keeps its own."""
    shown = HiddenFeed(entries=[Hidden(title="Кружка", seen=False)])
    posted = HiddenFeed(entries=[Hidden(title="Кружка", seen=False)], note="ok")
    current = HiddenFeed(entries=[Hidden(title="Кружка", seen=True)])

    merged = merge_account(shown, posted, current)

    assert merged.entries[0].seen is True
    assert merged.note == "ok"
