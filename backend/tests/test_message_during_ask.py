"""A message arriving while an ask is pending.

The composer does not know the server is waiting on a text question -- ask
mode lives in the client's own state -- so a plain message can reach the
runner while ``pending_ask`` is a text ask. It is the answer, and it must
reach the code that asked rather than ``on_message``. Everything else is a
message. See ``chainlit-panda/docs/task_chainlit_message_during_live_ask.md``.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
import pytest_asyncio

from chainlit.action import Action
from chainlit.message import AskActionMessage, AskUserMessage, Message
from chainlit.protocol.payloads import AskTextSpec, FileRef, Step as StepPayload
from chainlit.protocol.server import AskEnd, StepUpsert
from chainlit.runner import ApplicationRunner
from chainlit.ws.registry import SessionRegistry
from chainlit.ws.session import Session
from tests.conftest import bind_context

# ``chainlit.step`` the attribute is the decorator; the module is only
# reachable by name.
step_module = sys.modules["chainlit.step"]


class _Code:
    def __init__(self) -> None:
        self.on_message = None
        self.on_chat_start = None
        self.on_chat_end = None
        self.on_chat_resume = None
        self.on_thread_ready = None
        self.on_stop = None
        self.action_callbacks: Dict[str, Any] = {}
        self.author_rename = None


@pytest.fixture
def code():
    return _Code()


@pytest.fixture
def runner(code, monkeypatch):
    runner = ApplicationRunner(
        SimpleNamespace(
            code=code, features=SimpleNamespace(hot_swap_chat_profile=False)
        ),
        registry=SessionRegistry(),
    )
    return runner


@pytest.fixture
def session(runner, tmp_path, persisted_test_user) -> Session:
    return Session(
        id="sid-1",
        runner=runner,
        user=persisted_test_user,
        thread_id="t1",
        files_root=tmp_path,
    )


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


@pytest.fixture
def no_author_rename(monkeypatch):
    monkeypatch.setattr("chainlit.message.config.code.author_rename", None)
    monkeypatch.setattr(step_module.config.code, "author_rename", None)


def _text(**overrides: Any) -> StepPayload:
    fields: Dict[str, Any] = {
        "id": "u1",
        "name": "User",
        "type": "user_message",
        "output": "Кардиган вязанный для мальчика",
    }
    fields.update(overrides)
    return StepPayload(**fields)


async def _until_asked(session: Session) -> None:
    for _ in range(50):
        if session.pending_ask is not None:
            return
        await asyncio.sleep(0)
    raise AssertionError("no ask was sent")


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


class TestDeliverToPendingTextAsk:
    """Branch A: a live text ask takes the message as its answer."""

    async def test_message_answers_the_pending_text_ask(
        self, ctx, session, code, frames, no_author_rename
    ):
        """The incident: wizard asks, the user's text arrives as a message."""
        on_message_calls: List[Message] = []

        async def on_message(message):
            on_message_calls.append(message)

        code.on_message = on_message
        asking = asyncio.create_task(
            AskUserMessage(content="Что вяжем?", timeout=1).send()
        )
        await _until_asked(session)

        await session.runner.on_message(session, _text())
        result = await asking

        assert result["output"] == "Кардиган вязанный для мальчика"
        assert on_message_calls == []
        assert session.current_task is None

    async def test_answer_is_stamped_with_the_ask_parent_and_recorded(
        self, ctx, session, frames, no_author_rename
    ):
        """The client echo carries no parentId; the server must add it."""
        question = AskUserMessage(content="?", timeout=1)
        question.parent_id = "ask-parent"
        asking = asyncio.create_task(question.send())
        await _until_asked(session)

        await session.runner.on_message(session, _text(id="u2"))
        result = await asking

        assert result["parentId"] == "ask-parent"
        # Recorded as a user message: on the wire and in the transcript.
        assert any(f.step.id == "u2" for f in frames(session, StepUpsert))
        assert [e.step.id for e in session.transcript][-1] == "u2"
        assert [e.reason for e in frames(session, AskEnd)] == ["answered"]


class TestFallsBackToRegularMessage:
    """Branches B and C: everything that is not a plain text answer."""

    async def _run(self, session, code, message, file_references=()):
        received: List[Message] = []

        async def on_message(message):
            received.append(message)

        code.on_message = on_message
        await session.runner.on_message(session, message, file_references)
        assert session.current_task is not None
        await session.current_task
        return received

    async def test_no_pending_ask(self, ctx, session, code, no_author_rename):
        [received] = await self._run(session, code, _text())
        assert received.content == "Кардиган вязанный для мальчика"
        assert received.type == "user_message"

    async def test_action_ask_is_not_answered_by_text(
        self, ctx, session, code, no_author_rename
    ):
        action = Action(name="yes", payload={})
        asking = asyncio.create_task(
            AskActionMessage(content="?", actions=[action]).send()
        )
        await _until_asked(session)

        received = await self._run(session, code, _text())
        assert len(received) == 1
        assert not session.pending_ask.future.done()
        asking.cancel()
        await _settle()

    @pytest.mark.parametrize(
        ("message", "files"),
        [
            (_text(), (FileRef(id="f1"),)),
            (_text(command="search"), ()),
            (_text(modes={"model": "gpt"}), ()),
            (_text(type="system_message"), ()),
        ],
        ids=["attachments", "command", "modes", "not-a-user-message"],
    )
    async def test_anything_but_plain_text_stays_a_message(
        self, ctx, session, code, no_author_rename, message, files
    ):
        asking = asyncio.create_task(AskUserMessage(content="?").send())
        await _until_asked(session)

        received = await self._run(session, code, message, files)
        assert len(received) == 1
        assert not session.pending_ask.future.done()
        asking.cancel()
        await _settle()

    async def test_already_answered_ask_does_not_swallow_the_message(
        self, ctx, session, code, no_author_rename
    ):
        asking = asyncio.create_task(AskUserMessage(content="?").send())
        await _until_asked(session)
        session.pending_ask.future.set_result(None)

        received = await self._run(session, code, _text())
        assert len(received) == 1
        asking.cancel()
        await _settle()


class TestStrictAskSlot:
    async def _block(self, session):
        asking = asyncio.create_task(AskUserMessage(content="first").send())
        await _until_asked(session)
        return asking

    async def test_disabled_returns_none(
        self, ctx, session, no_author_rename, monkeypatch
    ):
        monkeypatch.setattr("chainlit.config.config.features.strict_ask_slot", False)
        first = await self._block(session)
        assert await AskUserMessage(content="second").send() is None
        first.cancel()
        await _settle()

    async def test_enabled_raises_with_the_blocking_step(
        self, ctx, session, no_author_rename, monkeypatch
    ):
        from chainlit.types import AskSlotBusyError

        monkeypatch.setattr("chainlit.config.config.features.strict_ask_slot", True)
        first = await self._block(session)
        blocking_id = session.pending_ask.step_id
        with pytest.raises(AskSlotBusyError) as info:
            await AskUserMessage(content="second").send()
        assert info.value.step_id == blocking_id
        # The refusal did not take the slot.
        assert session.pending_ask.step_id == blocking_id
        assert isinstance(session.pending_ask.spec, AskTextSpec)
        first.cancel()
        await _settle()
