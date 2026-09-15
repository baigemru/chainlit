"""The element panel, as state the session owns.

Until this module the panel was not a state at all: the server sent
``sidebar.set`` and forgot it, the browser kept the last frame in an atom,
and the user closing the panel wrote ``undefined`` over it. Everything the
application worked around followed from that -- one slot, "close" meaning
"destroy", nothing surviving a reload, and a ``key`` whose only power was to
forbid updates. So the panel joins the transcript and the pending question:
it lives on the ``Session``, every mutation happens here, and the frame is a
projection of the model rather than the model being whatever was last sent.

Deliberately transport-side, for the same reason ``ws/session.py`` is. The
``cl.Sidebar`` API in ``chainlit/sidebar.py`` reaches for ``chainlit.context``
and through it for the session -- so the model cannot live there without
``ws/session.py`` importing its own importer. What lives here is the model,
its invariants, and what one inbound ``sidebar.user`` frame does to it;
nothing here knows an application exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

import msgspec

from chainlit.protocol.payloads import Element, SidebarSlotRef
from chainlit.protocol.server import (
    ElementRemove,
    ElementUpsert,
    SidebarState as SidebarStateFrame,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chainlit.protocol.client import SidebarUser
    from chainlit.ws.session import Session

__all__ = [
    "PREVIEW_SLOT",
    "SidebarSlot",
    "SidebarState",
    "apply_user_op",
    "orphaned",
    "state_frame",
]

PREVIEW_SLOT = "preview"
"""The slot a click in the feed lands in.

