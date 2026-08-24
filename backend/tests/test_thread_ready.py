"""Tests for the on_thread_ready hook and the task-indicator owner counter.

Spec: chainlit-panda/docs/task_chainlit_on_thread_ready.md (rev 3).
Covers the owner counter with level-triggered resyncs (§3.5), the dedicated
``thread_ready_task`` slot and its readers (§3.2), the launch point and its
guards in ``connection_successful`` (§3.3-3.4), and the lifecycle (§3.7).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from chainlit.emitter import ChainlitEmitter
from chainlit.resume_policy import thread_has_live_task
from chainlit.session import WebsocketSession, ws_sessions_id
from chainlit.socket import connection_successful, stop
from chainlit.step import StepDict
from chainlit.types import AskSpec
from chainlit.utils import wrap_user_function

from .conftest import create_chainlit_context


def _emitted(session) -> list:
    return [call.args[0] for call in session.emit.call_args_list]


@pytest.fixture
def emitter(mock_websocket_session):
    return ChainlitEmitter(mock_websocket_session)


class TestIndicatorCounter:
    """Per-session owner counter: edge-triggered emits (§3.5)."""

    @pytest.mark.asyncio
    async def test_acquire_release_edge_emits(self, emitter, mock_websocket_session):
        session = mock_websocket_session

        await emitter.task_acquire()
        assert session.task_counter == 1
        assert _emitted(session) == ["task_start"]

        await emitter.task_acquire()
        assert session.task_counter == 2
        assert _emitted(session) == ["task_start"]  # no second emit

        await emitter.task_release()
        assert session.task_counter == 1
        assert _emitted(session) == ["task_start"]  # 2→1 stays silent

        await emitter.task_release()
        assert session.task_counter == 0
        assert _emitted(session) == ["task_start", "task_end"]

    @pytest.mark.asyncio
    async def test_release_at_zero_clamps_silently(
        self, emitter, mock_websocket_session
    ):
        await emitter.task_release()
        assert mock_websocket_session.task_counter == 0
        assert _emitted(mock_websocket_session) == []


class TestAskUserCounter:
    """send_ask_user pauses the counter and resyncs on exit (§3.5)."""

    @staticmethod
    def _step_dict() -> StepDict:
        return {
            "id": "step-1",
            "parentId": "parent-1",
            "type": "assistant_message",
            "name": "ask",
            "output": "Pick one",
        }

    @staticmethod
    def _action_spec(timeout=10) -> AskSpec:
        return AskSpec(timeout=timeout, type="action", step_id="step-1")

    async def _resolve_when_pending(self, session, value):
        while session.pending_ask is None:
            await asyncio.sleep(0)
        session.pending_ask.future.set_result(value)

    @pytest.mark.asyncio
    async def test_owned_ask_pauses_and_relights(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        session = mock_websocket_session
        session.task_counter = 1  # a live owner (e.g. the hook) sent this ask

        task = asyncio.ensure_future(
            emitter.send_ask_user(self._step_dict(), self._action_spec())
        )
        await self._resolve_when_pending(session, {"name": "continue", "id": "a1"})
        await task

        events = _emitted(session)
        assert "task_end" in events  # paused for the wait
        assert (
            events[-1] == "task_start"
            or "task_start" in events[events.index("task_end") :]
        )
        assert session.task_counter == 1  # the ask never mutates the counter

    @pytest.mark.asyncio
    async def test_ownerless_ask_answer_leaves_indicator_dark(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        """Acceptance 8: an ask from a bare background task must not leave
        the indicator burning after the answer."""
        session = mock_websocket_session
        assert session.task_counter == 0

        task = asyncio.ensure_future(
            emitter.send_ask_user(self._step_dict(), self._action_spec())
        )
        await self._resolve_when_pending(session, {"name": "continue", "id": "a1"})
        await task

        assert "task_start" not in _emitted(session)
        assert session.task_counter == 0

    @pytest.mark.asyncio
    async def test_ownerless_ask_timeout_leaves_indicator_dark(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        session = mock_websocket_session

        result = await emitter.send_ask_user(
            self._step_dict(), self._action_spec(timeout=0)
        )

        assert result is None
        events = _emitted(session)
        assert "ask_timeout" in events
        assert "task_start" not in events

    @pytest.mark.asyncio
    async def test_owned_ask_timeout_relights(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        session = mock_websocket_session
        session.task_counter = 1

        await emitter.send_ask_user(self._step_dict(), self._action_spec(timeout=0))

        events = _emitted(session)
        assert "ask_timeout" in events
        # The owner is still alive: the indicator must come back after the
        # client forced it off on ask_timeout.
        assert "task_start" in events[events.index("ask_timeout") :]
        assert session.task_counter == 1

    @pytest.mark.asyncio
    async def test_nested_ask_relights_level_triggered(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        """Acceptance 3: at counter depth 2 no edge is ever crossed — the
        level resync must re-emit task_start anyway, because the client
        forced loading=false on receiving the ask."""
        session = mock_websocket_session
        session.task_counter = 2

        task = asyncio.ensure_future(
            emitter.send_ask_user(self._step_dict(), self._action_spec())
        )
        await self._resolve_when_pending(session, {"name": "continue", "id": "a1"})
        await task

        events = _emitted(session)
        assert events[-1] == "task_start"
        assert session.task_counter == 2

    @pytest.mark.asyncio
    async def test_owner_arriving_during_wait_relights(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        """An ownerless ask does no paired re-acquire, but the level resync
        still lights the indicator when an owner started during the wait."""
        session = mock_websocket_session

        task = asyncio.ensure_future(
            emitter.send_ask_user(self._step_dict(), self._action_spec())
        )
        while session.pending_ask is None:
            await asyncio.sleep(0)
        session.task_counter = 1  # an owner (e.g. process_message) started
        session.pending_ask.future.set_result({"name": "continue", "id": "a1"})
        await task

        assert _emitted(session)[-1] == "task_start"
        assert session.task_counter == 1

    @pytest.mark.asyncio
    async def test_foreign_owner_exiting_during_wait_does_not_poison(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        """An ownerless ask must not capture a foreign owner's unit: the
        owner exits during the wait, and after the answer the counter is 0
        and the indicator stays dark — no phantom owner left behind."""
        session = mock_websocket_session
        session.task_counter = 1  # a foreign owner (e.g. process_message)

        task = asyncio.ensure_future(
            emitter.send_ask_user(self._step_dict(), self._action_spec())
        )
        while session.pending_ask is None:
            await asyncio.sleep(0)
        await emitter.task_release()  # the foreign owner finishes mid-wait
        session.emit.reset_mock()
        session.pending_ask.future.set_result({"name": "continue", "id": "a1"})
        await task

        assert session.task_counter == 0
        assert "task_start" not in _emitted(session)

    @pytest.mark.asyncio
    async def test_live_successor_ask_wins_over_exit_resync(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ):
        """After stop freed the slot and on_stop installed a successor ask,
        the cancelled waiter's finally must not emit task_start over the
        successor's form."""
        session = mock_websocket_session
        session.task_counter = 1

        task = asyncio.ensure_future(
            emitter.send_ask_user(self._step_dict(), self._action_spec())
        )
        while session.pending_ask is None:
            await asyncio.sleep(0)
        original = session.pending_ask
        successor = Mock()
        successor.is_live = True
        session.pending_ask = successor  # stop freed the slot; on_stop asked
        session.emit.reset_mock()
        original.future.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert "task_start" not in _emitted(session)
        assert session.pending_ask is successor


