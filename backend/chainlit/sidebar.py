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
from typing import TYPE_CHECKING, List, Literal, Optional, Sequence, Union

import msgspec

# Aliased: ``set_slot(persist=...)`` is a public keyword and would shadow it.
from chainlit import persist as persistence
from chainlit.context import context
from chainlit.element import ElementBased
from chainlit.protocol.payloads import Element as ElementPayload
from chainlit.ws.sidebar import PREVIEW_SLOT, SidebarState, release, row_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chainlit.ws.session import Session

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
        activate: Union[bool, Literal["force"]] = True,
        closable: bool = True,
        canvas: bool = False,
        persist: bool = True,
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

        ``activate`` selects the tab and asks for the panel on screen, on a
        new slot and on a refresh alike -- "bring this to the front" and
        "leave it where nobody can see it" cannot both be true.
        ``activate=False`` fills the slot without taking the screen and
        touches neither.

        *Asks*, because where the panel is a full-screen sheet -- a phone --
        raising it buries the feed, and the feed is where everything that
        wants an answer lives: a question, a running counter, a notice. So
        the client applies the raise or declines it by the screen it is
        actually on, and either way the slot is filled and the tab selected:
        a button in the feed saying "open the shortlist" leads to a ready tab
        rather than an empty one. ``activate="force"`` is for the rare call
        that must be seen wherever it lands and accepts covering the feed to
        do it; ``Sidebar.show()`` is the same thing said on its own.

        The screen is judged in the browser and nowhere else. The server
        never branches on the device a client reported -- that label can be
        pinned against the layout with ``?device=pc``, and a conversation
        must not behave differently depending on what a hello claimed.

        ``id`` may not be ``"preview"``: that address belongs to the user's
        click in the feed, and an application writing into it would erase
        what they were looking at -- and be erased by their next click.

        ``persist`` decides whether the slot's contents are *rows*. They are
        by default, written with ``forId NULL``, which is how the panel comes
        back after the last tab on this conversation was closed a day ago --
        restoring it is the engine's job, not a recipe the application keeps
        and replays in ``on_chat_resume``. ``persist=False`` is for content
        that is not worth a row: a loader, a progress card, anything the next
        call replaces anyway.

        The id the application gives a persisted element is *its* name for
        it, not the row's: the row id is minted from the thread and that
        name, the same on every call (``ws.sidebar.row_id``), and the element
        is sent under it. So ``id="cards"`` is stable, updates in
        place, and is a different row in every conversation -- ``elements.id``
        is the table's only key, and a name shared by every user of a
        deployment would otherwise be one row they all overwrite. An element
        that already hangs off a step keeps the id it has.

        A persisted element may not arrive carrying an ``object_key`` of its
        own: the panel deletes its rows when a tab closes, and deleting a row
        discards the blob its key names. A file the application owns goes in
        by ``url=``, which the row keeps and the delete leaves alone.

        Raises:
            ValueError: ``id`` is the reserved preview address, or a persisted
                element names a blob the panel would delete.
        """
        _refuse_preview(id)
        session = context.session
        sidebar = session.sidebar

        if not elements:
            await Sidebar.close_slot(id)
            return

        if persist:
            _mint_row_ids(session, elements)

        # Sent first, and before the model is touched: ``send`` is what mints
        # the element's key and url, so ``to_dict()`` is only the truth
        # afterwards. An element that already hangs off a step keeps that
        # step: putting a feed element in a tab must not detach its row from
        # the message it came from. One that hangs off nothing is the panel's
        # own, and ``None`` is the persisted spelling of "no step" -- ``forId``
        # is a uuid column and ``""`` does not parse as one; ``""`` stays the
        # throw-away spelling so a ``persist=False`` element is unchanged.
        await asyncio.gather(
            *(
                element.send(
                    for_id=element.for_id or (None if persist else ""),
                    persist=persist,
                )
                for element in elements
            )
        )
        payloads: List[ElementPayload] = [
            msgspec.convert(element.to_dict(), ElementPayload) for element in elements
        ]

        # Decided inside the model, where "is this slot new?" is asked at the
        # moment of the mutation. Asked out here it was read before the
        # ``await`` above and acted on after it -- a ``sidebar.user close``
        # arriving in that window answered the question for us. What the
        # displaced elements *were* -- rows or not -- is the slot's answer as
        # it stood before the mutation, and it is read in the same breath.
        existing = sidebar.slot(id)
        had_rows = existing.persisted if existing is not None else persist
        dropped = sidebar.set_slot(
            id,
            payloads,
            title=title,
            activate=activate,
            closable=closable,
            canvas=canvas,
            persisted=persist,
        )
        release(session, dropped, rows=had_rows)
        persistence.patch_sidebar(session)
        context.emitter.sidebar_state(force=activate == "force")

    @staticmethod
    async def close_slot(id: str) -> None:
        """Take one tab away. The panel closes with its last one.

        Raises:
            ValueError: ``id`` is the reserved preview address.
        """
        _refuse_preview(id)
        session = context.session
        _close(session, id)
        persistence.patch_sidebar(session)
        context.emitter.sidebar_state()

    @staticmethod
    async def activate(id: str) -> None:
        """Bring a slot to the front. Unknown ids are left unsaid."""
        context.session.sidebar.activate(id)
        context.emitter.sidebar_state()

    @staticmethod
    async def show() -> None:
        """Put the panel on screen, with or without anything in it.

        Unconditional, unlike ``set_slot(activate=True)``: this call has no
        other purpose, so there is nothing for the screen to weigh it
        against, and it is honoured on a phone too -- over the feed.
        """
        context.session.sidebar.show()
        context.emitter.sidebar_state(force=True)

    @staticmethod
    async def hide() -> None:
        """Put the panel away. The slots and their contents stay."""
        context.session.sidebar.hide()
        context.emitter.sidebar_state()

    @staticmethod
    async def clear() -> None:
        """Close every slot, and put the panel away with them."""
        session = context.session
        # One tab at a time, so each slot's leavings are let go with that
        # slot's own answer to "were these rows"; the model's ``clear`` would
        # hand back one list with the answer already lost. It is still called,
        # for the panel itself: an empty panel the chevron opened is on screen
        # with no tab to close, and ``clear`` means "away" even then.
        for slot in list(session.sidebar.slots):
            _close(session, slot.id)
        session.sidebar.clear()
        persistence.patch_sidebar(session)
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


def _close(session: Session, slot_id: str) -> None:
    """Drop one slot and let its elements go, rows and all if it had rows."""
    slot = session.sidebar.slot(slot_id)
    if slot is None:
        return
    release(session, session.sidebar.close_slot(slot_id), rows=slot.persisted)


def _mint_row_ids(session: Session, elements: Sequence[ElementBased]) -> None:
    """Give each new element of a persisted slot the id its row will have.

    Minted once: an element the panel is already showing is under its row id
    and is left alone (minting again would mint a *different* id from the
    minted one, and every refresh would be unmount-and-mount). An element
    hanging off a step is the feed's and keeps the id its row already has.

    The ``object_key`` refusal lives here because it is the same question --
    "what will this row be" -- asked before anything is sent. The engine's
    own uploads set the key on the *record*, inside the writer, never on the
    element, so this cannot fire on a blob the panel minted itself.
    """
    thread_id = session.thread_id or ""
    already = set(session.sidebar.element_ids())
    for element in elements:
        if element.for_id:
            continue
        if getattr(element, "object_key", None):
            raise ValueError(
                f"element {element.id!r} names a blob ({element.object_key!r}) "
                f"the panel would discard when its tab closes; pass url= for "
                f"a file the application owns, or persist=False"
            )
        if element.id not in already:
            element.id = row_id(thread_id, element.id)
