"""A message arriving while an ask is pending.

Mirror of the orphaned-ask_reply rescue: ask mode lives only in the
client's `askUser` atom, so a message can reach `client_message` while the
server still waits on `pending_ask`. See
`chainlit-panda/docs/task_chainlit_message_during_live_ask.md` (rev 2).
"""

import asyncio
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from chainlit.emitter import ChainlitEmitter
from chainlit.session import PendingAsk, WebsocketSession
from chainlit.socket import message as client_message
from chainlit.types import AskActionSpec, AskSlotBusyError, AskSpec


def _text_step(**overrides) -> dict:
    step = {
        "threadId": "",
        "id": str(uuid.uuid4()),
        "name": "User",
        "type": "user_message",
        "output": "Кардиган вязанный для мальчика",
        "createdAt": "2026-08-27T10:00:00.000Z",
        "metadata": {},
    }
    step.update(overrides)
    return step


def _pending(spec, *, parent_id="ask-parent", timeout=86400) -> PendingAsk:
    return PendingAsk(
        step_dict={"id": spec.step_id, "parentId": parent_id},
        spec=spec,
        future=asyncio.get_running_loop().create_future(),
        deadline=time.monotonic() + timeout,
        restore_actions=[],
        restore_element=None,
    )


def _text_ask(step_id="ask-step") -> PendingAsk:
    return _pending(AskSpec(type="text", step_id=step_id, timeout=86400))


def _action_ask(step_id="action-step") -> PendingAsk:
    return _pending(
        AskActionSpec(type="action", step_id=step_id, timeout=86400, keys=["yes"])
    )


def _context(session) -> Mock:
    context = Mock()
    context.session = session
    context.emitter = AsyncMock()
    return context


class TestDeliverToPendingTextAsk:
    """Branch A: a live text ask takes the message as its answer."""

    async def _run(self, session, payload):
        context = _context(session)
        with (
            patch.object(WebsocketSession, "require", return_value=session),
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.process_message", new=AsyncMock()) as process,
        ):
            await client_message("sid-1", payload)
        return context, process

    @pytest.mark.asyncio
    async def test_message_answers_the_pending_text_ask(self, mock_session_factory):
        """The incident: wizard asks, the user's text arrives as a message."""
        pending = _text_ask()
        session = mock_session_factory(pending_ask=pending)
        step = _text_step()

        _, process = await self._run(session, {"message": step, "fileReferences": []})

        assert pending.future.done()
        assert pending.future.result()["output"] == step["output"]
        process.assert_not_awaited()
        # The long-lived hook keeps its slot: no parallel on_message task.
        assert session.current_task is None

    @pytest.mark.asyncio
    async def test_answer_is_stamped_with_the_ask_parent_and_re_emitted(
        self, mock_session_factory
    ):
        """The client echo carries no parentId; the server must add it."""
        pending = _text_ask()
        session = mock_session_factory(pending_ask=pending)

        context, _ = await self._run(session, {"message": _text_step()})

        assert pending.future.result()["parentId"] == "ask-parent"
        context.emitter.send_step.assert_awaited_once()
        assert context.emitter.send_step.await_args.args[0]["parentId"] == "ask-parent"

    @pytest.mark.asyncio
    async def test_redelivery_is_deduped_by_step_id(self, mock_session_factory):
        """A later ask_reply for the same step must be recognised as a dup."""
        pending = _text_ask(step_id="step-42")
        session = mock_session_factory(pending_ask=pending)

        await self._run(session, {"message": _text_step()})

        assert session.last_resolved_ask_step_id == "step-42"

    @pytest.mark.asyncio
    async def test_expired_ask_still_holding_the_slot_takes_the_answer(
        self, mock_session_factory
    ):
        """Mirrors branch 1 of ask_reply: expired-but-pending still wins.

        Refusing here would hand the app both a timeout and a duplicate
        message.
        """
        pending = _pending(
            AskSpec(type="text", step_id="ask-step", timeout=1), timeout=-1
        )
        session = mock_session_factory(pending_ask=pending)

        _, process = await self._run(session, {"message": _text_step()})

        assert pending.future.done()
        process.assert_not_awaited()