class TestWrapperCounter:
    """with_task callbacks own the indicator through the counter ops."""

    @pytest.mark.asyncio
    async def test_with_task_uses_acquire_release(self, mock_session):
        async with create_chainlit_context(mock_session) as context:
            context.emitter.task_acquire = AsyncMock()
            context.emitter.task_release = AsyncMock()
            context.emitter.task_start = AsyncMock()
            context.emitter.task_end = AsyncMock()

            wrapped = wrap_user_function(AsyncMock(), with_task=True)
            await wrapped()

            context.emitter.task_acquire.assert_awaited_once()
            context.emitter.task_release.assert_awaited_once()
            # Raw emits stay reserved for handshake resyncs — a nested
            # callback going through them would double-drive the indicator.
            context.emitter.task_start.assert_not_awaited()
            context.emitter.task_end.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_during_acquire_still_releases(self, mock_session):
        """A cancellation landing inside the acquire's own emit await must
        still reach the paired release — an unpaired acquire would poison
        the owner counter for the rest of the session."""
        async with create_chainlit_context(mock_session) as context:
            context.emitter.task_acquire = AsyncMock(side_effect=asyncio.CancelledError)
            context.emitter.task_release = AsyncMock()

            wrapped = wrap_user_function(AsyncMock(), with_task=True)
            await wrapped()

            context.emitter.task_release.assert_awaited_once()


