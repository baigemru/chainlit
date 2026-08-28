"""``cl.Message`` and the ask messages, observed at the wire and the writer.

Every test binds a real ``Session`` and reads the frames it queued; the
persistence tests give the session an unstarted ``SessionWriter`` and read
what it holds. Nothing here mocks the emitter, because what the emitter does
with the dict is the thing being asserted.
"""

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from chainlit.action import Action
from chainlit.element import CustomElement
from chainlit.message import (
    AskActionMessage,
    AskElementMessage,
    AskFileMessage,
    AskUserMessage,
    ErrorMessage,
    Message,
    MessageBase,
)
from chainlit.persistence.writer import SaveStep, SessionWriter, WriterRegistry
from chainlit.protocol.payloads import (
    Action as ActionPayload,
    AskActionReply,
    AskElementReply,
    AskFileReply,
    AskFileSpec,
    AskTextReply,
    AskTextSpec,
    FileRef,
    Step as StepPayload,
)
from chainlit.protocol.server import (
    ActionAdd,
    ActionRemove,
    AskEnd,
    AskStart,
    ElementRemove,
    ElementUpsert,
    StepDelete,
    StepStreamStart,
    StepStreamToken,
    StepUpdate,
    StepUpsert,
)
from chainlit.types import AskSlotBusyError
from tests.conftest import bind_context


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


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


@pytest.fixture
def no_author_rename(monkeypatch):
    monkeypatch.setattr("chainlit.message.config.code.author_rename", None)


async def until_asked(session) -> None:
    """Wait until the ask is on the session's slot.

    A deadline, not a turn count: asking may spool a file to disk first,
    and how many loop turns that takes is the machine's business.
    """
    deadline = time.monotonic() + 5.0
    while session.pending_ask is None:
        if time.monotonic() > deadline:
            raise AssertionError("no ask was sent")
        await asyncio.sleep(0.001)


async def answer(session, value: Any) -> None:
    await until_asked(session)
    session.pending_ask.future.set_result(value)


def saved_steps(writer: SessionWriter):
    return [op.record for op in writer.held if isinstance(op, SaveStep)]


class TestMessageBase:
    async def test_post_init_sets_thread_id(self, ctx):
        assert Message(content="test").thread_id == "test_thread_id"

    async def test_post_init_generates_id_if_not_provided(self, ctx):
        assert len(Message(content="test").id) == 36

    async def test_post_init_uses_provided_id(self, ctx):
        assert Message(content="test", id="custom_id").id == "custom_id"

    async def test_from_dict_creates_message(self, ctx):
        msg = MessageBase.from_dict(
            {
                "id": "msg_123",
                "parentId": "parent_123",
                "createdAt": "2024-01-01T00:00:00Z",
                "output": "Hello world",
                "name": "Assistant",
                "command": "/test",
                "type": "user_message",
                "language": "python",
                "metadata": {"key": "value"},
            }
        )
        assert msg.id == "msg_123"
        assert msg.parent_id == "parent_123"
        assert msg.created_at == "2024-01-01T00:00:00Z"
        assert msg.content == "Hello world"
        assert msg.author == "Assistant"
        assert msg.command == "/test"
        assert msg.type == "user_message"
        assert msg.language == "python"
        assert msg.metadata == {"key": "value"}

    async def test_from_dict_with_minimal_data(self, ctx, monkeypatch):
        monkeypatch.setattr("chainlit.message.config.ui.name", "DefaultBot")
        msg = MessageBase.from_dict(
            {"id": "msg_123", "createdAt": "2024-01-01T00:00:00Z", "output": "Hello"}
        )
        assert msg.author == "DefaultBot"
        assert msg.type == "assistant_message"

    async def test_to_dict_returns_step_dict(self, ctx):
        msg = Message(
            content="Test content",
            author="TestBot",
            language="python",
            type="user_message",
            metadata={"key": "value"},
            tags=["tag1", "tag2"],
            id="msg_123",
            parent_id="parent_123",
            command="/test",
        )
        msg.created_at = "2024-01-01T00:00:00Z"
        result = msg.to_dict()
        assert result["id"] == "msg_123"
        assert result["threadId"] == "test_thread_id"
        assert result["parentId"] == "parent_123"
        assert result["createdAt"] == "2024-01-01T00:00:00Z"
        assert result["command"] == "/test"
        assert result["output"] == "Test content"
        assert result["name"] == "TestBot"
        assert result["type"] == "user_message"
        assert result["language"] == "python"
        assert result["streaming"] is False
        assert result["isError"] is False
        assert result["waitForAnswer"] is False
        assert result["metadata"] == {"key": "value"}
        assert result["tags"] == ["tag1", "tag2"]

    async def test_update_stops_streaming_and_patches(self, ctx, session, frames):
        msg = Message(content="test")
        msg.streaming = True
        assert await msg.update() is True
        assert msg.streaming is False
        [patch] = frames(session, StepUpdate)
        assert patch.step.id == msg.id
        assert patch.step.streaming is False

    async def test_update_adds_to_chat_context(self, ctx, session):
        from chainlit.chat_context import chat_context

        msg = Message(content="test")
        await msg.update()
        assert msg in chat_context.get()

    async def test_remove_deletes_on_the_wire_and_in_chat_context(
        self, ctx, session, frames
    ):
        from chainlit.chat_context import chat_context

        msg = Message(content="test", id="msg_123")
        chat_context.add(msg)
        assert await msg.remove() is True
        assert msg not in chat_context.get()
        assert [f.step_id for f in frames(session, StepDelete)] == ["msg_123"]


