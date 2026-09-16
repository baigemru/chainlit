"""The element panel as a model, and the frames that fall out of it.

Every case here asserts on two things at once, because they are the point:
what ``session.sidebar`` became, and what went on the wire. The old panel had
only the second -- there was no model to disagree with -- and that is how
"close" came to mean "destroy".
"""

import uuid
from unittest.mock import Mock

import pytest
import pytest_asyncio

from chainlit.element import File, Image, Text
from chainlit.persistence.writer import (
    DeleteElement,
    PatchThread,
    SaveElement,
    SessionWriter,
    WriterRegistry,
)
from chainlit.protocol.client import SidebarUser
from chainlit.protocol.server import ElementRemove, ElementUpsert, SidebarState
from chainlit.sidebar import Sidebar
from chainlit.ws.sidebar import (
    SIDEBAR_META_KEY,
    apply_user_op,
    row_id,
    sidebar_meta,
    state_from_meta,
)
from tests.conftest import bind_context


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


def eid(label: str) -> str:
    """The application's own name for a slot element -- any string will do."""
    return f"app-{label}"


def rid(label: str) -> str:
    """The row id the engine mints for ``eid(label)`` in the test session's thread."""
    return row_id("test_thread_id", eid(label))


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


def writer_for(session) -> SessionWriter:
    """A writer whose gate is shut, so what it was given is readable.

    ``held`` is the ops in issue order and nothing has to be flushed to read
    them -- which is also the state a real session is in until its first
    interaction, and the one the panel's writes are issued in.
    """
    writer = SessionWriter(
        Mock(storage=None),
        session.thread_id,
        registry=WriterRegistry(),
        hold_until_interaction=True,
    )
    session.writer = writer
    return writer


def rows(writer: SessionWriter) -> list:
    return [op for op in writer.held if isinstance(op, SaveElement)]


def deletions(writer: SessionWriter) -> list[str]:
    return [op.element_id for op in writer.held if isinstance(op, DeleteElement)]


def patches(writer: SessionWriter) -> list[dict]:
    return [
        op.patch.metadata[SIDEBAR_META_KEY]
        for op in writer.held
        if isinstance(op, PatchThread) and isinstance(op.patch.metadata, dict)
    ]


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------