One address, not one per element: a preview is "show me this, now", and a
second click replaces the first rather than growing a row of tabs nobody
asked for.
"""


class SidebarSlot(msgspec.Struct):
    """One tab, with the elements it is showing.

    Holds the wire payloads rather than the ``cl.Element`` objects the
    application built them from: what the panel is showing is what the
    client was sent, and the live objects are free to be garbage by the
    time a reconnect has to say it again.
    """

    id: str
    title: str = ""
    elements: List[Element] = []
    closable: bool = True
    canvas: bool = False


class SidebarState(msgspec.Struct):
    """The whole panel. Empty, hidden and with nothing active is the default.

    Five invariants, and each one is a state the old panel could reach:

    * ``active`` is either ``None`` or the id of a slot that exists -- a tab
      strip pointing at a slot that was closed renders nothing;
    * no slot is empty: ``set_slot(id, [])`` is ``close_slot(id)``, because
      an empty tab is a promise of content that is not coming;
    * the last slot closing puts the panel away, so ``visible`` never
      survives its contents by accident;
    * activating a slot puts the panel on screen: "bring this to the front"
      and "and leave it where nobody can see it" cannot both be true;
    * ``show`` is the one way to an empty visible panel, and it is allowed:
      the composer's chevron opens the panel whether or not anything is in
      it, and a button that sometimes does nothing is worse than an empty
      frame.

    Every mutation goes through a method here rather than through a caller
    that knows better. The one rule that used to live in ``cl.Sidebar`` --
    "a *new* slot opens the panel" -- read the model before an ``await`` and
    acted on it after, so a ``close`` arriving in that window decided it.
    """

    slots: List[SidebarSlot] = []
    active: Optional[str] = None
    visible: bool = False
    #: Bumped by every mutation, and carried in every frame. The client
    #: quotes the last one it saw in each ``sidebar.user``, which is the
    #: only way the server can tell an operation made against the state it
    #: is holding from one made against a state a frame already in flight
    #: has replaced. See ``apply_user_op``.
    rev: int = 0

    # ----------------------------------------------------------- reading

    def slot(self, slot_id: str) -> Optional[SidebarSlot]:
        for slot in self.slots:
            if slot.id == slot_id:
                return slot
        return None

    def element_ids(self) -> List[str]:
        """Every element the panel holds, in slot order, deduplicated."""
        seen: Dict[str, None] = {}
        for slot in self.slots:
            for element in slot.elements:
                seen.setdefault(element.id, None)
        return list(seen)

    # ---------------------------------------------------------- mutating

    def set_slot(
        self,
        slot_id: str,
        elements: Sequence[Element],
        *,
        title: Optional[str] = None,
        activate: bool = True,
        closable: bool = True,
        canvas: bool = False,
    ) -> List[str]:
        """Put ``elements`` in the slot, and name the ones that leave it.

        The returned ids are the caller's cue to send ``element.remove``:
        the model drops them here, and only the caller can put a frame on
        the wire. An empty ``elements`` closes the slot outright.

        ``activate`` selects the slot **and** puts the panel on screen, on a
        slot being created and on one being refreshed alike. The application
        asking for a slot to be at the front is the application asking for
        it to be seen; a refresh that quietly did nothing because the user
        had once put the panel away is the shape of bug nobody reports.
        ``activate=False`` is the way to fill a slot without taking the
        screen, and it touches neither ``active`` nor ``visible``.
        """
        if not elements:
            return self.close_slot(slot_id)

        existing = self.slot(slot_id)
        incoming = {element.id for element in elements}
        removed = (
            [e.id for e in existing.elements if e.id not in incoming]
            if existing
            else []
        )

        if existing is None:
            self.slots.append(
                SidebarSlot(
                    id=slot_id,
                    title=title or "",
                    elements=list(elements),
                    closable=closable,
                    canvas=canvas,
                )
            )
        else:
            existing.elements = list(elements)
            # ``None`` keeps the title: an application refreshing a slot's
            # contents has no opinion about its heading, and making it
            # restate one every time is how the panel ended up titled after
            # whichever file was last sent.
            if title is not None:
                existing.title = title
            existing.closable = closable
            existing.canvas = canvas

        if activate:
            self.active = slot_id
            self.visible = True
        self._settle_active()
        self.rev += 1
        return removed

    def close_slot(self, slot_id: str) -> List[str]:
        """Drop the slot, and name the elements that went with it."""
        index = next(
            (i for i, slot in enumerate(self.slots) if slot.id == slot_id), None
        )
        if index is None:
            return []
        slot = self.slots.pop(index)
        if self.active == slot_id:
            # The neighbour, not the first tab: closing the third of four and
            # landing on the first is the browser's oldest annoyance. The
            # previous one, or the one that slid into this index when there
            # was no previous.
            self.active = (
                self.slots[index - 1].id
                if index > 0
                else (self.slots[0].id if self.slots else None)
            )
        self._settle_active()
        self.rev += 1
        return [element.id for element in slot.elements]

    def clear(self) -> List[str]:
        removed = self.element_ids()
        self.slots = []
        self.active = None
        self.visible = False
        self.rev += 1
        return removed

    def activate(self, slot_id: str) -> bool:
        if self.slot(slot_id) is None:
            return False
        self.active = slot_id
        self.rev += 1
        return True

    def show(self) -> None:
        self.visible = True
        self.rev += 1

    def hide(self) -> None:
        self.visible = False
        self.rev += 1

    def _settle_active(self) -> None:
        """Keep ``active`` pointing at something, and hide an empty panel."""
        if not self.slots:
            self.active = None
            self.visible = False
            return
        if self.slot(self.active or "") is None:
            self.active = self.slots[0].id


def state_frame(sidebar: SidebarState) -> SidebarStateFrame:
    """The panel as the wire says it: slots by reference, elements by id."""
    return SidebarStateFrame(
        slots=[
            SidebarSlotRef(
                id=slot.id,
                title=slot.title,
                element_ids=[element.id for element in slot.elements],
                closable=slot.closable,
                canvas=slot.canvas,
            )
            for slot in sidebar.slots
        ],
        active=sidebar.active,
        visible=sidebar.visible,
        rev=sidebar.rev,
    )


def apply_user_op(session: "Session", message: "SidebarUser") -> bool:
    """Apply one inbound panel operation, and say whether to answer with state.

    The answer rule is the whole of the protocol here. A **scalar** move --
    ``hide``, ``show``, ``activate`` -- the client has already made locally,
    and echoing it would redraw the middle of a fast hide-then-show; it is
    applied in silence. A **structural** move (``close``, ``preview``) and
    every refusal answer with the full state, which is idempotent and
    overwrites whatever the client guessed.

    Silence is conditional on ``rev``, and that is what keeps the two sides
    from parting for good. The client quotes the revision it was last shown;
    if the panel has moved on since -- the application hid it, filled a slot,
    anything -- then the frame saying so is already on its way and the click
    was made against a screen that no longer exists. There is no correcting
    frame after a silent scalar, so that one is answered with the whole state
    instead, which lands *after* the frame in flight and settles it.

    There is no third party to tell: a thread holds exactly one live socket,
    so the sender is the only client there is.
    """
    sidebar = session.sidebar
    op = message.op
    # Read before the mutation: what the client knew when it acted.
    current = message.rev == sidebar.rev

    if op == "hide":
        sidebar.hide()
        return not current
    if op == "show":
        sidebar.show()
        return not current
    if op == "activate":
        # A refusal, not silence: the slot the client thinks it is on does
        # not exist, so it is showing something the server is not.
        return not sidebar.activate(message.slot or "") or not current

    if op == "close":
        slot = sidebar.slot(message.slot or "")
        if slot is None or not slot.closable:
            return True
        for element_id in orphaned(session, sidebar.close_slot(slot.id)):
            session.send(ElementRemove(id=element_id))
        return True

    element = _find_element(session, message.element_id or "")
    if element is None:
        return True
    replaced = sidebar.set_slot(
        PREVIEW_SLOT,
        [element],
        title=element.name,
        closable=True,
    )
    # Through the same gate as ``close``: what a second preview displaces is
    # usually a feed element the panel was only borrowing, and taking that
    # off the client would blank the attachment in the message it hangs off.
    for element_id in orphaned(session, replaced):
        session.send(ElementRemove(id=element_id))
    sidebar.show()
    # Ahead of the state frame, which names the element by id only. The
    # server keeps no model of what the client is holding -- the click
    # implies it holds this one, but an upsert is one small idempotent
    # frame and a dangling id is a hole in the panel.
    session.send(ElementUpsert(element=element))
    return True


def orphaned(session: "Session", element_ids: Sequence[str]) -> List[str]:
    """Of the ids a slot let go, the ones nothing else in the session holds.

    The gate on every ``element.remove`` the panel sends, and it exists
    because the panel does not always own what it shows. A ``preview`` slot
    holds an element of the *feed*; taking that off the client because a tab
    was closed would blank the attachment in the message it belongs to. So
    an id still in another slot, or hanging off a step in the transcript, is
    left alone -- what is removed is only what the panel minted and nothing
    is showing any more.
    """
    held = {
        element.id for slot in session.sidebar.slots for element in slot.elements
    } | {element.id for entry in session.transcript for element in entry.elements}
    return [element_id for element_id in element_ids if element_id not in held]


def _find_element(session: "Session", element_id: str) -> Optional[Element]:
    """The element a ``preview`` names, wherever the session has it.

    The transcript first, which is also the answer for a resumed thread:
    ``ApplicationRunner._resume`` fills ``session.transcript`` from the
    stored thread before the screen is rebuilt, so a stored element hanging
    off a stored message is here like any other. Then the panel's own
    elements, which never enter the transcript (they go out with an empty
    ``forId``). Nothing else is consulted: an id that is in neither is an id
    this conversation never showed, and answering it would let a browser ask
    the database questions through the panel.
    """
    if not element_id:
        return None
    for entry in session.transcript:
        for element in entry.elements:
            if element.id == element_id:
                return element
    for slot in session.sidebar.slots:
        for element in slot.elements:
            if element.id == element_id:
                return element
    return None