class TestMessage:
    async def test_message_with_string_content(self, ctx):
        msg = Message(content="Hello world")
        assert msg.content == "Hello world"
        assert msg.language is None

    async def test_message_with_dict_content(self, ctx):
        content = {"key": "value", "number": 42}
        msg = Message(content=content)
        assert msg.content == json.dumps(content, indent=4, ensure_ascii=False)
        assert msg.language == "json"

    async def test_message_with_non_serializable_dict(self, ctx):
        class NonSerializable:
            pass

        msg = Message(content={"obj": NonSerializable()})
        assert msg.language == "text"
        assert "NonSerializable" in msg.content

    async def test_message_with_non_string_content(self, ctx):
        msg = Message(content=12345)
        assert msg.content == "12345"
        assert msg.language == "text"

    async def test_message_with_none_content(self, ctx):
        assert Message(content=None).content == "None"

    async def test_message_with_default_author(self, ctx, monkeypatch):
        monkeypatch.setattr("chainlit.message.config.ui.name", "DefaultBot")
        assert Message(content="test").author == "DefaultBot"

    async def test_message_send_puts_the_step_on_the_wire(
        self, ctx, session, frames, no_author_rename
    ):
        msg = Message(content="test", author="Bot")
        assert await msg.send() is msg
        assert msg.created_at is not None
        [upsert] = frames(session, StepUpsert)
        assert upsert.step.id == msg.id
        assert upsert.step.output == "test"
        assert upsert.step.name == "Bot"
        assert upsert.step.thread_id == "test_thread_id"

    async def test_message_send_with_author_rename(
        self, ctx, session, frames, monkeypatch
    ):
        async def rename(name):
            return "NewName"

        monkeypatch.setattr("chainlit.message.config.code.author_rename", rename)
        msg = Message(content="test", author="OldName")
        await msg.send()
        assert msg.author == "NewName"
        assert frames(session, StepUpsert)[0].step.name == "NewName"

    async def test_message_send_with_none_content(self, ctx, no_author_rename):
        msg = Message(content="test")
        msg.content = None
        await msg.send()
        assert msg.content == ""

    async def test_message_send_with_actions_and_elements(
        self, ctx, session, frames, no_author_rename
    ):
        action = Action(name="go", payload={})
        element = CustomElement(name="widget", props={"a": 1})
        msg = Message(content="test", actions=[action], elements=[element])
        await msg.send()
        [added] = frames(session, ActionAdd)
        assert added.action.for_id == msg.id
        [shown] = frames(session, ElementUpsert)
        assert shown.element.for_id == msg.id

    async def test_message_update_resends_only_unsent_actions(
        self, ctx, session, frames
    ):
        fresh = Action(name="fresh", payload={})
        sent = Action(name="sent", payload={}, forId="existing_id")
        msg = Message(content="test", actions=[fresh, sent])
        assert await msg.update() is True
        assert [a.action.name for a in frames(session, ActionAdd)] == ["fresh"]

    async def test_message_remove_actions(self, ctx, session, frames):
        actions = [Action(name="a", payload={}), Action(name="b", payload={})]
        msg = Message(content="test", actions=actions)
        await msg.remove_actions()
        assert [r.id for r in frames(session, ActionRemove)] == [a.id for a in actions]

    async def test_stream_token_starts_streaming(self, ctx, session, frames):
        msg = Message(content="")
        await msg.stream_token("Hello")
        assert msg.streaming is True
        assert msg.content == "Hello"
        [start] = frames(session, StepStreamStart)
        assert start.step.id == msg.id

    async def test_stream_token_appends_content(self, ctx, session, frames):
        msg = Message(content="Hello")
        msg.streaming = True
        await msg.stream_token(" world")
        assert msg.content == "Hello world"
        [token] = frames(session, StepStreamToken)
        assert (token.id, token.token, token.is_sequence) == (msg.id, " world", False)

    async def test_stream_token_with_sequence(self, ctx, session, frames):
        msg = Message(content="Old content")
        msg.streaming = True
        await msg.stream_token("New content", is_sequence=True)
        assert msg.content == "New content"
        assert frames(session, StepStreamToken)[0].is_sequence is True

    async def test_stream_token_ignores_empty_token(self, ctx, session, frames):
        msg = Message(content="test")
        await msg.stream_token("")
        assert msg.content == "test"
        assert frames(session) == []