class TestInvariants:
    async def test_active_is_always_a_slot_that_exists(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        await Sidebar.set_slot("report", [text("b", eid("e2"))])
        assert session.sidebar.active == "report"

        await Sidebar.close_slot("report")
        # Not left pointing at the slot that went: a tab strip whose active
        # value names nothing renders no tab at all.
        assert session.sidebar.active == "cards"

    async def test_an_empty_element_list_closes_the_slot(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        await Sidebar.set_slot("cards", [])
        assert session.sidebar.slots == []
        assert last_state(session).slots == []

    async def test_the_last_slot_closing_puts_the_panel_away(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        assert session.sidebar.visible is True
        await Sidebar.close_slot("cards")
        assert session.sidebar.visible is False
        assert session.sidebar.active is None

    async def test_clear_puts_an_empty_panel_away_too(self, ctx, session):
        """An empty panel on screen is legal only where the user asked for it
        (the chevron); ``clear`` means "away", tabs or no tabs."""
        await Sidebar.show()
        assert session.sidebar.visible is True

        await Sidebar.clear()

        assert session.sidebar.visible is False
        assert last_state(session).visible is False

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
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        await Sidebar.hide()
        assert session.sidebar.visible is False
        assert ids_of(last_state(session)) == {"cards": [rid("e1")]}

    async def test_refreshing_a_slot_brings_the_panel_back(self, ctx, session):
        """ "Bring this to the front" is also "put it where it can be seen".

        The consumer refreshes one long-lived slot; a refresh that quietly
        did nothing because the user had put the panel away to answer a
        question is the shape of bug nobody reports.
        """
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        await Sidebar.hide()

        await Sidebar.set_slot("cards", [text("a", eid("e1")), text("b", eid("e2"))])

        assert session.sidebar.visible is True
        assert session.sidebar.active == "cards"

    async def test_a_quiet_slot_takes_neither_the_screen_nor_the_front(
        self, ctx, session
    ):
        """``activate=False`` is the way to fill a slot without being seen."""
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        await Sidebar.hide()

        await Sidebar.set_slot("notes", [text("b", eid("e2"))], activate=False)

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
            await Sidebar.set_slot("preview", [text("a", eid("e1"))])
        with pytest.raises(ValueError, match="preview"):
            await Sidebar.close_slot("preview")
        assert session.sidebar.slots == []

    async def test_closing_the_active_tab_lands_on_its_neighbour(self, ctx, session):
        """Not the first tab: closing the third of four and finding yourself
        on the first is the browser's oldest annoyance."""
        for index in range(4):
            await Sidebar.set_slot(f"s{index}", [text("a", eid(f"e{index}"))])
        await Sidebar.activate("s2")

        await Sidebar.close_slot("s2")
        assert session.sidebar.active == "s1"

        # No previous one: the tab that slid into the index.
        await Sidebar.activate("s0")
        await Sidebar.close_slot("s0")
        assert session.sidebar.active == "s1"

    async def test_clear_closes_everything(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        await Sidebar.set_slot("report", [text("b", eid("e2"))])
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
        await Sidebar.set_slot("cards", [text("a", eid("e1")), text("b", eid("e2"))])
        start = mark(session)

        await Sidebar.set_slot("cards", [text("a", eid("e1")), text("c", eid("e3"))])

        sent = since(session, start)
        # Sorted: the elements are sent with one ``gather``, so which of them
        # lands first is not a promise the wire makes.
        upserted = sorted(f.element.id for f in sent if isinstance(f, ElementUpsert))
        removed = [f.id for f in sent if isinstance(f, ElementRemove)]
        assert upserted == sorted([rid("e1"), rid("e3")])
        # e2 left the slot; e1 stayed and is updated in place.
        assert removed == [rid("e2")]
        assert ids_of(last_state(session)) == {"cards": [rid("e1"), rid("e3")]}

    async def test_the_elements_are_on_the_wire_before_the_frame_names_them(
        self, ctx, session
    ):
        """The frame is by reference, so FIFO order is the whole contract."""
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        tags = [type(f).__name__ for f in session.outbound.pending_frames]
        assert tags.index("ElementUpsert") < tags.index("SidebarState")

    async def test_a_title_of_none_keeps_the_one_the_slot_has(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", eid("e1"))], title="Shortlist")
        await Sidebar.set_slot("cards", [text("b", eid("e2"))])
        assert session.sidebar.slot("cards").title == "Shortlist"
        assert last_state(session).slots[0].title == "Shortlist"

    async def test_a_new_slot_with_no_title_is_untitled(self, ctx, session):
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        assert session.sidebar.slot("cards").title == ""

    async def test_elements_stay_out_of_the_transcript(self, ctx, session):
        """Their home is the model; a step they were never attached to
        must not replay them."""
        await Sidebar.set_slot("cards", [Image(name="cat", url="https://x/cat.png")])
        assert session.transcript == []

    async def test_an_element_shown_in_two_slots_survives_one_of_them_closing(
        self, ctx, session
    ):
        await Sidebar.set_slot("left", [text("shared", eid("e1"))])
        await Sidebar.set_slot("right", [text("shared", eid("e1"))])
        start = mark(session)

        await Sidebar.close_slot("right")

        assert not [f for f in since(session, start) if isinstance(f, ElementRemove)]

    async def test_a_throwaway_slot_spools_but_does_not_persist(self, ctx, session):
        """``persist=False`` is the loader, the progress card, the draft.

        It keeps the id rule off as well: nothing is written, so nothing has
        to be a uuid.
        """
        writer = writer_for(session)
        element = File(name="test.txt", content=b"test content", id="loader")
        await Sidebar.set_slot("files", [element], persist=False)
        assert element.chainlit_key in session.files
        # No row, and nothing to delete. The panel's own record is still
        # written -- it describes the whole panel, and the slots beside this
        # one are real -- and a slot whose elements have no rows is dropped
        # when the meta is read back, which is ``state_from_meta``'s job.
        assert rows(writer) == []
        assert deletions(writer) == []


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
        await Sidebar.set_slot("cards", [text("a", eid("e1"))], title="Cards")
        await Sidebar.set_slot("pinned", [text("b", eid("e2"))], closable=False)
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
        assert removed == [rid("e1")]

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
                elements=[TextElement(id=eid("e9"), name="the report", for_id="m1")],
            )
        )

        # Hidden first: a click in the feed is a request to *see* something,
        # so it brings the panel back as well as filling it.
        apply_user_op(session, op(session, op="hide"))

        assert (
            apply_user_op(session, op(session, op="preview", element_id=eid("e9")))
            is True
        )
        slot = session.sidebar.slot("preview")
        assert slot is not None
        assert [e.id for e in slot.elements] == [eid("e9")]
        assert slot.title == "the report"
        assert session.sidebar.visible is True
        # Ahead of the state frame, which names it by id only.
        upserts = [
            f.element.id
            for f in since(session, furnished)
            if isinstance(f, ElementUpsert)
        ]
        assert upserts == [eid("e9")]

    async def test_preview_resolves_against_the_panels_own_elements(self, session):
        assert (
            apply_user_op(session, op(session, op="preview", element_id=rid("e2")))
            is True
        )
        assert [e.id for e in session.sidebar.slot("preview").elements] == [rid("e2")]

    async def test_a_second_preview_releases_what_it_displaced(self, session):
        """But only when nothing else is still showing it.

        The panel's own element goes, because the panel minted it and this
        was the last slot holding it; a feed element would not, which is the
        case below.
        """
        apply_user_op(session, op(session, op="preview", element_id=rid("e1")))
        # "cards" is closable, so e1 is now shown nowhere but the preview.
        apply_user_op(session, op(session, op="close", slot="cards"))
        start = mark(session)

        apply_user_op(session, op(session, op="preview", element_id=rid("e2")))

        removed = [f.id for f in since(session, start) if isinstance(f, ElementRemove)]
        assert removed == [rid("e1")]

    async def test_a_second_preview_leaves_a_feeds_element_alone(self, session):
        from chainlit.protocol.payloads import Step, TextElement
        from chainlit.ws.session import TranscriptEntry

        session.transcript.append(
            TranscriptEntry(
                step=Step(id="m1", type="assistant_message"),
                elements=[TextElement(id=eid("e9"), name="the report", for_id="m1")],
            )
        )
        apply_user_op(session, op(session, op="preview", element_id=eid("e9")))
        start = mark(session)

        apply_user_op(session, op(session, op="preview", element_id=eid("e1")))

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
                elements=[TextElement(id=eid("e9"), name="the report", for_id="m1")],
            )
        )
        apply_user_op(session, op(session, op="preview", element_id=eid("e9")))
        start = mark(session)

        apply_user_op(session, op(session, op="close", slot="preview"))

        assert not [f for f in since(session, start) if isinstance(f, ElementRemove)]


# --------------------------------------------------------------------------
# Rows: the panel as something the thread remembers
# --------------------------------------------------------------------------


class TestRows:
    """A slot's contents are rows, and the panel's own record is one patch.

    The whole of PR-2: restoring the panel after a cold resume is the
    engine's job. Before it, the consumer kept a recipe in ``user_session``
    and replayed it in ``on_chat_resume`` -- which is an application being
    made to remember what the server forgot.
    """

    async def test_a_slots_elements_are_rows_that_hang_off_no_step(self, ctx, session):
        writer = writer_for(session)

        await Sidebar.set_slot("cards", [text("a", eid("e1")), text("b", eid("e2"))])

        written = rows(writer)
        assert sorted(op.record.id for op in written) == sorted([rid("e1"), rid("e2")])
        # NULL, not "": ``forId`` is a uuid column, and NULL is what the cold
        # resume recognises a panel row by.
        assert {op.record.for_id for op in written} == {None}
        assert session.transcript == []

    async def test_a_feed_element_put_in_a_tab_keeps_its_step(self, ctx, session):
        """Its row stays attached to the message it came from.

        Detaching it would take the attachment out of the feed on the next
        cold resume, and the resume looks the tab's ids up over every row of
        the thread, so the tab finds it either way.
        """
        writer = writer_for(session)
        step_id = str(uuid.uuid4())
        attached = text("a", eid("e1"))
        await attached.send(for_id=step_id)

        await Sidebar.set_slot("cards", [attached])

        # One row: a ``cl.Text`` is not updatable, so the second ``send`` does
        # not rewrite it -- and the one row it has keeps the step.
        written = rows(writer)
        assert [op.record.for_id for op in written] == [step_id]
        [payload] = session.sidebar.slots[0].elements
        assert payload.for_id == step_id

    async def test_the_patch_follows_the_rows_it_names(self, ctx, session):
        """Order is the whole of it: the queue is FIFO and the writer batches.

        A stored frame naming an element whose row is still behind it in the
        queue rebuilds a panel with a hole in it.
        """
        writer = writer_for(session)

        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        await Sidebar.set_slot("cards", [text("c", eid("e3"))])

        kinds = [type(op).__name__ for op in writer.held]
        assert kinds == [
            "SaveElement",
            "PatchThread",
            "SaveElement",
            "DeleteElement",
            "PatchThread",
        ]
        assert deletions(writer) == [rid("e1")]
        assert patches(writer)[-1]["slots"][0]["elementIds"] == [rid("e3")]

    async def test_the_engine_mints_the_row_id_from_the_thread_and_the_name(
        self, ctx, session
    ):
        """``elements.id`` is the table's only key: an application's stable
        name -- ``"cards"`` -- must not be one row for every user of the
        deployment. The application never learns there was a rule."""
        writer = writer_for(session)
        card = text("a", "cards")

        await Sidebar.set_slot("cards", [card])

        assert card.id == row_id("test_thread_id", "cards")
        uuid.UUID(card.id)
        assert [op.record.id for op in rows(writer)] == [card.id]
        assert card.id != row_id("another-thread", "cards")

    async def test_a_minted_id_is_stable_and_never_minted_twice(self, ctx, session):
        """The same name on the next call is the same row, so the refresh
        updates in place; and the object already sent is left under its row
        id rather than minted from it into a fresh one."""
        first = text("a", "cards")
        await Sidebar.set_slot("cards", [first])
        start = mark(session)

        again = text("b", "cards")
        await Sidebar.set_slot("cards", [again])
        await Sidebar.set_slot("cards", [again])

        assert again.id == first.id
        assert not [f for f in since(session, start) if isinstance(f, ElementRemove)]

    async def test_a_feed_element_keeps_its_own_id(self, ctx, session):
        attached = text("a", eid("e1"))
        await attached.send(for_id="m1")

        await Sidebar.set_slot("cards", [attached])

        assert attached.id == eid("e1")

    async def test_a_throwaway_element_keeps_its_own_id(self, ctx, session):
        loader = text("a", "loader")
        await Sidebar.set_slot("files", [loader], persist=False)
        assert loader.id == "loader"

    async def test_an_element_naming_a_blob_of_its_own_is_refused(self, ctx, session):
        """The panel deletes its rows when a tab closes, and a deleted row
        discards the blob its key names -- which would be the application's
        file. ``url=`` is how a file the application owns goes in."""
        writer = writer_for(session)
        pointer = File(name="report.pdf", url="/files/report.pdf")
        pointer.object_key = "reports/thread/report.pdf"
        start = mark(session)

        with pytest.raises(ValueError, match="blob"):
            await Sidebar.set_slot("report", [pointer])

        assert since(session, start) == []
        assert writer.held == ()
        assert session.sidebar.slots == []

    async def test_a_throwaway_slot_is_not_in_the_record(self, ctx, session):
        """It has no rows, so a record naming it would name a tab with
        nothing behind it -- and a cold resume would look for one."""
        writer = writer_for(session)
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        await Sidebar.set_slot("loader", [text("b", "spinner")], persist=False)

        assert [s["id"] for s in patches(writer)[-1]["slots"]] == ["cards"]
        assert [slot.persisted for slot in session.sidebar.slots] == [True, False]

    async def test_closing_a_slot_deletes_the_rows_it_drops(self, ctx, session):
        writer = writer_for(session)
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])

        await Sidebar.close_slot("cards")

        assert deletions(writer) == [rid("e1")]
        assert patches(writer)[-1].get("slots", []) == []

    async def test_clear_deletes_every_row(self, ctx, session):
        writer = writer_for(session)
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        await Sidebar.set_slot("notes", [text("b", eid("e2"))])

        await Sidebar.clear()

        assert sorted(deletions(writer)) == sorted([rid("e1"), rid("e2")])

    async def test_an_element_another_slot_still_holds_keeps_its_row(
        self, ctx, session
    ):
        """``orphaned`` gates the delete exactly as it gates the remove.

        A row deleted while another tab is showing the element is a card that
        vanishes from the panel on the next cold resume.
        """
        writer = writer_for(session)
        await Sidebar.set_slot("left", [text("shared", eid("e1"))])
        await Sidebar.set_slot("right", [text("shared", eid("e1"))])

        await Sidebar.close_slot("right")

        assert deletions(writer) == []

    async def test_a_throwaway_elements_id_is_never_submitted_for_deletion(
        self, ctx, session
    ):
        """It has no row, and ``to_uuid`` would fail the whole batch on it."""
        writer = writer_for(session)
        await Sidebar.set_slot("files", [text("a", "loader")], persist=False)
        start = mark(session)

        await Sidebar.close_slot("files")

        assert [
            f.id for f in since(session, start) if isinstance(f, ElementRemove)
        ] == ["loader"]
        assert deletions(writer) == []

    async def test_a_scalar_move_costs_no_write(self, ctx, session):
        """A click must not cost a database write; a lost ``visible`` costs
        the user one chevron."""
        writer = writer_for(session)
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        before = len(writer.held)

        await Sidebar.hide()
        await Sidebar.show()
        await Sidebar.activate("cards")

        assert len(writer.held) == before

    async def test_the_user_closing_a_tab_is_written_down(self, ctx, session):
        """Their move, and it must not be back tomorrow."""
        writer = writer_for(session)
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        await Sidebar.set_slot("notes", [text("b", eid("e2"))])
        before = len(writer.held)

        apply_user_op(
            session, SidebarUser(op="close", slot="cards", rev=session.sidebar.rev)
        )

        issued = list(writer.held)[before:]
        assert [type(op).__name__ for op in issued] == ["DeleteElement", "PatchThread"]
        assert issued[0].element_id == rid("e1")
        assert [
            s["id"] for s in issued[1].patch.metadata[SIDEBAR_META_KEY]["slots"]
        ] == ["notes"]

    async def test_a_scalar_user_op_is_not_written_down(self, ctx, session):
        writer = writer_for(session)
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])
        before = len(writer.held)

        for op_name in ("hide", "show", "activate"):
            apply_user_op(
                session,
                SidebarUser(op=op_name, slot="cards", rev=session.sidebar.rev),
            )

        assert len(writer.held) == before

    async def test_a_preview_of_a_feed_element_keeps_its_row(self, ctx, session):
        """The panel is only borrowing it; the message it hangs off owns it."""
        from chainlit.protocol.payloads import Step, TextElement
        from chainlit.ws.session import TranscriptEntry

        writer = writer_for(session)
        session.transcript.append(
            TranscriptEntry(
                step=Step(id="m1", type="assistant_message"),
                elements=[TextElement(id=eid("e9"), name="report", for_id="m1")],
            )
        )
        apply_user_op(session, SidebarUser(op="preview", element_id=eid("e9"), rev=0))
        before = len(writer.held)

        apply_user_op(
            session, SidebarUser(op="close", slot="preview", rev=session.sidebar.rev)
        )

        assert deletions(writer) == []
        # The preview is not part of what the thread remembers, so closing it
        # writes a patch that says nothing new -- but it must not have
        # deleted a row on the way.
        assert [type(op).__name__ for op in list(writer.held)[before:]] == [
            "PatchThread"
        ]


