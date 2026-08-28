import uuid

import pytest_asyncio

from chainlit.action import Action
from chainlit.protocol.server import ActionAdd, ActionRemove
from tests.conftest import bind_context


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


class TestAction:
    def test_action_initialization_with_required_fields(self):
        action = Action(name="test_action", payload={"key": "value"})
        assert action.label == ""
        assert action.tooltip == ""
        assert action.icon is None
        assert action.forId is None
        uuid.UUID(action.id)

    def test_action_initialization_with_all_fields(self):
        action = Action(
            name="a",
            payload={},
            label="L",
            tooltip="T",
            icon="star",
            forId="m",
            id="fixed",
        )
        assert (action.label, action.tooltip, action.icon, action.forId, action.id) == (
            "L",
            "T",
            "star",
            "m",
            "fixed",
        )

    def test_action_ids_are_unique(self):
        assert Action(name="a", payload={}).id != Action(name="a", payload={}).id

    def test_action_to_dict(self):
        action = Action(
            name="test_action",
            payload={"data": "test"},
            label="Test Label",
            icon="star",
        )
        action_dict = action.to_dict()
        assert action_dict["name"] == "test_action"
        assert action_dict["payload"] == {"data": "test"}
        assert action_dict["label"] == "Test Label"
        assert action_dict["icon"] == "star"
        assert action_dict["id"] == action.id
        assert action_dict["forId"] is None

    def test_action_serialization_round_trip(self):
        original = Action(name="s", payload={"data": "test"}, label="Test", icon="i")
        assert Action.from_dict(original.to_dict()) == original

    def test_from_dict_ignores_what_the_wire_adds(self):
        assert (
            Action.from_dict({"name": "a", "payload": {}, "id": "x", "kind": "?"}).id
            == "x"
        )

    async def test_action_send_puts_the_action_on_the_wire(self, ctx, session, frames):
        action = Action(name="send_action", payload={"test": "data"}, label="Send Test")
        await action.send(for_id="target_message_id")
        assert action.forId == "target_message_id"
        [added] = frames(session, ActionAdd)
        assert added.action.name == "send_action"
        assert added.action.payload == {"test": "data"}
        assert added.action.label == "Send Test"
        assert added.action.for_id == "target_message_id"

    async def test_action_send_updates_for_id(self, ctx, session, frames):
        action = Action(name="test", payload={})
        await action.send(for_id="first_id")
        await action.send(for_id="second_id")
        assert action.forId == "second_id"
        assert [a.action.for_id for a in frames(session, ActionAdd)] == [
            "first_id",
            "second_id",
        ]

    async def test_action_remove(self, ctx, session, frames):
        action = Action(name="r", payload={}, forId="m")
        await action.remove()
        assert [r.id for r in frames(session, ActionRemove)] == [action.id]

    def test_action_with_special_characters_in_payload(self):
        payload = {"text": "特殊字符 & symbols! 🎉", "nested": {"a": [1, 2]}}
        assert Action(name="s", payload=payload).to_dict()["payload"] == payload
