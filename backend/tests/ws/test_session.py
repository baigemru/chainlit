"""The session's copy of an element, and who it says is holding one.

Both questions are about the two places one element can live at once -- the
transcript entry it hangs off, and a panel slot showing it. Everything that
writes an element used to write only one of them, so the tests here are
written as pairs: whatever holds for the transcript has to hold for a slot.
"""

from typing import Any

import msgspec

from chainlit.protocol.payloads import Element, Step
from chainlit.ws.session import TranscriptEntry
from chainlit.ws.sidebar import SidebarSlot


def element(element_id: str, **fields: Any) -> Element:
    return msgspec.convert(
        {"id": element_id, "type": "custom", "name": "card", **fields}, Element
    )


def entry(step_id: str, *elements: Element) -> TranscriptEntry:
    return TranscriptEntry(
        step=msgspec.convert({"id": step_id, "type": "assistant_message"}, Step),
        elements=list(elements),
    )


class TestRememberElement:
    def test_it_replaces_the_copy_hanging_off_a_step(self, session) -> None:
        session.transcript.append(entry("s1", element("e1", props={"a": 1})))

        session.remember_element(element("e1", props={"a": 2}))

        assert [e.props for e in session.transcript[0].elements] == [{"a": 2}]

    def test_it_replaces_the_copy_a_slot_is_showing(self, session) -> None:
        """The half no writer ever touched: a slot element hangs off no step.

        ``Emitter.send_element`` attached by ``forId`` and nothing else, so
        the panel went on replaying the props it was first sent.
        """
        session.sidebar.slots.append(
            SidebarSlot(id="cards", elements=[element("e1", props={"a": 1})])
        )

        session.remember_element(element("e1", props={"a": 2}))

        assert [e.props for e in session.sidebar.slots[0].elements] == [{"a": 2}]

    def test_it_replaces_every_copy_at_once(self, session) -> None:
        """One element, two holders -- a previewed feed element is exactly that."""
        session.transcript.append(entry("s1", element("e1", props={"a": 1})))
        session.sidebar.slots.append(
            SidebarSlot(id="preview", elements=[element("e1", props={"a": 1})])
        )

        session.remember_element(element("e1", props={"a": 2}))

        assert session.transcript[0].elements[0].props == {"a": 2}
        assert session.sidebar.slots[0].elements[0].props == {"a": 2}

    def test_it_leaves_the_elements_it_does_not_name_alone(self, session) -> None:
        session.transcript.append(
            entry("s1", element("e1", props={"a": 1}), element("e2", props={"b": 1}))
        )

        session.remember_element(element("e1", props={"a": 2}))

        assert [(e.id, e.props) for e in session.transcript[0].elements] == [
            ("e1", {"a": 2}),
            ("e2", {"b": 1}),
        ]

    def test_an_element_nothing_holds_is_not_attached(self, session) -> None:
        """Not an attach. Where a new element goes is ``forId``'s business.

        An element added here would be replayed under a step it was never
        sent with -- and a write-back arriving for an element the session
        had already dropped would put it back on screen.
        """
        session.transcript.append(entry("s1", element("e1")))
        session.sidebar.slots.append(SidebarSlot(id="cards", elements=[element("e2")]))

        session.remember_element(element("e9", props={"a": 1}))

        assert [e.id for e in session.transcript[0].elements] == ["e1"]
        assert [e.id for e in session.sidebar.slots[0].elements] == ["e2"]


class TestHoldsElement:
    def test_a_step_attachment_counts(self, session) -> None:
        session.transcript.append(entry("s1", element("e1")))

        assert session.holds_element("e1")

    def test_a_slot_element_counts(self, session) -> None:
        session.sidebar.slots.append(SidebarSlot(id="cards", elements=[element("e1")]))

        assert session.holds_element("e1")

    def test_an_id_the_session_never_showed_does_not(self, session) -> None:
        session.transcript.append(entry("s1", element("e1")))

        assert not session.holds_element("e9")

    def test_an_empty_id_is_not_a_match(self, session) -> None:
        """A payload with no id must not be authorised by an empty string."""
        session.transcript.append(entry("s1", element("")))

        assert not session.holds_element("")


class TestFindElement:
    def test_the_transcript_is_asked_first(self, session) -> None:
        session.transcript.append(entry("s1", element("e1", props={"where": "feed"})))
        session.sidebar.slots.append(
            SidebarSlot(id="cards", elements=[element("e1", props={"where": "tab"})])
        )

        assert session.find_element("e1").props == {"where": "feed"}

    def test_every_id_shown_is_held(self, session) -> None:
        session.transcript.append(entry("s1", element("e1")))
        session.sidebar.slots.append(SidebarSlot(id="cards", elements=[element("e2")]))

        assert session.held_element_ids() == {"e1", "e2"}


class TestElementWritten:
    def test_a_feed_element_has_a_row(self, session) -> None:
        session.transcript.append(entry("s1", element("e1")))
        assert session.element_written("e1")

    def test_a_persisted_slots_element_has_a_row(self, session) -> None:
        session.sidebar.slots.append(SidebarSlot(id="cards", elements=[element("e1")]))
        assert session.element_written("e1")

    def test_a_throwaway_slots_element_has_none(self, session) -> None:
        """The route must not write one for it: the row would bring a loader
        back on a cold resume the slot itself never survives."""
        session.sidebar.slots.append(
            SidebarSlot(id="loader", elements=[element("e1")], persisted=False)
        )
        assert session.holds_element("e1")
        assert not session.element_written("e1")


class TestForgetElement:
    def test_it_drops_the_copy_hanging_off_a_step(self, session) -> None:
        session.transcript.append(entry("s1", element("e1"), element("e2")))

        session.forget_element("e1")

        assert [e.id for e in session.transcript[0].elements] == ["e2"]

    def test_it_drops_the_copy_a_slot_is_showing_and_says_so(self, session) -> None:
        """The panel half: the tab loses the card, the frame goes out, and
        the record is written down -- a reload and a cold resume must agree
        about a card the user deleted."""
        session.sidebar.slots.append(
            SidebarSlot(id="cards", elements=[element("e1"), element("e2")])
        )
        before = session.sidebar.rev
        start = len(session.outbound.pending_frames)

        session.forget_element("e1")

        assert [e.id for e in session.sidebar.slots[0].elements] == ["e2"]
        assert session.sidebar.rev > before
        assert [type(f).__name__ for f in session.outbound.pending_frames[start:]] == [
            "SidebarState"
        ]

    def test_a_tab_left_empty_closes(self, session) -> None:
        session.sidebar.slots.append(SidebarSlot(id="cards", elements=[element("e1")]))
        session.sidebar.active = "cards"
        session.sidebar.visible = True

        session.forget_element("e1")

        assert session.sidebar.slots == []
        assert session.sidebar.visible is False

    def test_an_element_nobody_holds_moves_nothing(self, session) -> None:
        start = len(session.outbound.pending_frames)
        session.forget_element("ghost")
        assert list(session.outbound.pending_frames[start:]) == []
