"""``cl.Step``: what it puts on the wire and on the writer."""

import asyncio
import sys
import uuid
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from chainlit.context import local_steps
from chainlit.element import Element
from chainlit.persistence.writer import SaveStep, SessionWriter, WriterRegistry
from chainlit.protocol.server import (
    StepDelete,
    StepStreamStart,
    StepStreamToken,
    StepUpdate,
    StepUpsert,
)
from chainlit.step import (
    Step,
    check_add_step_in_cot,
    flatten_args_kwargs,
    step,
    stub_step,
)
from tests.conftest import bind_context

# ``chainlit.step`` the attribute is the decorator; the module is only
# reachable by name.
step_module = sys.modules["chainlit.step"]


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


@pytest.fixture
def held_writer(session):
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
    monkeypatch.setattr(step_module.config.code, "author_rename", None)


class TestStepClass:
    async def test_step_initialization_with_defaults(self, ctx):
        test_step = Step(name="test_step")
        assert test_step.name == "test_step"
        assert test_step.type == "undefined"
        uuid.UUID(test_step.id)
        assert test_step.parent_id is None
        assert test_step.metadata == {}
        assert test_step.tags is None
        assert test_step.is_error is False
        assert test_step.show_input == "json"
        assert test_step.language is None
        assert test_step.default_open is False
        assert test_step.elements == []
        assert test_step.streaming is False
        assert test_step.persisted is False
        assert test_step.input == ""
        assert test_step.output == ""
        assert test_step.created_at is not None
        assert test_step.start is None
        assert test_step.end is None
        assert test_step.thread_id == "test_thread_id"

    async def test_step_initialization_with_all_fields(self, ctx):
        test_id = str(uuid.uuid4())
        parent_id = str(uuid.uuid4())
        test_step = Step(
            name="custom_step",
            type="tool",
            id=test_id,
            parent_id=parent_id,
            metadata={"key": "value"},
            tags=["tag1", "tag2"],
            language="python",
            default_open=True,
            show_input=False,
            thread_id="custom_thread_123",
        )
        assert test_step.id == test_id
        assert test_step.parent_id == parent_id
        assert test_step.metadata == {"key": "value"}
        assert test_step.tags == ["tag1", "tag2"]
        assert test_step.language == "python"
        assert test_step.default_open is True
        assert test_step.show_input is False
        assert test_step.thread_id == "custom_thread_123"

    async def test_input_and_output_setters(self, ctx):
        test_step = Step(name="test")
        test_step.input = {"param1": "value1", "param2": 42}
        assert "value1" in test_step.input
        assert test_step.language is None  # input never sets the language
        test_step.output = {"result": "success", "data": [1, 2, 3]}
        assert "success" in test_step.output
        assert test_step.language == "json"
        test_step.output = None
        assert test_step.output == ""

    async def test_step_clean_content_with_bytes(self, ctx):
        test_step = Step(name="test")
        test_step.output = {"text": "hello", "nested": {"data": b"more_binary"}}
        assert "STRIPPED_BINARY_DATA" in test_step.output
        assert "more_binary" not in test_step.output

    async def test_step_with_non_serializable_content(self, ctx):
        class NonSerializable:
            pass

        test_step = Step(name="test")
        test_step.output = NonSerializable()
        assert isinstance(test_step.output, str)
        assert test_step.language == "text"

    async def test_step_to_dict(self, ctx):
        test_step = Step(
            name="test_step",
            type="tool",
            metadata={"key": "value"},
            tags=["tag1"],
            icon="bolt",
        )
        test_step.input = "test input"
        test_step.output = "test output"
        step_dict = test_step.to_dict()
        assert step_dict["name"] == "test_step"
        assert step_dict["type"] == "tool"
        assert step_dict["id"] == test_step.id
        assert step_dict["threadId"] == "test_thread_id"
        assert step_dict["parentId"] is None
        assert step_dict["streaming"] is False
        # The icon rides in the metadata, which is where the row keeps it.
        assert step_dict["metadata"] == {"key": "value", "icon": "bolt"}
        assert step_dict["tags"] == ["tag1"]
        assert step_dict["input"] == "test input"
        assert step_dict["output"] == "test output"
        assert step_dict["isError"] is False
        assert step_dict["createdAt"] is not None

    async def test_step_send(self, ctx, session, frames, no_author_rename):
        test_step = Step(name="test_step", type="tool")
        test_step.output = "done"
        assert await test_step.send() is test_step
        assert test_step.persisted is True
        [upsert] = frames(session, StepUpsert)
        assert upsert.step.id == test_step.id
        assert upsert.step.type == "tool"
        assert upsert.step.output == "done"

    async def test_step_send_queues_the_row(
        self, ctx, session, frames, held_writer, no_author_rename
    ):
        test_step = Step(name="test_step", type="tool")
        await test_step.send()
        [op] = [op for op in held_writer.held if isinstance(op, SaveStep)]
        assert op.record.id == test_step.id
        assert op.record.type == "tool"

    async def test_step_send_with_elements(self, ctx, no_author_rename):
        element = Mock(spec=Element)
        element.send = AsyncMock()
        test_step = Step(name="test_step", elements=[element])
        await test_step.send()
        element.send.assert_awaited_once_with(for_id=test_step.id)

    async def test_step_send_already_persisted(self, ctx, session, frames):
        test_step = Step(name="test_step")
        test_step.persisted = True
        assert await test_step.send() is test_step
        assert frames(session) == []

    async def test_step_send_with_author_rename(
        self, ctx, session, frames, monkeypatch
    ):
        async def rename(name):
            return f"renamed {name}"

        monkeypatch.setattr(step_module.config.code, "author_rename", rename)
        await Step(name="tool").send()
        assert frames(session, StepUpsert)[0].step.name == "renamed tool"

    async def test_step_update(self, ctx, session, frames):
        test_step = Step(name="test_step")
        test_step.streaming = True
        assert await test_step.update() is True
        assert test_step.streaming is False
        [patch] = frames(session, StepUpdate)
        assert patch.step.id == test_step.id
        assert patch.step.streaming is False

    async def test_step_multiple_updates(self, ctx, session, frames):
        test_step = Step(name="test")
        for _ in range(3):
            await test_step.update()
        assert len(frames(session, StepUpdate)) == 3

    async def test_step_remove(self, ctx, session, frames):
        test_step = Step(name="test_step")
        assert await test_step.remove() is True
        assert [f.step_id for f in frames(session, StepDelete)] == [test_step.id]

    async def test_step_stream_token_output(self, ctx, session, frames):
        test_step = Step(name="test_step")
        await test_step.stream_token("Hello")
        await test_step.stream_token(" ")
        await test_step.stream_token("World")
        assert test_step.output == "Hello World"
        assert test_step.streaming is True
        [start] = frames(session, StepStreamStart)
        assert start.step.id == test_step.id
        assert [t.token for t in frames(session, StepStreamToken)] == [" ", "World"]

    async def test_step_stream_token_input(self, ctx, session, frames):
        test_step = Step(name="test_step")
        await test_step.stream_token("Input", is_input=True)
        await test_step.stream_token(" text", is_input=True)
        assert test_step.input == "Input text"
        assert frames(session, StepStreamToken)[0].is_input is True

    async def test_step_stream_token_sequence(self, ctx, session, frames):
        test_step = Step(name="test_step")
        await test_step.stream_token("First", is_sequence=True)
        await test_step.stream_token("Second", is_sequence=True)
        assert test_step.output == "Second"
        assert frames(session, StepStreamToken)[0].is_sequence is True

    async def test_step_stream_token_empty(self, ctx, session, frames):
        test_step = Step(name="test_step")
        await test_step.stream_token("")
        assert test_step.output == ""
        assert frames(session) == []

    async def test_hidden_cot_streams_a_stub(self, ctx, session, frames, monkeypatch):
        monkeypatch.setattr(step_module.config.ui, "cot", "hidden")
        tool = Step(name="secret", type="tool")
        await tool.stream_token("classified")
        [upsert] = frames(session, StepUpsert)
        assert upsert.step.output == ""
        assert frames(session, StepStreamStart) == []

    async def test_step_context_manager_async(
        self, ctx, session, frames, no_author_rename
    ):
        async with Step(name="context_step") as test_step:
            assert test_step.start is not None
            assert test_step.end is None
        assert test_step.end is not None
        assert len(frames(session, StepUpsert)) == 1
        assert len(frames(session, StepUpdate)) == 1

    async def test_step_context_manager_with_exception(self, ctx, no_author_rename):
        try:
            async with Step(name="error_step") as test_step:
                raise ValueError("Test error")
        except ValueError:
            pass
        assert test_step.is_error is True
        assert "Test error" in test_step.output

    async def test_step_sync_context_manager(
        self, ctx, session, frames, no_author_rename
    ):
        with Step(name="sync_step") as test_step:
            assert test_step.start is not None
        assert test_step.end is not None
        # The sync form schedules its sends; they land on the next turn.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(frames(session, StepUpsert)) == 1
        assert len(frames(session, StepUpdate)) == 1

    async def test_step_parent_id_from_context(self, ctx, no_author_rename):
        async with Step(name="parent_step") as parent:
            async with Step(name="child_step") as child:
                assert child.parent_id == parent.id

    async def test_step_local_steps_tracking(self, ctx, no_author_rename):
        async with Step(name="step1") as step1:
            assert step1 in local_steps.get()
            async with Step(name="step2") as step2:
                assert step2 in local_steps.get()
            assert step2 not in local_steps.get()

    async def test_step_id_uniqueness(self, ctx):
        assert len({Step(name="s").id for _ in range(3)}) == 3


