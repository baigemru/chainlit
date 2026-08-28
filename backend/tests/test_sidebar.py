import pytest_asyncio
from msgspec import UNSET

from chainlit.element import File, Image, Text
from chainlit.protocol.server import ElementUpsert, SidebarSet
from chainlit.sidebar import ElementSidebar
from tests.conftest import bind_context


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


class TestElementSidebar:
    async def test_set_title(self, ctx, session, frames):
        await ElementSidebar.set_title("My Sidebar Title")
        [frame] = frames(session, SidebarSet)
        assert frame.title == "My Sidebar Title"
        # Only the title is stated; the elements are left as they are.
        assert frame.elements is UNSET
        assert frame.key is UNSET

    async def test_set_title_with_empty_string(self, ctx, session, frames):
        await ElementSidebar.set_title("")
        assert frames(session, SidebarSet)[0].title == ""

    async def test_set_elements_sends_each_then_the_set(self, ctx, session, frames):
        elements = [
            File(name="file1.txt", url="https://example.com/file1.txt"),
            Image(
                name="image1.png", url="https://example.com/image1.png", size="large"
            ),
            Text(name="text1", content="Some text content"),
        ]
        await ElementSidebar.set_elements(elements, key="k")
        shown = frames(session, ElementUpsert)
        assert [e.element.name for e in shown] == ["file1.txt", "image1.png", "text1"]
        [frame] = frames(session, SidebarSet)
        assert [e.name for e in frame.elements] == ["file1.txt", "image1.png", "text1"]
        assert frame.elements[1].size == "large"
        assert frame.key == "k"
        assert frame.title is UNSET
        assert isinstance(frames(session)[-1], SidebarSet)

    async def test_set_elements_with_empty_list_closes(self, ctx, session, frames):
        await ElementSidebar.set_elements([])
        [frame] = frames(session, SidebarSet)
        assert frame.elements == []
        assert frame.key is None

    async def test_set_elements_keeps_an_existing_for_id(self, ctx, session, frames):
        element = File(name="t", url="https://example.com/t", for_id="message_123")
        await ElementSidebar.set_elements([element])
        assert frames(session, ElementUpsert)[0].element.for_id == "message_123"

    async def test_set_elements_spools_but_does_not_persist(self, ctx, session, frames):
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
        await ElementSidebar.set_elements([element])
        assert element.chainlit_key in session.files
        assert writer.held == ()
        assert len(frames(session, ElementUpsert)) == 1

    async def test_title_and_elements_together(self, ctx, session, frames):
        await ElementSidebar.set_title("Docs")
        await ElementSidebar.set_elements([File(name="f", url="https://example.com/f")])
        titles = [f.title for f in frames(session, SidebarSet)]
        assert titles == ["Docs", UNSET]