class TestThreadReadyTaskSlot:
    """The dedicated slot and its readers (§3.2, §3.7)."""

    def _real_session(self, id="tr-session", socket_id="tr-sid") -> WebsocketSession:
        return WebsocketSession(
            id=id,
            socket_id=socket_id,
            emit=AsyncMock(),
            emit_call=AsyncMock(),
            user_env={},
            client_type="webapp",
        )

    @pytest.mark.asyncio
    async def test_delete_cancels_hook_but_not_current_task(self):
        session = self._real_session()
        hook_task = asyncio.create_task(asyncio.sleep(30))
        message_task = asyncio.create_task(asyncio.sleep(30))
        session.thread_ready_task = hook_task
        session.current_task = message_task

        try:
            await session.delete()
            await asyncio.sleep(0)

            # Acceptance 10: the GC path must not leave a zombie hook…
            assert hook_task.cancelled()
            # …while current_task keeps today's semantics (never cancelled
            # by delete — that would kill in-flight on_message tasks of
            # disconnected users).
            assert not message_task.cancelled()
        finally:
            message_task.cancel()
            hook_task.cancel()

    @pytest.mark.asyncio
    async def test_stop_cancels_both_tasks(self):
        session = self._real_session(id="tr-stop", socket_id="tr-stop-sid")
        hook_task = asyncio.create_task(asyncio.sleep(30))
        message_task = asyncio.create_task(asyncio.sleep(30))
        session.thread_ready_task = hook_task
        session.current_task = message_task

        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()

        mock_config = Mock()
        mock_config.code.on_stop = None

        try:
            with (
                patch("chainlit.socket.init_ws_context", return_value=mock_context),
                patch("chainlit.socket.config", mock_config),
                patch("chainlit.socket.Message") as mock_message,
            ):
                mock_message.return_value.send = AsyncMock()
                await stop("tr-stop-sid")
            await asyncio.sleep(0)

            # Acceptance 2: stop cancels BOTH the converted-message task and
            # the hook.
            assert message_task.cancelled()
            assert hook_task.cancelled()
        finally:
            message_task.cancel()
            hook_task.cancel()
            await session.delete()

    @pytest.mark.asyncio
    async def test_thread_has_live_task_sees_hook_slot(self):
        """Acceptance 5: a second-tab resume must not delete resume="delete"
        steps from under a running hook."""
        hook_task = asyncio.create_task(asyncio.sleep(30))
        fake = SimpleNamespace(
            thread_id="thread-live",
            current_task=None,
            thread_ready_task=hook_task,
        )
        ws_sessions_id["fake-live"] = fake
        try:
            assert thread_has_live_task("thread-live") is True
        finally:
            ws_sessions_id.pop("fake-live", None)
            hook_task.cancel()

    @pytest.mark.asyncio
    async def test_f5_keepalive_sees_hook_slot(self):
        """Acceptance 1: F5 mid-pipeline (no live ask) must keep the session."""
        from chainlit.socket import _session_has_live_work

        hook_task = asyncio.create_task(asyncio.sleep(30))
        try:
            session = SimpleNamespace(
                pending_ask=None, current_task=None, thread_ready_task=hook_task
            )
            assert _session_has_live_work(session) is True

            done_session = SimpleNamespace(
                pending_ask=None, current_task=None, thread_ready_task=None
            )
            assert _session_has_live_work(done_session) is False
        finally:
            hook_task.cancel()