class TestStepDecorator:
    async def test_step_decorator_async_function(
        self, ctx, session, frames, no_author_rename
    ):
        @step(name="async_step", type="tool")
        async def async_function(x: int) -> int:
            return x * 2

        assert await async_function(5) == 10
        [upsert] = frames(session, StepUpsert)
        assert upsert.step.name == "async_step"
        assert upsert.step.type == "tool"
        [patch] = frames(session, StepUpdate)
        assert patch.step.output == "10"

    async def test_step_decorator_sync_function(self, ctx, no_author_rename):
        @step(name="sync_step")
        def sync_function(x: int) -> int:
            return x * 2

        assert sync_function(5) == 10

    async def test_step_decorator_uses_function_name(
        self, ctx, session, frames, no_author_rename
    ):
        @step
        async def my_custom_function():
            return "result"

        assert await my_custom_function() == "result"
        assert frames(session, StepUpsert)[0].step.name == "my_custom_function"

    async def test_step_decorator_captures_input(
        self, ctx, session, frames, no_author_rename
    ):
        @step(name="input_step")
        async def function_with_args(a: int, b: str = "default"):
            return None

        await function_with_args(1, b="custom")
        assert '"a": 1' in frames(session, StepUpdate)[0].step.input
        assert '"b": "custom"' in frames(session, StepUpdate)[0].step.input

    async def test_step_decorator_handles_exception(
        self, ctx, session, frames, no_author_rename
    ):
        @step(name="failing_step")
        async def failing_function():
            raise RuntimeError("Something went wrong")

        with pytest.raises(RuntimeError):
            await failing_function()
        [patch] = frames(session, StepUpdate)
        assert patch.step.is_error is True
        assert "Something went wrong" in patch.step.output

    async def test_step_decorator_with_metadata_and_tags(
        self, ctx, session, frames, no_author_rename
    ):
        @step(name="tagged", metadata={"k": "v"}, tags=["t1"])
        async def tagged():
            return None

        await tagged()
        assert frames(session, StepUpsert)[0].step.metadata == {"k": "v"}
        assert frames(session, StepUpsert)[0].step.tags == ["t1"]


