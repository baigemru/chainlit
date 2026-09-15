"""``cl.Sidebar`` -- the application's half of the element panel.

Every method here is one mutation of ``session.sidebar`` (the model lives in
``chainlit.ws.sidebar``, where the transport can reach it) followed by one
``sidebar.state`` frame built from the model. Nothing in this module decides
what the panel looks like; it decides what the panel *is*, and the frame is
read off afterwards.

The order of the wire traffic is the one thing that is not free to change:
elements go out as ``element.upsert`` **before** the state frame that names
them by id. ``session.send`` is a single FIFO queue, so a slot's contents
have always arrived by the time the client is told which slot they belong to.
The old ``ElementSidebar`` sent them twice -- once as upserts, once inside
the frame -- which is what made a fifty-card composer cost fifty cards every
time a PDF was opened beside it.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional, Sequence

import msgspec

from chainlit.context import context
from chainlit.element import ElementBased
from chainlit.protocol.payloads import Element as ElementPayload
from chainlit.ws.sidebar import PREVIEW_SLOT, SidebarState, orphaned

__all__ = ["Sidebar", "SidebarState"]


class Sidebar:
    """Open, fill, rearrange and put away the element panel.

    Slots are addressed by an id the application chooses, and a slot is one
    tab. The panel survives everything a session survives: a reload replays
    it, hiding it keeps it, and closing one tab leaves the others alone.
    """

    @staticmethod
    async def set_slot(
        id: str,
        elements: Sequence[ElementBased],
        *,
        title: Optional[str] = None,
        activate: bool = True,
        closable: bool = True,
        canvas: bool = False,
    ) -> None:
        """Put ``elements`` in the slot ``id``, creating it if it is new.

        Replacement is **by element identity**. An element whose id is
        already in this slot goes out as ``element.upsert`` and the client
        updates its props in place; an id that is new is mounted; an id that
        is gone is removed. That only works if the application gives its
        elements stable ids -- ``cl.CustomElement(id="cards", ...)``. Without
        one chainlit mints a fresh uuid per call, every id is new every time,
        and "replace" is unmount-and-mount, which throws away whatever state
        the mounted element was holding.

        ``title=None`` keeps the title a slot already has, and is ``""`` on a
        slot being created. An empty ``elements`` is ``close_slot(id)``: an
        empty tab is a promise of content that is not coming.

        ``activate`` selects the tab **and** puts the panel on screen, on a
        new slot and on a refresh alike -- "bring this to the front" and
        "leave it where nobody can see it" cannot both be true, and an
        application refreshing its one long-lived slot has to be able to
        count on the result being seen. ``activate=False`` fills the slot
        without taking the screen and touches neither.

        ``id`` may not be ``"preview"``: that address belongs to the user's
        click in the feed, and an application writing into it would erase
        what they were looking at -- and be erased by their next click.

        Raises:
            ValueError: ``id`` is the reserved preview address.
        """
        _refuse_preview(id)
        session = context.session
        sidebar = session.sidebar

        if not elements:
            await Sidebar.close_slot(id)
            return

        # Sent first, and before the model is touched: ``send`` is what mints
        # the element's key and url, so ``to_dict()`` is only the truth
        # afterwards. ``persist=False`` with an empty ``forId`` keeps them out
        # of the transcript -- the panel is their home, and a step they were
        # never attached to must not replay them.
        await asyncio.gather(
            *(
                element.send(for_id=element.for_id or "", persist=False)
                for element in elements
            )
        )
        payloads: List[ElementPayload] = [
            msgspec.convert(element.to_dict(), ElementPayload) for element in elements
        ]

        # Decided inside the model, where "is this slot new?" is asked at the
        # moment of the mutation. Asked out here it was read before the
        # ``await`` above and acted on after it -- a ``sidebar.user close``
        # arriving in that window answered the question for us.
        dropped = sidebar.set_slot(
            id,
            payloads,
            title=title,
            activate=activate,
            closable=closable,
            canvas=canvas,
        )
        _release(dropped)
        context.emitter.sidebar_state()

    @staticmethod
    async def close_slot(id: str) -> None:
        """Take one tab away. The panel closes with its last one.

        Raises:
            ValueError: ``id`` is the reserved preview address.
        """
        _refuse_preview(id)
        session = context.session
        _release(session.sidebar.close_slot(id))
        context.emitter.sidebar_state()

    @staticmethod
    async def activate(id: str) -> None:
        """Bring a slot to the front. Unknown ids are left unsaid."""
        context.session.sidebar.activate(id)
        context.emitter.sidebar_state()

    @staticmethod
    async def show() -> None:
        """Put the panel on screen, with or without anything in it."""
        context.session.sidebar.show()
        context.emitter.sidebar_state()

    @staticmethod
    async def hide() -> None:
        """Put the panel away. The slots and their contents stay."""
        context.session.sidebar.hide()
        context.emitter.sidebar_state()

    @staticmethod
    async def clear() -> None:
        """Close every slot."""
        _release(context.session.sidebar.clear())
        context.emitter.sidebar_state()

    @staticmethod
    def state() -> SidebarState:
        """The model, live. Synchronous: it is read off the session."""
        return context.session.sidebar


def _refuse_preview(slot_id: str) -> None:
    """Keep the application out of the slot the user's clicks land in."""
    if slot_id == PREVIEW_SLOT:
        raise ValueError(
            f"{PREVIEW_SLOT!r} is the slot a click in the feed opens; "
            f"an application slot needs an address of its own"
        )


def _release(element_ids: Sequence[str]) -> None:
    """Take the panel's own leavings off the client, and nothing else.

    ``orphaned`` is the gate: an element still in another slot, or hanging
    off a step in the transcript, belongs to something that is still showing
    it. A ``preview`` slot holds an element of the *feed*, and removing that
    because the tab went away would blank the attachment in the message it
    came from.
    """
    session = context.session
    for element_id in orphaned(session, element_ids):
        context.emitter.remove_element(element_id)
