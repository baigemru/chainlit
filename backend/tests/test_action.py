import asyncio
import time
import uuid
from typing import Any, List, Tuple
from unittest.mock import ANY, Mock

import msgspec
import pytest
import pytest_asyncio

from chainlit.action import Action
from chainlit.message import AskActionMessage, Message
from chainlit.persistence.records import STEP_ACTIONS_KEY
from chainlit.persistence.writer import PatchStep, SessionWriter, WriterRegistry
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
        assert action.variant == "default"
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

    def test_variant_survives_the_dict_round_trip(self):
        original = Action(name="a", payload={}, label="weight of the box?")
        original.variant = "chip"
        assert original.to_dict()["variant"] == "chip"
        assert Action.from_dict(original.to_dict()) == original

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

    async def test_variant_reaches_the_client(self, ctx, session, frames):
        await Action(name="go", payload={}, variant="primary").send(for_id="m")
        [added] = frames(session, ActionAdd)
        assert added.action.variant == "primary"

    async def test_a_variant_nobody_can_draw_is_refused_on_the_way_out(
        self, ctx, session, frames
    ):
        # Nothing validates the dataclass, so the wire struct is where a
        # typo stops -- loudly, at the send, rather than as a button the
        # client silently draws in the default weight.
        action = Action(name="go", payload={})
        action.variant = "accent"  # type: ignore[assignment]
        with pytest.raises(msgspec.ValidationError):
            await action.send(for_id="m")
        assert not frames(session, ActionAdd)

    async def test_a_clicked_chip_reaches_the_callback_as_a_chip(
        self, ctx, session, frames
    ):
        # The button the client posts back is rebuilt with ``Action(**dict)``
        # (``runner.call_action``), so every key the wire carries has to be a
        # field -- which is the whole reason ``variant`` is declared on the
        # dataclass rather than smuggled through ``to_dict``.
        action = Action(name="weight", payload={"sku": "7"}, variant="chip")
        await action.send(for_id="m")
        [added] = frames(session, ActionAdd)
        posted = msgspec.json.decode(msgspec.json.encode(added.action))
        assert Action(**posted).variant == "chip"

    async def test_a_clicked_chip_with_nothing_to_say_still_reaches_the_callback(
        self, ctx, session, frames
    ):
        # ``omit_defaults`` drops an empty payload from the wire and the
        # client posts back exactly what it received: without a default on
        # ``payload`` the click died as a missing constructor argument.
        action = Action(name="weight", variant="chip")
        await action.send(for_id="m")
        [added] = frames(session, ActionAdd)
        posted = msgspec.json.decode(msgspec.json.encode(added.action))
        assert "payload" not in posted
        rebuilt = Action(**posted)
        assert rebuilt.payload == {}
        assert rebuilt.variant == "chip"

    def test_action_with_special_characters_in_payload(self):
        payload = {"text": "特殊字符 & symbols! 🎉", "nested": {"a": [1, 2]}}
        assert Action(name="s", payload=payload).to_dict()["payload"] == payload


# ------------------------------------------------------ the kept row of buttons


@pytest.fixture
def held_writer(session):
    """An unstarted writer with its gate shut: what it holds is what was written."""
    writer = SessionWriter(
        Mock(),
        session.thread_id,
        registry=WriterRegistry(),
        hold_until_interaction=True,
    )
    session.writer = writer
    return writer


def stored_rows(writer: SessionWriter) -> List[Tuple[str, Any]]:
    return [
        (op.step_id, op.metadata[STEP_ACTIONS_KEY])
        for op in writer.held
        if isinstance(op, PatchStep)
    ]


def kept(session, step_id: str) -> List[str]:
    [entry] = [e for e in session.transcript if e.step.id == step_id]
    return [a.id for a in entry.actions]