class TestThreadReadyLaunch:
    """Launch point, guards and ordering in connection_successful (§3.3-3.4)."""

    THREAD = {"id": "thread-1", "steps": [], "elements": [], "metadata": {}}

    def _session(self, mock_session_factory, **kwargs):
        gate = asyncio.Event()
        session = mock_session_factory(connection_inited=gate, **kwargs)
        session.restored = False
        session.chat_started = False
        session.current_task = None
        session.thread_id_to_resume = "thread-1"
        session.thread_ready_task = None
        session.resume_task_started = False
        session.task_counter = 0
        return session

    def _context(self, session):
        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()
        return mock_context

    def _config(self, on_chat_resume, on_thread_ready):
        mock_config = Mock()
        mock_config.code.on_chat_start = None
        mock_config.code.on_chat_resume = on_chat_resume
        mock_config.code.on_thread_ready = on_thread_ready
        return mock_config

    @pytest.mark.asyncio
    async def test_hook_launches_once_in_its_own_slot(self, mock_session_factory):
        """Acceptance 6: one launch per session; current_task untouched."""
        on_chat_resume = AsyncMock()
        on_thread_ready = AsyncMock()
        session = self._session(mock_session_factory)
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch(
                "chainlit.socket.resume_thread",
                AsyncMock(return_value=dict(self.THREAD)),
            ),
            patch(
                "chainlit.socket.config", self._config(on_chat_resume, on_thread_ready)
            ),
        ):
            await connection_successful("sid-1")
            session.restored = True
            await connection_successful("sid-1")

        assert on_thread_ready.call_count == 1
        assert session.thread_ready_task is not None
        assert session.resume_task_started is True
        assert session.current_task is None  # the slot with two unconditional
        # writers must never hold the hook
        thread_arg = on_thread_ready.call_args.args[0]
        assert thread_arg["id"] == "thread-1"
        await session.thread_ready_task

    @pytest.mark.asyncio
    async def test_resume_branch_runs_without_on_chat_resume(
        self, mock_session_factory
    ):
        """Acceptance 9: an app with only on_thread_ready still gets the
        resume (snapshot emit, cleanup, restore, hook launch)."""
        on_thread_ready = AsyncMock()
        session = self._session(mock_session_factory)
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch(
                "chainlit.socket.resume_thread",
                AsyncMock(return_value=dict(self.THREAD)),
            ),
            patch("chainlit.socket.config", self._config(None, on_thread_ready)),
        ):
            await connection_successful("sid-1")

        context.emitter.resume_thread.assert_awaited_once()
        assert on_thread_ready.call_count == 1
        assert session.thread_ready_task is not None
        await session.thread_ready_task

    @pytest.mark.asyncio
    async def test_no_hook_when_thread_not_found(self, mock_session_factory):
        on_thread_ready = AsyncMock()
        session = self._session(mock_session_factory)
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.resume_thread", AsyncMock(return_value=None)),
            patch("chainlit.socket.config", self._config(AsyncMock(), on_thread_ready)),
        ):
            await connection_successful("sid-1")

        on_thread_ready.assert_not_called()
        assert session.thread_ready_task is None
        context.emitter.send_resume_thread_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hook_launches_even_if_on_chat_resume_raises(
        self, mock_session_factory
    ):
        """§3.3: the finally runs after an on_chat_resume crash — symmetric
        with start, the hook still launches and the gate still opens."""
        on_chat_resume = AsyncMock(side_effect=RuntimeError("resume crashed"))
        on_thread_ready = AsyncMock()
        session = self._session(mock_session_factory)
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch(
                "chainlit.socket.resume_thread",
                AsyncMock(return_value=dict(self.THREAD)),
            ),
            patch(
                "chainlit.socket.config", self._config(on_chat_resume, on_thread_ready)
            ),
        ):
            with pytest.raises(RuntimeError):
                await connection_successful("sid-1")

        assert on_thread_ready.call_count == 1
        assert session.connection_inited.is_set()
        await session.thread_ready_task

    @pytest.mark.asyncio
    async def test_hook_launches_even_if_restore_raises(self, mock_session_factory):
        """§3.3 three-tier finally: a restore_pending_ask crash must not eat
        the hook launch while the gate opens anyway."""
        on_thread_ready = AsyncMock()
        session = self._session(mock_session_factory)
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch(
                "chainlit.socket.resume_thread",
                AsyncMock(return_value=dict(self.THREAD)),
            ),
            patch(
                "chainlit.socket.restore_pending_ask",
                AsyncMock(side_effect=RuntimeError("restore crashed")),
            ),
            patch("chainlit.socket.config", self._config(AsyncMock(), on_thread_ready)),
        ):
            with pytest.raises(RuntimeError):
                await connection_successful("sid-1")

        assert on_thread_ready.call_count == 1
        assert session.connection_inited.is_set()
        await session.thread_ready_task

    @pytest.mark.asyncio
    async def test_gate_opens_after_slot_assignment(self, mock_session_factory):
        """§3.3 ordering: when the deferred-conversion gate opens, the hook
        slot must already be occupied."""
        on_thread_ready = AsyncMock()
        session = self._session(mock_session_factory)
        slot_at_gate_open = []
        real_set = session.connection_inited.set
        session.connection_inited = Mock()
        session.connection_inited.set = Mock(
            side_effect=lambda: (
                slot_at_gate_open.append(session.thread_ready_task),
                real_set(),
            )
        )
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch(
                "chainlit.socket.resume_thread",
                AsyncMock(return_value=dict(self.THREAD)),
            ),
            patch("chainlit.socket.config", self._config(AsyncMock(), on_thread_ready)),
        ):
            await connection_successful("sid-1")

        assert slot_at_gate_open
        assert slot_at_gate_open[0] is not None
        await session.thread_ready_task


