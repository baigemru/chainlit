"""The element panel: what a reconnect owes it, and what the user can do to it.

The panel used to be the last frame anybody sent about it, so none of these
rows could be written: there was nothing on the server to reconnect *to*, and
no way for the client to say anything about the panel at all. Both halves are
stated here -- the replay, and the five operations with their answer rule.

That rule is the subject of most of the rows. A **scalar** move the client has
already made is applied in silence, because echoing it redraws the middle of a
fast hide-then-show. A **structural** move, and every refusal, answers with the
whole panel, because the client either did not apply it or applied it wrongly.
"""

from ..frames import Expect
from ..spec import (
    Given,
    Incoming,
    Result,
    Scenario,
    SidebarSlotState,
    assert_that,
)

HELLO = Incoming("hello")

CARDS = SidebarSlotState(id="cards", title="Shortlist", elements=("el-1", "el-2"))
PINNED = SidebarSlotState(
    id="pinned", title="Brief", elements=("el-3",), closable=False
)


def _holding(*slots: SidebarSlotState, hidden: bool = False) -> Given:
    return Given(
        restored=True,
        chat_started=True,
        sidebar=slots,
        sidebar_hidden=hidden,
    )


def _states(result: Result) -> list:
    return [frame for frame in result.ledger.frames if frame.tag == "sidebar.state"]


