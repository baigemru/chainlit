"""The element panel as a model, and the frames that fall out of it.

Every case here asserts on two things at once, because they are the point:
what ``session.sidebar`` became, and what went on the wire. The old panel had
only the second -- there was no model to disagree with -- and that is how
"close" came to mean "destroy".
"""

import pytest
import pytest_asyncio

from chainlit.element import File, Image, Text
from chainlit.protocol.client import SidebarUser
from chainlit.protocol.server import ElementRemove, ElementUpsert, SidebarState
from chainlit.sidebar import Sidebar
from chainlit.ws.sidebar import apply_user_op
from tests.conftest import bind_context


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


def text(name: str, id: str) -> Text:
    """An element with an id the application chose, which is the contract.

    ``set_slot`` replaces by identity, and identity is the id. Without one
    chainlit mints a uuid per call and every replacement is a remount.
    """
    element = Text(content=f"content of {name}", name=name)
    element.id = id
    return element


def last_state(session) -> SidebarState:
    states = [f for f in session.outbound.pending_frames if isinstance(f, SidebarState)]
    assert states, "no sidebar.state frame was sent"
    return states[-1]


def ids_of(frame: SidebarState) -> dict[str, list[str]]:
    return {slot.id: list(slot.element_ids) for slot in frame.slots}


def mark(session) -> int:
    """Where the interesting frames start. The queue is read-only, so a case
    that cares about "what this call sent" remembers the length instead of
    emptying it."""
    return len(session.outbound.pending_frames)


def since(session, start: int) -> list:
    return list(session.outbound.pending_frames[start:])


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------


