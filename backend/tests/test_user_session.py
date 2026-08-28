"""``cl.user_session`` is a view over ``Session.state``."""

import pytest_asyncio

from chainlit.user_session import user_session
from tests.conftest import bind_context


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


async def test_set_get_and_default(ctx, session):
    user_session.set("test_key", "test_value")
    assert user_session.get("test_key") == "test_value"
    assert user_session.get("non_existent_key", "default") == "default"
    # The dict is the session's own, which is what persistence snapshots.
    assert session.state["test_key"] == "test_value"


async def test_session_fields_are_mirrored(ctx, session, persisted_test_user):
    session.chat_profile = "gpt"
    assert user_session.get("id") == "test_session_id"
    assert user_session.get("env") == {"test_env": "value"}
    assert user_session.get("user") is persisted_test_user
    assert user_session.get("chat_profile") == "gpt"
    assert user_session.get("client_type") == "webapp"


async def test_state_written_elsewhere_is_visible(ctx, session):
    """What the runner parks (``transit_message``) is what the app reads."""
    session.state["transit_message"] = {"from": "profile-a"}
    assert user_session.get("transit_message") == {"from": "profile-a"}


async def test_accessor(ctx):
    counter = user_session.create_accessor("counter", 0, apply_fn=lambda x: x + 1)
    assert counter.get() == 0
    assert counter.apply() == 1
    assert counter.apply() == 2
    counter.set(10)
    assert counter.get() == 10
    counter.reset()
    assert counter.get() == 0


async def test_two_sessions_are_isolated(session_factory):
    one = session_factory(id="one")
    two = session_factory(id="two")
    async with bind_context(one):
        user_session.set("k", "one")
    async with bind_context(two):
        assert user_session.get("k") is None