class TestErrorMessage:
    async def test_error_message_initialization(self, ctx):
        msg = ErrorMessage(content="An error occurred")
        assert msg.type == "assistant_message"
        assert msg.is_error is True

    async def test_error_message_send(self, ctx, session, frames, no_author_rename):
        msg = ErrorMessage(content="Error occurred", author="ErrorBot")
        assert await msg.send() is msg
        [upsert] = frames(session, StepUpsert)
        assert upsert.step.is_error is True
        assert upsert.step.name == "ErrorBot"


class TestPersistence:
    """The row goes to the session's writer, never to the wire."""

    async def test_send_queues_the_step_row(
        self, ctx, session, held_writer, no_author_rename
    ):
        msg = Message(content="hello", author="Bot")
        await msg.send()
        [record] = saved_steps(held_writer)
        assert record.id == msg.id
        assert record.output == "hello"
        assert record.thread_id == "test_thread_id"
        assert msg.persisted is True

    async def test_send_queues_the_row_once(
        self, ctx, session, held_writer, no_author_rename
    ):
        msg = Message(content="hello")
        await msg.send()
        await msg.send()
        assert len(saved_steps(held_writer)) == 1

    async def test_update_queues_a_row_too(self, ctx, session, held_writer):
        msg = Message(content="v1")
        await msg.update()
        msg.content = "v2"
        await msg.update()
        assert [r.output for r in saved_steps(held_writer)] == ["v1", "v2"]

    async def test_no_writer_means_no_row_and_no_error(
        self, ctx, session, no_author_rename
    ):
        assert session.writer is None
        await Message(content="hello").send()

    async def test_wait_never_reaches_the_row(
        self, ctx, session, held_writer, frames, no_author_rename
    ):
        msg = Message(content="loading", wait=["a", "b"])
        await msg.send()
        msg.wait = ["c"]
        await msg.update()
        assert frames(session, StepUpsert)[0].step.wait is not None
        assert frames(session, StepUpdate)[0].step.wait is not None
        # ``StepRecord`` has no ``wait`` field: had it leaked, the conversion
        # would have refused the dict and no row would be held at all.
        assert len(saved_steps(held_writer)) == 2

    async def test_resume_delete_flag_reaches_the_row(
        self, ctx, session, held_writer, no_author_rename
    ):
        await Message(content="test", resume="delete").send()
        [record] = saved_steps(held_writer)
        assert record.metadata == {"resume_policy": "delete"}