class TestFallsBackToRegularMessage:
    """Branches B and C: everything that is not a plain text answer."""

    async def _run(self, session, payload):
        context = _context(session)
        with (
            patch.object(WebsocketSession, "require", return_value=session),
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.process_message", new=AsyncMock()) as process,
        ):
            await client_message("sid-1", payload)
            # The handler fires process_message as a task; let it run.
            task = session.current_task
            if isinstance(task, asyncio.Task):
                await task
        return context, process

    @pytest.mark.asyncio
    async def test_no_pending_ask(self, mock_session_factory):
        session = mock_session_factory(pending_ask=None)

        _, process = await self._run(session, {"message": _text_step()})

        process.assert_awaited_once()
        assert session.current_task is not None

    @pytest.mark.asyncio
    async def test_action_ask_is_not_answered_by_text(self, mock_session_factory):
        """A text value cannot answer an action spec."""
        pending = _action_ask()
        session = mock_session_factory(pending_ask=pending)

        _, process = await self._run(session, {"message": _text_step()})

        assert not pending.future.done()
        process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_attachments_stay_a_regular_message(self, mock_session_factory):
        """A text ask cannot carry files; dropping them silently is worse."""
        pending = _text_ask()
        session = mock_session_factory(pending_ask=pending)

        _, process = await self._run(
            session,
            {"message": _text_step(), "fileReferences": [{"id": "file-1"}]},
        )

        assert not pending.future.done()
        process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_command_stays_a_regular_message(self, mock_session_factory):
        """Only process_message reads `command`; an ask reply would eat it."""
        pending = _text_ask()
        session = mock_session_factory(pending_ask=pending)

        _, process = await self._run(session, {"message": _text_step(command="search")})

        assert not pending.future.done()
        process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_modes_stay_a_regular_message(self, mock_session_factory):
        pending = _text_ask()
        session = mock_session_factory(pending_ask=pending)

        _, process = await self._run(
            session, {"message": _text_step(modes={"deep": True})}
        )

        assert not pending.future.done()
        process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_convertible_payload(self, mock_session_factory):
        """The strict gate is shared with the orphan-reply conversion."""
        pending = _text_ask()
        session = mock_session_factory(pending_ask=pending)

        _, process = await self._run(session, {"message": _text_step(id="not-a-uuid4")})

        assert not pending.future.done()
        process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_already_answered_ask_does_not_swallow_the_message(
        self, mock_session_factory
    ):
        pending = _text_ask()
        pending.future.set_result({"output": "earlier"})
        session = mock_session_factory(pending_ask=pending)

        _, process = await self._run(session, {"message": _text_step()})

        process.assert_awaited_once()


class TestStrictAskSlot:
    """A busy slot must be distinguishable from a timeout."""

    @pytest.fixture
    def emitter(self, mock_websocket_session: MagicMock) -> ChainlitEmitter:
        return ChainlitEmitter(mock_websocket_session)

    def _busy(self, session, pending):
        session.pending_ask = pending

    def _strict(self, session, enabled: bool):
        session.get_config.return_value = SimpleNamespace(
            features=SimpleNamespace(strict_ask_slot=enabled)
        )

    @pytest.mark.asyncio
    async def test_disabled_returns_none(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        self._busy(mock_websocket_session, _text_ask())
        self._strict(mock_websocket_session, False)

        res = await emitter.send_ask_user(
            {"id": "s", "parentId": "p"},
            AskSpec(type="text", step_id="s", timeout=10),
            False,
        )

        assert res is None

    @pytest.mark.asyncio
    async def test_mock_config_does_not_arm_strict_mode(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        """`Mock(spec=...).get_config()` is truthy — it must not count."""
        self._busy(mock_websocket_session, _text_ask())

        res = await emitter.send_ask_user(
            {"id": "s", "parentId": "p"},
            AskSpec(type="text", step_id="s", timeout=10),
            False,
        )

        assert res is None

    @pytest.mark.asyncio
    async def test_enabled_raises_with_the_blocking_step(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        self._busy(mock_websocket_session, _text_ask(step_id="blocking-step"))
        self._strict(mock_websocket_session, True)

        with pytest.raises(AskSlotBusyError) as excinfo:
            await emitter.send_ask_user(
                {"id": "s", "parentId": "p"},
                AskSpec(type="text", step_id="s", timeout=10),
                False,
            )

        assert excinfo.value.step_id == "blocking-step"

    @pytest.mark.asyncio
    async def test_refusal_does_not_take_the_slot(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        pending = _text_ask()
        self._busy(mock_websocket_session, pending)
        self._strict(mock_websocket_session, True)

        with pytest.raises(AskSlotBusyError):
            await emitter.send_ask_user(
                {"id": "s", "parentId": "p"},
                AskSpec(type="text", step_id="s", timeout=10),
                False,
            )

        assert mock_websocket_session.pending_ask is pending
