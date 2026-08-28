"""``cl.chat_context`` lives on the session's state, one list per session."""

import pytest_asyncio

from chainlit.chat_context import MESSAGES_KEY, chat_context
from chainlit.message import Message
from tests.conftest import bind_context


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


class TestChatContext:
    async def test_get_on_a_fresh_session(self, ctx, session):
        assert chat_context.get() == []
        assert session.state[MESSAGES_KEY] == []

    async def test_get_returns_a_copy(self, ctx):
        msg = Message(content="a")
        chat_context.add(msg)
        listing = chat_context.get()
        listing.clear()
        assert chat_context.get() == [msg]

    async def test_add_returns_the_message_and_dedupes(self, ctx):
        msg = Message(content="a")
        assert chat_context.add(msg) is msg
        chat_context.add(msg)
        assert chat_context.get() == [msg]

    async def test_add_keeps_order(self, ctx):
        first, second = Message(content="1"), Message(content="2")
        chat_context.add(first)
        chat_context.add(second)
        assert chat_context.get() == [first, second]

    async def test_remove(self, ctx):
        msg = Message(content="a")
        assert chat_context.remove(msg) is False
        chat_context.add(msg)
        assert chat_context.remove(msg) is True
        assert chat_context.get() == []

    async def test_clear(self, ctx, session):
        chat_context.add(Message(content="a"))
        chat_context.clear()
        assert chat_context.get() == []
        assert session.state[MESSAGES_KEY] == []

    async def test_to_openai_maps_types_to_roles(self, ctx):
        chat_context.add(Message(content="hi", type="user_message"))
        chat_context.add(Message(content="hello", type="assistant_message"))
        chat_context.add(Message(content="sys", type="system_message"))
        chat_context.add(Message(content="tool", type="tool"))
        assert chat_context.to_openai() == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "system", "content": "sys"},
            {"role": "system", "content": "tool"},
        ]

    async def test_to_openai_with_empty_context(self, ctx):
        assert chat_context.to_openai() == []


class TestChatContextIsolation:
    async def test_two_sessions_do_not_share_messages(self, session_factory):
        one = session_factory(id="one")
        two = session_factory(id="two")
        async with bind_context(one):
            msg = Message(content="mine")
            chat_context.add(msg)
        async with bind_context(two):
            assert chat_context.get() == []
        async with bind_context(one):
            assert chat_context.get() == [msg]

    async def test_messages_die_with_the_session_state(self, ctx, session):
        chat_context.add(Message(content="a"))
        session.state.clear()
        assert chat_context.get() == []