class TestAskUserMessage:
    async def test_initialization(self, ctx):
        msg = AskUserMessage(content="What is your name?")
        assert msg.timeout == 60
        assert msg.raise_on_timeout is False
        assert AskUserMessage(content="?", timeout=120).timeout == 120

    async def test_send_returns_the_answer_stamped_with_its_parent(
        self, ctx, session, frames, no_author_rename
    ):
        msg = AskUserMessage(content="Question?")
        msg.parent_id = "the-parent"
        sending = asyncio.create_task(msg.send())
        reply = StepPayload(id="reply-1", type="user_message", output="Answer")
        await answer(session, AskTextReply(step=reply))
        result = await sending

        assert result["output"] == "Answer"
        assert result["parentId"] == "the-parent"
        assert msg.wait_for_answer is False
        [start] = frames(session, AskStart)
        assert start.spec.step_id == msg.id
        assert isinstance(start.spec, AskTextSpec)
        assert start.step.wait_for_answer is True
        assert [e.reason for e in frames(session, AskEnd)] == ["answered"]
        assert session.runner.user_messages == [reply]

    async def test_send_returns_none_on_timeout(
        self, ctx, session, frames, no_author_rename
    ):
        msg = AskUserMessage(content="Question?", timeout=0)
        assert await msg.send() is None
        assert [e.reason for e in frames(session, AskEnd)] == ["timeout"]

    async def test_send_raises_on_timeout_when_asked(
        self, ctx, session, no_author_rename
    ):
        msg = AskUserMessage(content="Question?", timeout=0, raise_on_timeout=True)
        with pytest.raises(TimeoutError):
            await msg.send()

    async def test_question_is_in_chat_context_before_the_answer(
        self, ctx, session, no_author_rename
    ):
        from chainlit.chat_context import chat_context

        msg = AskUserMessage(content="Question?")
        sending = asyncio.create_task(msg.send())
        await until_asked(session)
        assert msg in chat_context.get()
        session.pending_ask.future.set_result(
            AskTextReply(step=StepPayload(id="r", type="user_message", output="x"))
        )
        await sending


class TestAskFileMessage:
    async def test_initialization(self, ctx, monkeypatch):
        monkeypatch.setattr("chainlit.message.config.ui.name", "Bot")
        msg = AskFileMessage(content="Upload a file", accept=["text/plain"])
        assert msg.max_size_mb == 2
        assert msg.max_files == 1
        custom = AskFileMessage(
            content="Upload", accept=["image/*"], max_size_mb=10, max_files=5
        )
        assert (custom.max_size_mb, custom.max_files) == (10, 5)

    async def test_send_returns_the_spooled_files(
        self, ctx, session, frames, no_author_rename
    ):
        session.files["file_123"] = {
            "id": "file_123",
            "name": "test.txt",
            "path": "/path/to/test.txt",
            "size": 1024,
            "type": "text/plain",
        }
        msg = AskFileMessage(content="Upload", accept=["text/plain"], max_files=3)
        sending = asyncio.create_task(msg.send())
        await answer(session, AskFileReply(files=[FileRef(id="file_123")]))
        result = await sending

        assert [r.id for r in result] == ["file_123"]
        assert result[0].path == "/path/to/test.txt"
        [start] = frames(session, AskStart)
        assert isinstance(start.spec, AskFileSpec)
        assert start.spec.max_files == 3
        assert start.spec.accept == ["text/plain"]
        assert session.runner.ask_files == [[session.files["file_123"]]]

    async def test_send_with_no_response(self, ctx, session, no_author_rename):
        msg = AskFileMessage(content="Upload", accept=["text/plain"], timeout=0)
        assert await msg.send() is None