SIDEBAR_SCENARIOS = (
    Scenario(
        name="a reconnect is given the panel back, elements first",
        why=(
            "The panel's elements never enter the transcript -- they go out "
            "with an empty forId -- so nothing else in the replay mentions "
            "them, and the state frame names them by id only. Sent the other "
            "way round the client is handed a panel full of ids it has never "
            "seen."
        ),
        given=_holding(CARDS, PINNED),
        when=(HELLO,),
        expect=(
            Expect("element.upsert", {"element.id": "el-1"}),
            Expect("element.upsert", {"element.id": "el-2"}),
            Expect("element.upsert", {"element.id": "el-3"}),
            Expect(
                "sidebar.state",
                {
                    "slots": lambda slots: (
                        [s["id"] for s in slots] == ["cards", "pinned"]
                    )
                },
            ),
        ),
        then=lambda result: assert_that(
            len(_states(result)) == 1,
            f"the panel was stated more than once: {_states(result)}",
        ),
    ),
    Scenario(
        name="a panel the user put away comes back put away",
        why=(
            "Hiding it is not closing it. A reload that reopened the panel "
            "would put a 95%-wide sheet back over the question the user hid "
            "it to answer."
        ),
        given=_holding(CARDS, hidden=True),
        when=(HELLO,),
        expect=(
            Expect(
                "sidebar.state",
                {"slots": lambda slots: [s["id"] for s in slots] == ["cards"]},
            ),
        ),
        then=lambda result: assert_that(
            all("visible" not in frame.payload for frame in _states(result)),
            "the replay claimed the panel was on screen",
        ),
    ),
    Scenario(
        name="hiding the panel is applied and not answered",
        why=(
            "The client has already hidden it. An echo would be a second "
            "render of a state it is already in, and a fast hide-then-show "
            "would flicker through the middle."
        ),
        given=_holding(CARDS),
        when=(HELLO, Incoming("sidebar.user", {"op": "hide"})),
        then=lambda result: (
            assert_that(
                len(_states(result)) == 1,
                "hiding the panel was echoed back at the client",
            )
            and assert_that(
                result.state["sidebar_visible"] is False,
                "the server did not hide the panel",
            )
            and assert_that(
                result.state["sidebar_slots"] == ["cards"],
                "hiding the panel destroyed what was in it",
            )
        ),
    ),
    Scenario(
        name="a scalar made against a screen that has moved on is answered",
        why=(
            "Silence is the right answer only while the client is looking at "
            "what the server is holding. A click made against a state a frame "
            "in flight has already replaced would otherwise leave the two "
            "parted with nothing left to correct them."
        ),
        given=_holding(CARDS),
        when=(HELLO, Incoming("sidebar.user", {"op": "hide", "rev": 0})),
        then=lambda result: (
            assert_that(
                len(_states(result)) == 2,
                "a stale scalar was passed over in silence",
            )
            and assert_that(
                result.state["sidebar_visible"] is False,
                "the operation itself was not applied",
            )
        ),
    ),
    Scenario(
        name="activating a tab is applied and not answered",
        why="Same as hiding: a scalar the client has already moved.",
        given=_holding(CARDS, PINNED),
        when=(
            HELLO,
            Incoming("sidebar.user", {"op": "activate", "slot": "cards"}),
        ),
        then=lambda result: (
            assert_that(len(_states(result)) == 1, "activating a tab was echoed back")
            and assert_that(result.state["sidebar_active"] == "cards", "not activated")
        ),
    ),
    Scenario(
        name="activating a tab that is not there is answered with the panel",
        why=(
            "The client is showing a tab the server does not have. Silence "
            "would leave the two disagreeing about what is on screen."
        ),
        given=_holding(CARDS),
        when=(HELLO, Incoming("sidebar.user", {"op": "activate", "slot": "ghost"})),
        then=lambda result: assert_that(
            len(_states(result)) == 2,
            "an operation the server refused was passed over in silence",
        ),
    ),
    Scenario(
        name="closing a tab takes it away and answers with the panel",
        why=(
            "Structural, so the client's own guess is replaced rather than "
            "trusted -- and the elements that left with it are taken off the "
            "client, which is the only place they were."
        ),
        given=_holding(CARDS, PINNED),
        when=(HELLO, Incoming("sidebar.user", {"op": "close", "slot": "cards"})),
        expect=(
            Expect("element.remove", {"id": "el-1"}),
            Expect("element.remove", {"id": "el-2"}),
        ),
        then=lambda result: (
            assert_that(
                result.state["sidebar_slots"] == ["pinned"], "the tab did not close"
            )
            and assert_that(len(_states(result)) == 2, "the close was not answered")
        ),
    ),
    Scenario(
        name="a tab that refuses to close is answered with the panel unchanged",
        why=(
            "``closable=False`` is the application saying this one is not the "
            "user's to dismiss. The client has already removed it optimistically, "
            "so the refusal has to be a frame and not silence."
        ),
        given=_holding(CARDS, PINNED),
        when=(HELLO, Incoming("sidebar.user", {"op": "close", "slot": "pinned"})),
        forbid=("element.remove",),
        then=lambda result: (
            assert_that(
                result.state["sidebar_slots"] == ["cards", "pinned"],
                "a slot the application pinned was closed anyway",
            )
            and assert_that(len(_states(result)) == 2, "the refusal was not answered")
        ),
    ),
    Scenario(
        name="closing the last tab puts the panel away",
        why=(
            "``visible`` must never outlive the contents: an empty panel left "
            "on screen is a frame around nothing."
        ),
        given=_holding(CARDS),
        when=(HELLO, Incoming("sidebar.user", {"op": "close", "slot": "cards"})),
        then=lambda result: (
            assert_that(result.state["sidebar_slots"] == [], "the tab did not close")
            and assert_that(
                result.state["sidebar_visible"] is False,
                "the panel stayed on screen with nothing in it",
            )
            and assert_that(
                result.state["sidebar_active"] is None,
                "active still names a slot that is gone",
            )
        ),
    ),
    Scenario(
        name="previewing an element of the conversation opens it in its own tab",
        why=(
            "A click in the feed. The server resolves the id against what this "
            "conversation actually showed -- never against a payload the "
            "browser supplies -- and sends the element ahead of the frame that "
            "names it."
        ),
        given=Given(
            restored=True,
            chat_started=True,
            transcript=(),
            sidebar=(CARDS,),
        ),
        when=(HELLO, Incoming("sidebar.user", {"op": "preview", "elementId": "el-2"})),
        then=lambda result: (
            assert_that(
                result.state["sidebar_slots"] == ["cards", "preview"],
                f"no preview tab: {result.state['sidebar_slots']}",
            )
            and assert_that(
                result.state["sidebar_active"] == "preview",
                "the tab was not brought up",
            )
        ),
    ),
    Scenario(
        name="previewing something this conversation never showed is refused",
        why=(
            "The id comes from a browser. Resolving it anywhere but in this "
            "session's own elements would let a page script ask the server to "
            "display rows it was never shown."
        ),
        given=_holding(CARDS),
        when=(
            HELLO,
            Incoming("sidebar.user", {"op": "preview", "elementId": "somebody-elses"}),
        ),
        then=lambda result: (
            assert_that(
                result.state["sidebar_slots"] == ["cards"],
                "an unresolvable preview opened a tab anyway",
            )
            and assert_that(len(_states(result)) == 2, "the refusal was not answered")
        ),
    ),
)