class TestReconnectResync:
    """The indicator resync is the final word of the handshake (§3.5)."""

    def _session(self, mock_session_factory, **kwargs):
        session = mock_session_factory(**kwargs)
        session.restored = True
        session.chat_started = True
        session.thread_id_to_resume = None
        session.thread_ready_task = None
        session.resume_task_started = False
        return session

    def _context(self, session):
        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()
        return mock_context

    def _config(self):
        mock_config = Mock()
        mock_config.code.on_chat_start = None
        mock_config.code.on_chat_resume = None
        mock_config.code.on_thread_ready = None
        return mock_config

    @pytest.mark.asyncio
    async def test_live_counter_relights_after_reconnect(self, mock_session_factory):
        """Acceptance 1: after a reconnect during live work (no live ask)
        the indicator must come back on."""
        session = self._session(mock_session_factory)
        session.task_counter = 1
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config()),
        ):
            await connection_successful("sid-1")

        # :643 still darkens the indicator early; the resync has the final
        # word.
        indicator_calls = [
            name
            for name, *_ in context.emitter.method_calls
            if name in ("task_start", "task_end")
        ]
        assert indicator_calls
        assert indicator_calls[-1] == "task_start"

    @pytest.mark.asyncio
    async def test_idle_counter_stays_dark_after_reconnect(self, mock_session_factory):
        session = self._session(mock_session_factory)
        session.task_counter = 0
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config()),
        ):
            await connection_successful("sid-1")

        context.emitter.task_start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_live_ask_wins_over_counter(self, mock_session_factory):
        """With a live restored ask the composer is in ask mode — the resync
        must not light the indicator over it."""
        pending = Mock()
        pending.is_live = True
        session = self._session(mock_session_factory, pending_ask=pending)
        session.task_counter = 1
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config()),
            patch("chainlit.socket.restore_pending_ask", AsyncMock()),
        ):
            await connection_successful("sid-1")

        context.emitter.task_start.assert_not_awaited()


class TestDecoratorRegistration:
    def test_on_thread_ready_registers_callback(self, test_config):
        from chainlit.callbacks import on_thread_ready

        async def hook(thread):
            pass

        registered = on_thread_ready(hook)

        assert registered is hook
        assert test_config.code.on_thread_ready is not None