class TestAskActionMessage:
    async def test_send_returns_the_chosen_action(
        self, ctx, session, frames, no_author_rename
    ):
        action = Action(name="confirm", payload={}, label="Confirm", id="action_123")
        msg = AskActionMessage(content="Choose", actions=[action])
        sending = asyncio.create_task(msg.send())
        await answer(
            session,
            AskActionReply(
                action=ActionPayload(id="action_123", name="confirm", label="Confirm")
            ),
        )
        result = await sending

        assert result["id"] == "action_123"
        assert msg.content == "**Selected:** Confirm"
        assert [a.action.id for a in frames(session, ActionAdd)] == ["action_123"]
        assert [r.id for r in frames(session, ActionRemove)] == ["action_123"]
        [start] = frames(session, AskStart)
        assert start.spec.keys == ["action_123"]
        # Buttons were sent, the ask was asked, the answer updated the step.
        assert [type(f).__name__ for f in frames(session)][:2] == [
            "ActionAdd",
            "AskStart",
        ]
        assert frames(session, StepUpdate)[-1].step.output == "**Selected:** Confirm"

    async def test_send_timeout(self, ctx, session, no_author_rename):
        action = Action(name="confirm", payload={}, id="action_123")
        msg = AskActionMessage(content="Choose", actions=[action], timeout=0)
        assert await msg.send() is None
        assert msg.content == "Timed out: no action was taken"

    async def test_restore_actions_are_snapshotted_on_the_ask(
        self, ctx, session, no_author_rename
    ):
        action = Action(name="confirm", payload={}, id="action_123")
        msg = AskActionMessage(content="Choose", actions=[action])
        sending = asyncio.create_task(msg.send())
        await until_asked(session)
        assert [a.id for a in session.pending_ask.restore_actions] == ["action_123"]
        session.pending_ask.future.set_result(
            AskActionReply(action=ActionPayload(id="action_123", name="confirm"))
        )
        await sending


class TestAskElementMessage:
    async def test_send_submitted(self, ctx, session, frames, no_author_rename):
        element = CustomElement(name="form", props={"field": ""}, id="element_123")
        msg = AskElementMessage(content="Submit", element=element)
        sending = asyncio.create_task(msg.send())
        await answer(session, AskElementReply(submitted=True, props={"field": "value"}))
        result = await sending

        # Spread, not nested: the app reads its own props off the top level.
        assert result == {"field": "value", "submitted": True}
        assert msg.content == "Thanks for submitting"
        assert [e.element.id for e in frames(session, ElementUpsert)] == ["element_123"]
        assert [r.id for r in frames(session, ElementRemove)] == ["element_123"]
        [start] = frames(session, AskStart)
        assert start.spec.element_id == "element_123"

    async def test_send_cancelled(self, ctx, session, no_author_rename):
        element = CustomElement(name="form", props={})
        msg = AskElementMessage(content="Submit", element=element)
        sending = asyncio.create_task(msg.send())
        await answer(session, AskElementReply(submitted=False))
        assert (await sending) == {"submitted": False}
        assert msg.content == "Cancelled"

    async def test_send_timeout(self, ctx, session, no_author_rename):
        element = CustomElement(name="form", props={})
        msg = AskElementMessage(content="Submit", element=element, timeout=0)
        assert await msg.send() is None
        assert msg.content == "Timed out"

    async def test_restore_element_is_a_snapshot(self, ctx, session, no_author_rename):
        element = CustomElement(name="form", props={"v": 1}, id="element_123")
        msg = AskElementMessage(content="Submit", element=element)
        sending = asyncio.create_task(msg.send())
        await until_asked(session)
        restored = session.pending_ask.restore_element
        assert restored is not None
        assert restored.id == "element_123"
        assert restored.props == {"v": 1}
        session.pending_ask.future.set_result(AskElementReply(submitted=True))
        await sending