class TestStepHelperFunctions:
    def test_flatten_args_kwargs(self):
        def sample_func(a, b, c=10, d=20):
            pass

        result = flatten_args_kwargs(sample_func, (1, 2), {"d": 30})
        assert result == {"a": 1, "b": 2, "c": 10, "d": 30}

    async def test_stub_step(self, ctx):
        test_step = Step(name="test_step", type="tool")
        test_step.parent_id = "parent_123"
        test_step.input = "full input"
        test_step.output = "full output"
        stub = stub_step(test_step)
        assert stub["name"] == "test_step"
        assert stub["type"] == "tool"
        assert stub["parentId"] == "parent_123"
        assert stub["threadId"] == test_step.thread_id
        assert stub["input"] == ""
        assert stub["output"] == ""

    async def test_check_add_step_in_cot_hidden(self, ctx, monkeypatch):
        monkeypatch.setattr(step_module.config.ui, "cot", "hidden")
        assert (
            check_add_step_in_cot(Step(name="test", type="assistant_message")) is True
        )
        assert check_add_step_in_cot(Step(name="test", type="tool")) is False
        assert check_add_step_in_cot(Step(name="on_message", type="run")) is True

    async def test_check_add_step_in_cot_visible(self, ctx, monkeypatch):
        monkeypatch.setattr(step_module.config.ui, "cot", "full")
        assert check_add_step_in_cot(Step(name="test", type="tool")) is True
