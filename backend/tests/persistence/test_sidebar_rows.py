"""The element panel, through the real tables and back.

``tests/test_sidebar.py`` pins what the panel *submits*; these pin what the
database does with it. Two things only PostgreSQL can answer: that an
element with no step is a legal row -- ``forId`` is a ``uuid`` column and
NULL is the one value that means "hangs off nothing" -- and that the panel's
own record survives a merge with whatever else the thread's metadata is
carrying, which is the whole reason it is one key and not a column.
"""

import msgspec
import pytest

from chainlit.persistence import Persistence, UnitOfWork
from chainlit.persistence.records import ElementRecord, ThreadPatch
from chainlit.persistence.writer import (
    DeleteElement,
    PatchThread,
    SaveElement,
    SessionWriter,
    WriterRegistry,
)
from chainlit.protocol.payloads import CustomElement, Element
from chainlit.ws.sidebar import (
    SIDEBAR_META_KEY,
    SidebarSlot,
    SidebarState,
    sidebar_meta,
    state_from_meta,
)

from .conftest import make_thread, new_id


@pytest.fixture
def thread_id() -> str:
    return new_id()


def _present(record):
    return {k: v for k, v in msgspec.to_builtins(record).items() if v is not None}


def card(thread_id: str, element_id: str, **props) -> ElementRecord:
    """A panel element: a custom element with props and no step."""
    return ElementRecord(
        id=element_id,
        name="Cards",
        type="custom",
        thread_id=thread_id,
        props=props or {"n": 1},
        # NULL, not "": ``forId`` is a uuid column, and this element hangs
        # off no step at all.
        for_id=None,
    )


async def test_an_element_with_no_step_is_a_row(uow: UnitOfWork, thread_id: str):
    await make_thread(uow, thread_id)
    element_id = new_id()

    await uow.elements.save(card(thread_id, element_id))
    detail = await uow.threads.get_detail(thread_id)
    assert detail is not None

    [row] = detail.elements
    assert row.id == element_id
    # ``None`` and not UNSET: this is the predicate ``runner._resume``
    # splits the thread's elements on.
    assert row.for_id is None
    assert row.props == {"n": 1}


async def test_the_panel_survives_the_round_trip(uow: UnitOfWork, thread_id: str):
    """Meta out, rows out, panel back -- through the tables, not a double."""
    await make_thread(uow, thread_id)
    first, second = new_id(), new_id()
    panel = SidebarState(
        slots=[
            SidebarSlot(
                id="cards",
                title="Shortlist",
                elements=[
                    CustomElement(id=first, name="Cards"),
                    CustomElement(id=second, name="Cards"),
                ],
                canvas=True,
            )
        ],
        active="cards",
        visible=True,
        rev=17,
    )

    await uow.elements.save(card(thread_id, first))
    await uow.elements.save(card(thread_id, second))
    await uow.threads.patch(
        thread_id, ThreadPatch(metadata={SIDEBAR_META_KEY: sidebar_meta(panel)})
    )

    detail = await uow.threads.get_detail(thread_id)

    assert detail is not None
    rows = [row for row in detail.elements if row.for_id is None]
    rebuilt = state_from_meta(
        (detail.metadata or {}).get(SIDEBAR_META_KEY),
        [msgspec.convert(_present(row), Element) for row in rows],
    )

    assert [slot.id for slot in rebuilt.slots] == ["cards"]
    assert [e.id for e in rebuilt.slots[0].elements] == [first, second]
    assert rebuilt.slots[0].title == "Shortlist"
    assert rebuilt.slots[0].canvas is True
    assert rebuilt.active == "cards"
    assert rebuilt.visible is True
    # A stored revision would make the first click on a resumed panel look
    # stale to ``apply_user_op`` and be answered with a redraw.
    assert rebuilt.rev == 0


async def test_the_panels_record_merges_with_the_rest_of_the_metadata(
    uow: UnitOfWork, thread_id: str
):
    """One key, merged -- which is why the immediate patch can be small.

    The application's own ``user_session`` keys live in the same dict, and a
    panel write that replaced it would erase them.
    """
    await make_thread(uow, thread_id, metadata={"counter": 7, "chat_profile": "A"})

    await uow.threads.patch(
        thread_id,
        ThreadPatch(metadata={SIDEBAR_META_KEY: sidebar_meta(SidebarState())}),
    )
    detail = await uow.threads.get_detail(thread_id)
    assert detail is not None
    stored = detail.metadata or {}

    assert stored["counter"] == 7
    assert stored["chat_profile"] == "A"
    assert SIDEBAR_META_KEY in stored


async def test_a_deleted_panel_row_leaves_nothing_behind(
    persistence: Persistence, thread_id: str
):
    """What the panel writes, the panel can take away -- through the writer.

    In issue order, through one queue: the row, the record naming it, the
    delete, the record that no longer does. ``DeleteElement`` on a row with
    no ``objectKey`` deletes nothing in the bucket, which is what makes a
    ``cl.Pdf(url=...)`` in a slot safe to close.

    The thread is created by the writer's own first patch rather than by the
    ``uow`` fixture: that session has not committed, and the element's
    foreign key is checked against what is actually there.
    """
    element_id = new_id()
    writer = SessionWriter(persistence, thread_id, registry=WriterRegistry()).start()
    try:
        writer.submit(PatchThread(thread_id, ThreadPatch(name="a conversation")))
        writer.submit(SaveElement(card(thread_id, element_id)))
        writer.submit(
            PatchThread(
                thread_id,
                ThreadPatch(
                    metadata={
                        SIDEBAR_META_KEY: sidebar_meta(
                            SidebarState(
                                slots=[
                                    SidebarSlot(
                                        id="cards",
                                        elements=[
                                            CustomElement(id=element_id, name="Cards")
                                        ],
                                    )
                                ],
                                active="cards",
                                visible=True,
                            )
                        )
                    }
                ),
            )
        )
        writer.submit(DeleteElement(element_id, thread_id))
        writer.submit(
            PatchThread(
                thread_id,
                ThreadPatch(metadata={SIDEBAR_META_KEY: sidebar_meta(SidebarState())}),
            )
        )
        await writer.drain(timeout=5.0)
    finally:
        await writer.aclose(timeout=5.0)

    async with persistence.uow() as unit:
        detail = await unit.threads.get_detail(thread_id)
        assert detail is not None

    assert detail.elements == []
    # The row is gone and so is the tab that named it: a meta that still
    # named it would rebuild a slot with nothing in it, which
    # ``state_from_meta`` drops -- but the record must not be left lying.
    assert (detail.metadata or {})[SIDEBAR_META_KEY].get("slots", []) == []