class TestMessageWait:
    def emitted_wait(self, frame):
        return frame.step.wait

    async def test_wait_defaults(self, ctx):
        msg = Message(content="test")
        assert msg.wait is False
        assert msg.wait_interval == 5.0
        assert msg.wait_loop is False

    async def test_wait_never_in_to_dict(self, ctx):
        assert "wait" not in Message(content="test", wait=["a", "b"]).to_dict()

    async def test_send_without_wait_has_no_wait(
        self, ctx, session, frames, no_author_rename
    ):
        await Message(content="test").send()
        assert frames(session, StepUpsert)[0].step.wait is None

    async def test_send_with_wait_list(self, ctx, session, frames, no_author_rename):
        msg = Message(
            content="step 1", wait=["step 1", "step 2"], wait_interval=8, wait_loop=True
        )
        await msg.send()
        wait = frames(session, StepUpsert)[0].step.wait
        assert (wait.texts, wait.interval_ms, wait.loop) == (
            ["step 1", "step 2"],
            8000,
            True,
        )

    async def test_send_with_wait_true(self, ctx, session, frames, no_author_rename):
        await Message(content="test", wait=True).send()
        wait = frames(session, StepUpsert)[0].step.wait
        assert (wait.texts, wait.interval_ms, wait.loop) == ([], 5000, False)

    async def test_send_with_empty_wait_list(
        self, ctx, session, frames, no_author_rename
    ):
        await Message(content="test", wait=[]).send()
        assert frames(session, StepUpsert)[0].step.wait.texts == []

    @pytest.mark.parametrize(
        "bad_interval", [None, float("inf"), float("-inf"), float("nan")]
    )
    async def test_wait_interval_invalid_falls_back_to_default(
        self, ctx, session, frames, no_author_rename, bad_interval
    ):
        msg = Message(content="test", wait=["a"])
        msg.wait_interval = bad_interval
        await msg.send()
        assert frames(session, StepUpsert)[0].step.wait.interval_ms == 5000

    async def test_wait_interval_clamped_to_minimum(
        self, ctx, session, frames, no_author_rename
    ):
        await Message(content="test", wait=["a"], wait_interval=0.5).send()
        assert frames(session, StepUpsert)[0].step.wait.interval_ms == 2000

    async def test_wait_assigned_empty_list_emits_shimmer_only(
        self, ctx, session, frames, no_author_rename
    ):
        msg = Message(content="loading", wait=["a"])
        await msg.send()
        msg.wait = []
        await msg.update()
        assert frames(session, StepUpdate)[0].step.wait.texts == []
        assert msg.wait is False

    async def test_wait_false_emits_no_wait_on_send_and_update(
        self, ctx, session, frames, no_author_rename
    ):
        msg = Message(content="test", wait=False)
        await msg.send()
        await msg.update()
        assert frames(session, StepUpsert)[0].step.wait is None
        # A patch without an opinion leaves wait alone: UNSET, not null.
        assert frames(session, StepUpdate)[0].step.wait is not None
        assert frames(session, StepUpdate)[0].step.wait is not None

    async def test_empty_content_takes_first_wait_text(
        self, ctx, session, frames, no_author_rename
    ):
        msg = Message(content="", wait=["first", "second"])
        assert msg.content == "first"
        await msg.send()
        assert frames(session, StepUpsert)[0].step.output == "first"

    async def test_empty_content_kept_with_wait_true(self, ctx):
        assert Message(content="", wait=True).content == ""

    async def test_non_empty_content_not_overridden_by_wait_texts(self, ctx):
        assert Message(content="explicit", wait=["first"]).content == "explicit"

    async def test_wait_consumed_on_send(self, ctx, session, frames, no_author_rename):
        msg = Message(content="loading", wait=["a", "b"])
        await msg.send()
        assert msg.wait is False
        msg.content = "done"
        await msg.update()
        from msgspec import UNSET

        [patch] = frames(session, StepUpdate)
        assert patch.step.wait is UNSET
        assert patch.step.output == "done"

    async def test_active_wait_payload_tracked_for_replay(self, ctx, no_author_rename):
        msg = Message(content="loading", wait=["a", "b"], wait_interval=8)
        await msg.send()
        assert msg._active_wait_payload == {
            "texts": ["a", "b"],
            "intervalMs": 8000,
            "loop": False,
        }
        msg.content = "done"
        await msg.update()
        assert msg._active_wait_payload is None

    async def test_wait_reassigned_before_update(
        self, ctx, session, frames, no_author_rename
    ):
        msg = Message(content="phase 1", wait=["phase 1"])
        await msg.send()
        msg.wait = ["phase 2a", "phase 2b"]
        msg.wait_interval = 3
        await msg.update()
        wait = frames(session, StepUpdate)[0].step.wait
        assert (wait.texts, wait.interval_ms) == (["phase 2a", "phase 2b"], 3000)
        assert msg.wait is False


