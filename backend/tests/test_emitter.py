from unittest.mock import AsyncMock, MagicMock

import pytest

import chainlit.transit as transit
from chainlit.data.base import BaseDataLayer
from chainlit.element import ElementDict
from chainlit.emitter import ChainlitEmitter
from chainlit.step import StepDict


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


async def test_open_thread_defaults_to_keeping_transcript(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    await emitter.open_thread("thread-a")

    mock_websocket_session.emit.assert_called_once_with(
        "open_thread",
        {"threadId": "thread-a", "keepTranscript": True},
    )


async def test_open_thread_without_transcript(
    emitter: ChainlitEmitter, mock_websocket_session: MagicMock
) -> None:
    await emitter.open_thread("thread-a", keep_transcript=False)

    mock_websocket_session.emit.assert_called_once_with(
        "open_thread",
        {"threadId": "thread-a", "keepTranscript": False},
    )


async def test_open_thread_rejects_positional_flags(
    emitter: ChainlitEmitter,
) -> None:
    with pytest.raises(TypeError):
        await emitter.open_thread("thread-a", False)  # type: ignore[misc]


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
