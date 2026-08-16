import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from socketio.exceptions import TimeoutError as SocketIOTimeoutError

import chainlit.transit as transit
from chainlit.data.base import BaseDataLayer
from chainlit.element import ElementDict
from chainlit.emitter import ChainlitEmitter
from chainlit.step import StepDict
from chainlit.types import AskFileSpec, AskSpec


@pytest.fixture(autouse=True)
def clean_transit_store():
    # The transit store is module-level state; without this, records parked
    # by one test leak into the next.
    transit.clear()
    yield
    transit.clear()


@pytest.fixture
def emitter(mock_websocket_session):
    return ChainlitEmitter(mock_websocket_session)


async def test_send_element(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    element_dict: ElementDict = {
        "id": "test_element",
        "threadId": None,
        "type": "text",
        "chainlitKey": None,
        "url": None,
        "objectKey": None,
        "name": "Test Element",
        "display": "inline",
        "size": None,
        "language": None,
        "page": None,
        "props": None,
        "autoPlay": None,
        "playerConfig": None,
        "forId": None,
        "mime": None,
    }

    await emitter.send_element(element_dict)

    mock_websocket_session.emit.assert_called_once_with("element", element_dict)


async def test_send_step(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    step_dict: StepDict = {
        "id": "test_step",
        "type": "user_message",
        "name": "Test Step",
        "output": "This is a test step",
    }

    await emitter.send_step(step_dict)

    mock_websocket_session.emit.assert_called_once_with("new_message", step_dict)


async def test_send_step_with_icon(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    step_dict: StepDict = {
        "id": "test_step_with_icon",
        "type": "tool",
        "name": "Test Step with Icon",
        "output": "This is a test step with an icon",
        "metadata": {"icon": "search"},
    }

    await emitter.send_step(step_dict)

    mock_websocket_session.emit.assert_called_once_with("new_message", step_dict)


async def test_update_step(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    step_dict: StepDict = {
        "id": "test_step",
        "type": "assistant_message",
        "name": "Updated Test Step",
        "output": "This is an updated test step",
    }

    await emitter.update_step(step_dict)

    mock_websocket_session.emit.assert_called_once_with("update_message", step_dict)


async def test_update_step_with_icon(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    step_dict: StepDict = {
        "id": "test_step_with_icon",
        "type": "tool",
        "name": "Updated Test Step with Icon",
        "output": "This is an updated test step with an icon",
        "metadata": {"icon": "database"},
    }

    await emitter.update_step(step_dict)

    mock_websocket_session.emit.assert_called_once_with("update_message", step_dict)


async def test_delete_step(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    step_dict: StepDict = {
        "id": "test_step",
        "type": "system_message",
        "name": "Deleted Test Step",
        "output": "This step will be deleted",
    }

    await emitter.delete_step(step_dict)

    mock_websocket_session.emit.assert_called_once_with("delete_message", step_dict)


async def test_send_timeout(emitter, mock_websocket_session):
    await emitter.send_timeout("ask_timeout")
    mock_websocket_session.emit.assert_called_once_with("ask_timeout", {})


async def test_clear(emitter, mock_websocket_session):
    await emitter.clear("clear_ask")
    mock_websocket_session.emit.assert_called_once_with("clear_ask", {})


async def test_send_token(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    await emitter.send_token("test_id", "test_token", is_sequence=True, is_input=False)
    mock_websocket_session.emit.assert_called_once_with(
        "stream_token",
        {"id": "test_id", "token": "test_token", "isSequence": True, "isInput": False},
    )


async def test_set_chat_settings(emitter, mock_websocket_session):
    settings = {"key": "value"}
    emitter.set_chat_settings(settings)
    assert emitter.session.chat_settings == settings


async def test_update_token_count(emitter, mock_websocket_session):
    count = 100
    await emitter.update_token_count(count)
    mock_websocket_session.emit.assert_called_once_with("token_usage", count)


async def test_task_start(emitter, mock_websocket_session):
    await emitter.task_start()
    mock_websocket_session.emit.assert_called_once_with("task_start", {})


async def test_task_end(emitter, mock_websocket_session):
    await emitter.task_end()
    mock_websocket_session.emit.assert_called_once_with("task_end", {})


async def test_stream_start(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    step_dict: StepDict = {
        "id": "test_stream",
        "type": "run",
        "name": "Test Stream",
        "output": "This is a test stream",
    }
    await emitter.stream_start(step_dict)
    mock_websocket_session.emit.assert_called_once_with("stream_start", step_dict)


async def test_stream_start_with_icon(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    step_dict: StepDict = {
        "id": "test_stream_with_icon",
        "type": "tool",
        "name": "Test Stream with Icon",
        "output": "This is a test stream with an icon",
        "metadata": {"icon": "cpu"},
    }
    await emitter.stream_start(step_dict)
    mock_websocket_session.emit.assert_called_once_with("stream_start", step_dict)


async def test_send_toast(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    message = "This is a test message"
    await emitter.send_toast(message)
    mock_websocket_session.emit.assert_called_once_with(
        "toast", {"message": message, "type": "info"}
    )


async def test_send_toast_with_type(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    message = "This is a test message"
    await emitter.send_toast(message, type="error")
    mock_websocket_session.emit.assert_called_once_with(
        "toast", {"message": message, "type": "error"}
    )


async def test_send_toast_invalid_type(emitter: ChainlitEmitter) -> None:
    message = "This is a test message"
    with pytest.raises(ValueError, match="Invalid toast type: invalid"):
        await emitter.send_toast(message, type="invalid")  # type: ignore[arg-type]


async def test_set_chat_profile_defaults(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    mock_websocket_session.id = "session-defaults"
    mock_websocket_session.user = None
    mock_websocket_session.has_first_interaction = False

    await emitter.set_chat_profile("GPT-4")

    mock_websocket_session.emit.assert_called_once_with(
        "set_chat_profile",
        {"name": "GPT-4", "keepTranscript": False, "hasTransitMessage": False},
    )
    # Nothing was parked for the next session: no message, and no parent
    # either — the thread row does not exist before the first interaction.
    assert transit.pop("session-defaults", None) is transit.NO_TRANSIT


async def test_set_chat_profile_with_options(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    mock_websocket_session.id = "session-options"
    mock_websocket_session.user = None
    mock_websocket_session.has_first_interaction = False

    await emitter.set_chat_profile(
        "Search", keep_transcript=True, transit_message="knife sharpener"
    )

    mock_websocket_session.emit.assert_called_once_with(
        "set_chat_profile",
        {
            "name": "Search",
            "keepTranscript": True,
            "hasTransitMessage": True,
        },
    )
    record = transit.pop("session-options", None)
    assert record.value == "knife sharpener"
    assert record.parent is None


async def test_set_chat_profile_parks_parent_after_first_interaction(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    mock_websocket_session.id = "session-parent"
    mock_websocket_session.user = None
    mock_websocket_session.has_first_interaction = True
    mock_websocket_session.thread_id = "thread-a"

    await emitter.set_chat_profile("Search", transit_message="knife sharpener")

    record = transit.pop("session-parent", None)
    assert record.value == "knife sharpener"
    assert record.parent == "thread-a"


async def test_set_chat_profile_parks_parent_only_record(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    # A switch without a transit message still hands over the parent link.
    mock_websocket_session.id = "session-parent-only"
    mock_websocket_session.user = None
    mock_websocket_session.has_first_interaction = True
    mock_websocket_session.thread_id = "thread-a"

    await emitter.set_chat_profile("Search")

    mock_websocket_session.emit.assert_called_once_with(
        "set_chat_profile",
        {"name": "Search", "keepTranscript": False, "hasTransitMessage": False},
    )
    record = transit.pop("session-parent-only", None)
    assert record.value is None
    assert record.parent == "thread-a"


async def test_set_chat_profile_none_clears_parked_transit(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    mock_websocket_session.id = "session-clears"
    mock_websocket_session.user = None
    mock_websocket_session.has_first_interaction = False

    await emitter.set_chat_profile("Search", transit_message="first")
    await emitter.set_chat_profile("Search")

    assert transit.pop("session-clears", None) is transit.NO_TRANSIT


async def test_set_chat_profile_rejects_positional_flags(
    emitter: ChainlitEmitter,
) -> None:
    with pytest.raises(TypeError):
        await emitter.set_chat_profile("Search", True)  # type: ignore[misc]


@pytest.fixture
def flush_session(mock_websocket_session: MagicMock) -> MagicMock:
    mock_websocket_session.thread_id = "thread-b"
    mock_websocket_session.user = None
    mock_websocket_session.chat_profile = None
    mock_websocket_session.parent_thread_id = None
    mock_websocket_session.flush_method_queue = AsyncMock()
    return mock_websocket_session


@pytest.fixture
def flush_data_layer(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    # Patched at the emitter's import site: get_data_layer() caches its
    # instance in a module global, which would leak across tests.
    data_layer = AsyncMock(spec=BaseDataLayer)
    monkeypatch.setattr("chainlit.emitter.get_data_layer", lambda: data_layer)
    return data_layer


async def test_flush_thread_queues_without_parent(
    emitter: ChainlitEmitter,
    flush_session: MagicMock,
    flush_data_layer: AsyncMock,
) -> None:
    await emitter.flush_thread_queues("hello")

    flush_data_layer.update_thread.assert_awaited_once_with(
        thread_id="thread-b", name="hello", user_id=None, tags=None
    )


async def test_flush_thread_queues_passes_parent(
    emitter: ChainlitEmitter,
    flush_session: MagicMock,
    flush_data_layer: AsyncMock,
) -> None:
    flush_session.parent_thread_id = "thread-a"

    await emitter.flush_thread_queues("hello")

    flush_data_layer.update_thread.assert_awaited_once_with(
        thread_id="thread-b",
        name="hello",
        user_id=None,
        tags=None,
        parent_thread_id="thread-a",
    )


async def test_flush_thread_queues_retries_without_parent_for_old_layers(
    emitter: ChainlitEmitter,
    flush_session: MagicMock,
    flush_data_layer: AsyncMock,
) -> None:
    # Third-party data layers predating parent_thread_id raise TypeError on
    # the unknown kwarg; the thread must still be created, without the link.
    flush_session.parent_thread_id = "thread-a"
    seen = []

    async def legacy_update_thread(
        thread_id, name=None, user_id=None, metadata=None, tags=None
    ):
        seen.append({"thread_id": thread_id, "name": name})

    flush_data_layer.update_thread = legacy_update_thread

    await emitter.flush_thread_queues("hello")

    assert seen == [{"thread_id": "thread-b", "name": "hello"}]


class TestSendAskUser:
    """send_ask_user waits on a session-level future resolved by ask_reply."""

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

    async def test_reply_resolves_and_clears_slot(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ) -> None:
        mock_websocket_session.has_first_interaction = True
        action_res = {"name": "continue", "id": "a1", "label": "Continue"}

        task = asyncio.ensure_future(
            emitter.send_ask_user(self._step_dict(), self._action_spec())
        )
        await self._resolve_when_pending(mock_websocket_session, action_res)
        result = await task

        assert result == action_res
        assert mock_websocket_session.pending_ask is None
        emitted_events = [
            call.args[0] for call in mock_websocket_session.emit.call_args_list
        ]
        assert "ask" in emitted_events
        assert "clear_ask" in emitted_events
        assert "task_start" in emitted_events  # finally block ran

    async def test_ask_payload_uses_plain_emit(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ) -> None:
        step_dict = self._step_dict()
        spec = self._action_spec()

        task = asyncio.ensure_future(emitter.send_ask_user(step_dict, spec))
        await self._resolve_when_pending(mock_websocket_session, {"name": "x"})
        await task

        mock_websocket_session.emit.assert_any_call(
            "ask", {"msg": step_dict, "spec": spec.to_dict()}
        )
        mock_websocket_session.emit_call.assert_not_called()

    async def test_falsy_reply_returns_none(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ) -> None:
        task = asyncio.ensure_future(
            emitter.send_ask_user(self._step_dict(), self._action_spec())
        )
        await self._resolve_when_pending(mock_websocket_session, None)
        result = await task

        assert result is None
        emitted_events = [
            call.args[0] for call in mock_websocket_session.emit.call_args_list
        ]
        assert "clear_ask" in emitted_events

    async def test_timeout_sends_ask_timeout_and_returns_none(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ) -> None:
        result = await emitter.send_ask_user(
            self._step_dict(), self._action_spec(timeout=0)
        )

        assert result is None
        assert mock_websocket_session.pending_ask is None
        emitted_events = [
            call.args[0] for call in mock_websocket_session.emit.call_args_list
        ]
        assert "ask_timeout" in emitted_events
        assert "task_start" in emitted_events

    async def test_timeout_raises_socketio_timeout_error(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ) -> None:
        with pytest.raises(SocketIOTimeoutError):
            await emitter.send_ask_user(
                self._step_dict(), self._action_spec(timeout=0), True
            )

        assert mock_websocket_session.pending_ask is None

    async def test_busy_slot_returns_none_without_touching_pending(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ) -> None:
        existing = Mock()
        existing.future.done.return_value = False
        mock_websocket_session.pending_ask = existing

        result = await emitter.send_ask_user(self._step_dict(), self._action_spec())

        assert result is None
        assert mock_websocket_session.pending_ask is existing
        mock_websocket_session.emit.assert_not_called()

    async def test_file_ask_sets_and_clears_files_spec(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ) -> None:
        spec = AskFileSpec(
            timeout=10,
            type="file",
            step_id="step-1",
            accept=["text/plain"],
            max_files=1,
            max_size_mb=2,
        )
        seen_specs = {}

        task = asyncio.ensure_future(emitter.send_ask_user(self._step_dict(), spec))
        while mock_websocket_session.pending_ask is None:
            await asyncio.sleep(0)
        seen_specs = dict(mock_websocket_session.files_spec)
        mock_websocket_session.pending_ask.future.set_result(None)
        await task

        assert seen_specs == {"parent-1": spec}
        assert mock_websocket_session.files_spec == {}

    async def test_cancelled_future_propagates_and_clears_slot(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ) -> None:
        task = asyncio.ensure_future(
            emitter.send_ask_user(self._step_dict(), self._action_spec())
        )
        while mock_websocket_session.pending_ask is None:
            await asyncio.sleep(0)
        mock_websocket_session.pending_ask.future.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert mock_websocket_session.pending_ask is None
        emitted_events = [
            call.args[0] for call in mock_websocket_session.emit.call_args_list
        ]
        assert "ask_timeout" not in emitted_events

    async def test_deadline_uses_monotonic_clock(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ) -> None:
        task = asyncio.ensure_future(
            emitter.send_ask_user(self._step_dict(), self._action_spec(timeout=60))
        )
        while mock_websocket_session.pending_ask is None:
            await asyncio.sleep(0)
        pending = mock_websocket_session.pending_ask

        assert 0 < pending.remaining <= 60
        assert not pending.expired

        pending.future.set_result(None)
        await task


class TestSendAskUserRestorePayloads:
    """restore_* kwargs must land in the PendingAsk slot."""

    async def test_restore_kwargs_stored_in_pending_ask(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ) -> None:
        element = Mock()
        action_dict = {"id": "a1"}
        step_dict: StepDict = {
            "id": "step-1",
            "parentId": "parent-1",
            "type": "assistant_message",
            "name": "ask",
            "output": "Pick",
        }
        spec = AskSpec(timeout=10, type="action", step_id="step-1")

        task = asyncio.ensure_future(
            emitter.send_ask_user(
                step_dict,
                spec,
                restore_actions=[action_dict],
                restore_element=element,
            )
        )
        while mock_websocket_session.pending_ask is None:
            await asyncio.sleep(0)
        pending = mock_websocket_session.pending_ask

        assert pending.restore_actions == [action_dict]
        assert pending.restore_element is element

        pending.future.set_result(None)
        await task

    async def test_base_emitter_stub_accepts_restore_kwargs(
        self, mock_websocket_session: MagicMock
    ) -> None:
        from chainlit.emitter import BaseChainlitEmitter

        stub = BaseChainlitEmitter(mock_websocket_session)
        result = await stub.send_ask_user(
            {"id": "s", "parentId": "p"},  # type: ignore[typeddict-item]
            AskSpec(timeout=1, type="text", step_id="s"),
            restore_actions=[{"id": "a"}],
            restore_element=Mock(),
        )
        assert result is None

    async def test_legacy_ack_resolves_future_when_emit_ask_available(
        self, emitter: ChainlitEmitter, mock_websocket_session: MagicMock
    ) -> None:
        """A session with emit_ask sends the ask through it, and the attached
        legacy ack resolves the same future (stale cached bundles)."""
        captured = {}

        async def emit_ask(payload, callback):
            captured["payload"] = payload
            captured["ack"] = callback

        mock_websocket_session.emit_ask = emit_ask
        step_dict: StepDict = {
            "id": "step-1",
            "parentId": "parent-1",
            "type": "assistant_message",
            "name": "ask",
            "output": "Pick",
        }
        spec = AskSpec(timeout=10, type="action", step_id="step-1")

        task = asyncio.ensure_future(emitter.send_ask_user(step_dict, spec))
        while "ack" not in captured:
            await asyncio.sleep(0)

        action_res = {"name": "go", "id": "a1", "label": "Go"}
        captured["ack"](action_res)
        # A duplicate ack must be a no-op, not an InvalidStateError.
        captured["ack"]({"name": "other"})

        result = await task
        assert result == action_res
        assert captured["payload"]["msg"] == step_dict