class TestMessageResumePolicy:
    async def test_resume_keep_default_leaves_metadata_untouched(self, ctx):
        msg = Message(content="test")
        assert msg.metadata is None
        assert msg.to_dict()["metadata"] == {}
        assert Message(content="test", resume="keep").metadata is None

    async def test_resume_delete_sets_metadata_flag(self, ctx):
        assert Message(content="test", resume="delete").metadata == {
            "resume_policy": "delete"
        }
        assert Message(content="test", metadata={"a": 1}, resume="delete").metadata == {
            "a": 1,
            "resume_policy": "delete",
        }

    async def test_resume_invalid_value_raises(self, ctx):
        with pytest.raises(ValueError, match="resume must be"):
            Message(content="test", resume="drop")
        with pytest.raises(ValueError, match="resume must be"):
            AskUserMessage(content="Question?", resume="wipe")

    async def test_ask_messages_take_the_flag(self, ctx):
        flag = {"resume_policy": "delete"}
        assert AskUserMessage(content="?", resume="delete").metadata == flag
        assert AskUserMessage(content="?").metadata is None
        assert (
            AskFileMessage(content="?", accept=["text/plain"], resume="delete").metadata
            == flag
        )
        action = Action(name="a", payload={})
        assert (
            AskActionMessage(content="?", actions=[action], resume="delete").metadata
            == flag
        )
        element = CustomElement(name="e", props={})
        assert (
            AskElementMessage(content="?", element=element, resume="delete").metadata
            == flag
        )


class TestAskSlotBusyTeardown:
    """A refused ask must still clean up what it already sent.

    The refusal happens before send_ask_user's own try/finally, so every
    teardown below the call is skipped unless the send methods repeat it.
    """

    @pytest_asyncio.fixture
    async def busy_slot(self, ctx, session, monkeypatch):
        monkeypatch.setattr("chainlit.config.config.features.strict_ask_slot", True)
        blocking = AskUserMessage(content="first")
        sending = asyncio.create_task(blocking.send())
        yield sending
        sending.cancel()

    async def test_ask_action_removes_its_buttons(
        self, ctx, session, frames, no_author_rename, busy_slot
    ):
        await until_asked(session)
        action = Action(name="confirm", payload={}, id="action_123")
        msg = AskActionMessage(content="Choose", actions=[action])
        with pytest.raises(AskSlotBusyError):
            await msg.send()
        assert [r.id for r in frames(session, ActionRemove)] == ["action_123"]
        assert msg.wait_for_answer is False
        assert msg.content == "Choose"

    async def test_ask_user_clears_wait_for_answer(
        self, ctx, session, no_author_rename, busy_slot
    ):
        await until_asked(session)
        msg = AskUserMessage(content="Your name?")
        with pytest.raises(AskSlotBusyError):
            await msg.send()
        assert msg.wait_for_answer is False

    async def test_ask_file_clears_wait_for_answer(
        self, ctx, session, no_author_rename, busy_slot
    ):
        await until_asked(session)
        msg = AskFileMessage(content="Upload", accept=["text/plain"])
        with pytest.raises(AskSlotBusyError):
            await msg.send()
        assert msg.wait_for_answer is False

    async def test_ask_element_removes_its_element(
        self, ctx, session, frames, no_author_rename, busy_slot
    ):
        await until_asked(session)
        element = CustomElement(name="form", props={}, id="element_123")
        msg = AskElementMessage(content="Submit", element=element)
        with pytest.raises(AskSlotBusyError):
            await msg.send()
        assert [r.id for r in frames(session, ElementRemove)] == ["element_123"]
        assert msg.content == "Submit"


class TestMockedElements:
    """Elements and actions the message owns are told the message id."""

    async def test_send_forwards_the_id(self, ctx, no_author_rename):
        action = AsyncMock(spec=Action)
        element = AsyncMock()
        msg = Message(content="test", actions=[action], elements=[element])
        await msg.send()
        action.send.assert_awaited_once_with(for_id=msg.id)
        element.send.assert_awaited_once_with(for_id=msg.id)