class TestTheRowIsKept:
    """A button is held with its step, in memory and in the row.

    The client keeps actions in memory only: a reload used to bring the
    message back and drop its buttons, and a resume from storage never had
    them to begin with.
    """

    async def test_a_message_keeps_its_buttons_with_it(self, ctx, session, held_writer):
        again = Action(name="again", id="a1")
        other = Action(name="other", id="a2")
        msg = Message(content="result", actions=[again, other])
        await msg.send()

        assert kept(session, msg.id) == ["a1", "a2"]
        assert [[a["id"] for a in row] for _, row in stored_rows(held_writer)] == [
            ["a1"],
            ["a1", "a2"],
        ]
        assert {step for step, _ in stored_rows(held_writer)} == {msg.id}

    async def test_a_removed_button_leaves_the_row_and_the_last_one_deletes_it(
        self, ctx, session, held_writer
    ):
        msg = Message(content="result")
        await msg.send()
        button = Action(name="again", id="a1")
        await button.send(for_id=msg.id)
        await button.remove()

        assert kept(session, msg.id) == []
        # ``None``, not ``[]``: the key goes, and the row reads back as one
        # that never had buttons.
        assert stored_rows(held_writer)[-1] == (msg.id, None)

    async def test_a_button_sent_under_another_step_moves(
        self, ctx, session, held_writer
    ):
        first = Message(content="one")
        second = Message(content="two")
        await first.send()
        await second.send()
        button = Action(name="go", id="a1")
        await button.send(for_id=first.id)
        await button.send(for_id=second.id)

        assert kept(session, first.id) == []
        assert kept(session, second.id) == ["a1"]
        assert stored_rows(held_writer)[-2:] in (
            [(first.id, None), (second.id, [ANY])],
            [(second.id, [ANY]), (first.id, None)],
        )

    async def test_a_deleted_message_takes_its_buttons_off_the_replay(
        self, ctx, session
    ):
        msg = Message(content="result", actions=[Action(name="again", id="a1")])
        await msg.send()
        await msg.remove()
        assert all(entry.step.id != msg.id for entry in session.transcript)

    async def test_the_asks_buttons_are_the_questions_not_the_steps(
        self, ctx, session, held_writer
    ):
        """They come back with the question, or not at all once it is answered.

        A kept one would outlive the ask as a button wired to nothing -- and
        be sent twice on every reconnect while the question is open.
        """
        action = Action(name="yes", id="a1")
        ask = asyncio.ensure_future(
            AskActionMessage(content="?", actions=[action]).send()
        )
        deadline = time.monotonic() + 5.0
        while session.pending_ask is None:
            assert time.monotonic() < deadline, "no ask was sent"
            await asyncio.sleep(0.001)
        step_id = session.pending_ask.step_id

        assert kept(session, step_id) == []
        assert [a.id for a in session.pending_ask.restore_actions] == ["a1"]
        assert frames_of(session, ActionAdd)[-1].action.for_id == step_id

        session.pending_ask.future.set_result({"id": "a1", "name": "yes"})
        await ask
        assert stored_rows(held_writer) == []

    async def test_a_click_leaves_the_row_as_it_was(self, session_factory):
        """The callback runs; the button stays until the application removes it."""
        from chainlit.runner import ApplicationRunner

        session = session_factory()
        async with bind_context(session):
            msg = Message(content="result", actions=[Action(name="again", id="a1")])
            await msg.send()

        clicked: List[str] = []

        async def on_again(action: Action) -> None:
            clicked.append(action.id)

        runner = Mock(spec=ApplicationRunner)
        runner.code = Mock(action_callbacks={"again": on_again})
        runner._bind = lambda s: None
        await ApplicationRunner.call_action(
            runner, session, {"name": "again", "id": "a1", "forId": msg.id}
        )

        assert clicked == ["a1"]
        assert kept(session, msg.id) == ["a1"]


def frames_of(session, kind):
    return [f for f in session.outbound.pending_frames if isinstance(f, kind)]