# --------------------------------------------------------------------------
# The stored frame, and reading it back
# --------------------------------------------------------------------------


class TestMeta:
    async def test_the_meta_is_the_frame_without_rev_or_preview(self, ctx, session):
        from chainlit.protocol.payloads import Step, TextElement
        from chainlit.ws.session import TranscriptEntry

        await Sidebar.set_slot("cards", [text("a", eid("e1"))], title="Cards")
        session.transcript.append(
            TranscriptEntry(
                step=Step(id="m1", type="assistant_message"),
                elements=[TextElement(id=eid("e9"), name="report", for_id="m1")],
            )
        )
        apply_user_op(session, SidebarUser(op="preview", element_id=eid("e9"), rev=0))

        meta = sidebar_meta(session.sidebar)

        assert [slot["id"] for slot in meta["slots"]] == ["cards"]
        assert meta["slots"][0]["elementIds"] == [rid("e1")]
        # ``rev`` counts a session's mutations and restarts at 0 in the next
        # one; stored, it would make the first click on a resumed panel look
        # stale and be answered with a redraw.
        assert "rev" not in meta
        # Ids only: the rows are the props, and a copy of them here would be
        # a second truth that goes stale on the first ``updateElement``.
        assert set(meta["slots"][0]) <= {
            "id",
            "title",
            "elementIds",
            "closable",
            "canvas",
        }

    async def test_the_meta_reads_back_into_the_panel_it_came_from(self, ctx, session):
        await Sidebar.set_slot(
            "cards", [text("a", eid("e1")), text("b", eid("e2"))], title="Cards"
        )
        await Sidebar.set_slot("notes", [text("c", eid("e3"))], activate=False)
        await Sidebar.hide()
        meta = sidebar_meta(session.sidebar)
        elements = [e for slot in session.sidebar.slots for e in slot.elements]

        rebuilt = state_from_meta(meta, elements)

        assert [slot.id for slot in rebuilt.slots] == ["cards", "notes"]
        assert [e.id for e in rebuilt.slots[0].elements] == [rid("e1"), rid("e2")]
        assert rebuilt.slots[0].title == "Cards"
        assert rebuilt.active == session.sidebar.active
        assert rebuilt.visible is False
        # A new session's panel, whatever the old one counted to.
        assert rebuilt.rev == 0

    def test_a_slot_whose_rows_are_gone_is_dropped(self):
        from chainlit.protocol.payloads import TextElement

        meta = {
            "slots": [
                {"id": "cards", "elementIds": [eid("e1")]},
                {"id": "ghost", "elementIds": [eid("gone")]},
            ],
            "active": "ghost",
            "visible": True,
        }

        rebuilt = state_from_meta(meta, [TextElement(id=eid("e1"), name="a")])

        # An empty tab is a promise of content that is not coming, and
        # ``active`` is settled onto what did come back.
        assert [slot.id for slot in rebuilt.slots] == ["cards"]
        assert rebuilt.active == "cards"
        assert rebuilt.visible is True

    def test_a_row_the_meta_does_not_name_is_ignored(self):
        """A ``forId NULL`` row written before this feature, or by a slot
        that has since been closed, is not a tab."""
        from chainlit.protocol.payloads import TextElement

        rebuilt = state_from_meta(
            {"slots": [{"id": "cards", "elementIds": [eid("e1")]}]},
            [
                TextElement(id=eid("e1"), name="a"),
                TextElement(id=eid("stray"), name="b"),
            ],
        )

        assert [e.id for e in rebuilt.slots[0].elements] == [eid("e1")]

    def test_no_meta_is_an_empty_panel(self):
        from chainlit.protocol.payloads import TextElement

        assert state_from_meta(None, [TextElement(id=eid("e1"), name="a")]).slots == []
        assert state_from_meta({}, []).slots == []

    def test_a_record_that_does_not_parse_is_an_empty_panel(self, caplog):
        """A hand-edited row must not make the thread unresumable: the
        rebuild runs inside the handshake, and an exception there is a
        thread nobody can open again until somebody fixes the database."""
        from chainlit.protocol.payloads import TextElement

        rows = [TextElement(id=eid("e1"), name="a")]
        for broken in ({"slots": "cards"}, {"slots": [{"title": 1}]}, {"slots": 7}):
            rebuilt = state_from_meta(broken, rows)
            assert rebuilt.slots == []
            assert rebuilt.visible is False
        assert "does not parse" in caplog.text

    def test_an_empty_panel_reads_back_empty_and_away(self):
        from chainlit.ws.sidebar import SidebarState as SidebarStateModel

        meta = sidebar_meta(SidebarStateModel())
        rebuilt = state_from_meta(meta, [])
        assert rebuilt.slots == []
        assert rebuilt.active is None
        assert rebuilt.visible is False

    async def test_an_application_cannot_shadow_the_panels_record(self, ctx, session):
        """``__sidebar`` is the thread's key, not ``user_session``'s.

        It is put in after the volatile keys are filtered out, so an
        application that writes the name -- by accident or by reading this
        test -- does not get to decide what the panel was.
        """
        from chainlit import persist

        session.state[SIDEBAR_META_KEY] = "junk the application wrote"
        await Sidebar.set_slot("cards", [text("a", eid("e1"))])

        stored = persist.thread_state(session)

        assert stored[SIDEBAR_META_KEY]["slots"][0]["elementIds"] == [rid("e1")]