class TestInvariants:
    async def test_active_is_always_a_slot_that_exists(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", "e1")])
        await Sidebar.set_slot("report", [text("b", "e2")])
        assert session.sidebar.active == "report"

        await Sidebar.close_slot("report")
        # Not left pointing at the slot that went: a tab strip whose active
        # value names nothing renders no tab at all.
        assert session.sidebar.active == "cards"

    async def test_an_empty_element_list_closes_the_slot(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", "e1")])
        await Sidebar.set_slot("cards", [])
        assert session.sidebar.slots == []
        assert last_state(session).slots == []

    async def test_the_last_slot_closing_puts_the_panel_away(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", "e1")])
        assert session.sidebar.visible is True
        await Sidebar.close_slot("cards")
        assert session.sidebar.visible is False
        assert session.sidebar.active is None

    async def test_show_on_an_empty_panel_is_legal(self, ctx, session):
        """The composer's chevron opens the panel whatever is in it.

        A button that sometimes does nothing is worse than an empty frame,
        so an empty visible panel is a state -- reachable only this way.
        """
        await Sidebar.show()
        assert session.sidebar.visible is True
        assert session.sidebar.slots == []
        frame = last_state(session)
        assert frame.visible is True
        assert frame.slots == []

    async def test_hiding_keeps_the_contents(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", "e1")])
        await Sidebar.hide()
        assert session.sidebar.visible is False
        assert ids_of(last_state(session)) == {"cards": ["e1"]}

    async def test_refreshing_a_slot_brings_the_panel_back(self, ctx, session):
        """ "Bring this to the front" is also "put it where it can be seen".

        The consumer refreshes one long-lived slot; a refresh that quietly
        did nothing because the user had put the panel away to answer a
        question is the shape of bug nobody reports.
        """
        await Sidebar.set_slot("cards", [text("a", "e1")])
        await Sidebar.hide()

        await Sidebar.set_slot("cards", [text("a", "e1"), text("b", "e2")])

        assert session.sidebar.visible is True
        assert session.sidebar.active == "cards"

    async def test_a_quiet_slot_takes_neither_the_screen_nor_the_front(
        self, ctx, session
    ):
        """``activate=False`` is the way to fill a slot without being seen."""
        await Sidebar.set_slot("cards", [text("a", "e1")])
        await Sidebar.hide()

        await Sidebar.set_slot("notes", [text("b", "e2")], activate=False)

        assert session.sidebar.visible is False
        assert session.sidebar.active == "cards"
        assert [slot.id for slot in session.sidebar.slots] == ["cards", "notes"]

    async def test_the_preview_address_is_not_the_applications_to_use(
        self, ctx, session
    ):
        """It is where a click in the feed lands; an app writing there would
        erase what the user was looking at, and be erased by their next
        click."""
        with pytest.raises(ValueError, match="preview"):
            await Sidebar.set_slot("preview", [text("a", "e1")])
        with pytest.raises(ValueError, match="preview"):
            await Sidebar.close_slot("preview")
        assert session.sidebar.slots == []

    async def test_closing_the_active_tab_lands_on_its_neighbour(self, ctx, session):
        """Not the first tab: closing the third of four and finding yourself
        on the first is the browser's oldest annoyance."""
        for index in range(4):
            await Sidebar.set_slot(f"s{index}", [text("a", f"e{index}")])
        await Sidebar.activate("s2")

        await Sidebar.close_slot("s2")
        assert session.sidebar.active == "s1"

        # No previous one: the tab that slid into the index.
        await Sidebar.activate("s0")
        await Sidebar.close_slot("s0")
        assert session.sidebar.active == "s1"

    async def test_clear_closes_everything(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", "e1")])
        await Sidebar.set_slot("report", [text("b", "e2")])
        await Sidebar.clear()
        assert session.sidebar.slots == []
        assert session.sidebar.visible is False
        assert session.sidebar.active is None


# --------------------------------------------------------------------------
# Replacement by identity
# --------------------------------------------------------------------------


class TestReplaceByIdentity:
    async def test_a_kept_id_is_upserted_and_never_removed(self, ctx, session):
        """The whole promise of ``set_slot``: same id, same mounted element."""
        await Sidebar.set_slot("cards", [text("a", "e1"), text("b", "e2")])
        start = mark(session)

        await Sidebar.set_slot("cards", [text("a", "e1"), text("c", "e3")])

        sent = since(session, start)
        # Sorted: the elements are sent with one ``gather``, so which of them
        # lands first is not a promise the wire makes.
        upserted = sorted(f.element.id for f in sent if isinstance(f, ElementUpsert))
        removed = [f.id for f in sent if isinstance(f, ElementRemove)]
        assert upserted == ["e1", "e3"]
        # e2 left the slot; e1 stayed and is updated in place.
        assert removed == ["e2"]
        assert ids_of(last_state(session)) == {"cards": ["e1", "e3"]}

    async def test_the_elements_are_on_the_wire_before_the_frame_names_them(
        self, ctx, session
    ):
        """The frame is by reference, so FIFO order is the whole contract."""
        await Sidebar.set_slot("cards", [text("a", "e1")])
        tags = [type(f).__name__ for f in session.outbound.pending_frames]
        assert tags.index("ElementUpsert") < tags.index("SidebarState")

    async def test_a_title_of_none_keeps_the_one_the_slot_has(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", "e1")], title="Shortlist")
        await Sidebar.set_slot("cards", [text("b", "e2")])
        assert session.sidebar.slot("cards").title == "Shortlist"
        assert last_state(session).slots[0].title == "Shortlist"

    async def test_a_new_slot_with_no_title_is_untitled(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", "e1")])
        assert session.sidebar.slot("cards").title == ""

    async def test_elements_stay_out_of_the_transcript(self, ctx, session):
        """Their home is the model; a step they were never attached to
        must not replay them."""
        await Sidebar.set_slot("cards", [Image(name="cat", url="https://x/cat.png")])
        assert session.transcript == []

    async def test_an_element_shown_in_two_slots_survives_one_of_them_closing(
        self, ctx, session
    ):
        await Sidebar.set_slot("left", [text("shared", "e1")])
        await Sidebar.set_slot("right", [text("shared", "e1")])
        start = mark(session)

        await Sidebar.close_slot("right")

        assert not [f for f in since(session, start) if isinstance(f, ElementRemove)]

    async def test_set_elements_spools_but_does_not_persist(self, ctx, session):
        from unittest.mock import Mock

        from chainlit.persistence.writer import SessionWriter, WriterRegistry

        writer = SessionWriter(
            Mock(),
            session.thread_id,
            registry=WriterRegistry(),
            hold_until_interaction=True,
        )
        session.writer = writer
        element = File(name="test.txt", content=b"test content")
        await Sidebar.set_slot("files", [element])
        assert element.chainlit_key in session.files
        assert writer.held == ()


# --------------------------------------------------------------------------
# sidebar.user
# --------------------------------------------------------------------------


def op(session=None, **kwargs) -> SidebarUser:
    """One inbound operation, quoting the revision the client was last shown.

    Passing the session is how a case says "made against what is on screen";
    leaving it out quotes revision 0, which is a client that has seen a state
    older than anything these fixtures produce.
    """
    if session is not None:
        kwargs.setdefault("rev", session.sidebar.rev)
    return SidebarUser(**kwargs)


class TestUserOps:
    @pytest_asyncio.fixture(autouse=True)
    async def furnished(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", "e1")], title="Cards")
        await Sidebar.set_slot("pinned", [text("b", "e2")], closable=False)
        return mark(session)

    async def test_scalar_ops_are_applied_in_silence(self, session, furnished):
        """A fast hide-then-show must not draw the state in between."""
        assert apply_user_op(session, op(session, op="hide")) is False
        assert session.sidebar.visible is False
        assert apply_user_op(session, op(session, op="show")) is False
        assert session.sidebar.visible is True
        assert apply_user_op(session, op(session, op="activate", slot="cards")) is False
        assert session.sidebar.active == "cards"
        assert since(session, furnished) == []

    async def test_activating_a_slot_that_is_not_there_is_answered_with_state(
        self, session
    ):
        assert apply_user_op(session, op(session, op="activate", slot="ghost")) is True
        assert session.sidebar.active == "pinned"

    async def test_a_scalar_made_against_a_stale_screen_is_answered(
        self, session, furnished
    ):
        """The one case silence cannot serve.

        The application moved the panel and the frame saying so is still on
        its way; the click was made against a screen that no longer exists.
        Nothing would ever correct the difference, so this one is answered
        with the whole state -- which lands after the frame in flight and
        settles it.
        """
        session.sidebar.hide()  # as `cl.Sidebar.hide()` would, mid-flight

        assert apply_user_op(session, op(op="show", rev=0)) is True
        assert session.sidebar.visible is True

    async def test_a_scalar_quoting_the_current_revision_is_silent(
        self, session, furnished
    ):
        assert apply_user_op(session, op(session, op="hide")) is False
        assert since(session, furnished) == []

    async def test_every_mutation_moves_the_revision(self, session):
        before = session.sidebar.rev
        apply_user_op(session, op(session, op="hide"))
        assert session.sidebar.rev == before + 1
        apply_user_op(session, op(session, op="close", slot="cards"))
        assert session.sidebar.rev == before + 2

    async def test_the_frame_carries_the_revision_it_states(self, session):
        from chainlit.ws.sidebar import state_frame

        assert state_frame(session.sidebar).rev == session.sidebar.rev

    async def test_close_drops_the_slot_and_answers_with_state(
        self, session, furnished
    ):
        assert apply_user_op(session, op(session, op="close", slot="cards")) is True
        assert [slot.id for slot in session.sidebar.slots] == ["pinned"]
        removed = [
            f.id for f in since(session, furnished) if isinstance(f, ElementRemove)
        ]
        assert removed == ["e1"]

    async def test_close_on_a_slot_that_refuses_is_rejected_with_state(self, session):
        assert apply_user_op(session, op(session, op="close", slot="pinned")) is True
        assert [slot.id for slot in session.sidebar.slots] == ["cards", "pinned"]

    async def test_close_on_an_unknown_slot_is_rejected_with_state(self, session):
        assert apply_user_op(session, op(session, op="close", slot="ghost")) is True
        assert len(session.sidebar.slots) == 2

    async def test_preview_resolves_against_the_transcript(self, session, furnished):
        from chainlit.protocol.payloads import Step, TextElement
        from chainlit.ws.session import TranscriptEntry

        session.transcript.append(
            TranscriptEntry(
                step=Step(id="m1", type="assistant_message"),
                elements=[TextElement(id="e9", name="the report", for_id="m1")],
            )
        )

        # Hidden first: a click in the feed is a request to *see* something,
        # so it brings the panel back as well as filling it.
        apply_user_op(session, op(session, op="hide"))

        assert (
            apply_user_op(session, op(session, op="preview", element_id="e9")) is True
        )
        slot = session.sidebar.slot("preview")
        assert slot is not None
        assert [e.id for e in slot.elements] == ["e9"]
        assert slot.title == "the report"
        assert session.sidebar.visible is True
        # Ahead of the state frame, which names it by id only.
        upserts = [
            f.element.id
            for f in since(session, furnished)
            if isinstance(f, ElementUpsert)
        ]
        assert upserts == ["e9"]

    async def test_preview_resolves_against_the_panels_own_elements(self, session):
        assert (
            apply_user_op(session, op(session, op="preview", element_id="e2")) is True
        )
        assert [e.id for e in session.sidebar.slot("preview").elements] == ["e2"]

    async def test_a_second_preview_releases_what_it_displaced(self, session):
        """But only when nothing else is still showing it.

        The panel's own element goes, because the panel minted it and this
        was the last slot holding it; a feed element would not, which is the
        case below.
        """
        apply_user_op(session, op(session, op="preview", element_id="e1"))
        # "cards" is closable, so e1 is now shown nowhere but the preview.
        apply_user_op(session, op(session, op="close", slot="cards"))
        start = mark(session)

        apply_user_op(session, op(session, op="preview", element_id="e2"))

        removed = [f.id for f in since(session, start) if isinstance(f, ElementRemove)]
        assert removed == ["e1"]

    async def test_a_second_preview_leaves_a_feeds_element_alone(self, session):
        from chainlit.protocol.payloads import Step, TextElement
        from chainlit.ws.session import TranscriptEntry

        session.transcript.append(
            TranscriptEntry(
                step=Step(id="m1", type="assistant_message"),
                elements=[TextElement(id="e9", name="the report", for_id="m1")],
            )
        )
        apply_user_op(session, op(session, op="preview", element_id="e9"))
        start = mark(session)

        apply_user_op(session, op(session, op="preview", element_id="e1"))

        # e9 hangs off a message in the feed; the panel was only borrowing it.
        assert not [f for f in since(session, start) if isinstance(f, ElementRemove)]

    async def test_preview_of_an_element_this_conversation_never_showed_is_refused(
        self, session
    ):
        assert (
            apply_user_op(session, op(session, op="preview", element_id="nope")) is True
        )
        assert session.sidebar.slot("preview") is None

    async def test_closing_a_preview_leaves_the_feeds_element_alone(self, session):
        """The panel was borrowing it; the message it hangs off still shows it."""
        from chainlit.protocol.payloads import Step, TextElement
        from chainlit.ws.session import TranscriptEntry

        session.transcript.append(
            TranscriptEntry(
                step=Step(id="m1", type="assistant_message"),
                elements=[TextElement(id="e9", name="the report", for_id="m1")],
            )
        )
        apply_user_op(session, op(session, op="preview", element_id="e9"))
        start = mark(session)

        apply_user_op(session, op(session, op="close", slot="preview"))

        assert not [f for f in since(session, start) if isinstance(f, ElementRemove)]
