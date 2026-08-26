import asyncio
import json
import time
import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest

import chainlit.transit as transit
from chainlit.session import (
    PendingAsk,
    WebsocketSession,
)
from chainlit.socket import (
    _authenticate_connection,
    _get_token,
    _get_token_from_cookie,
    apply_transit_message,
    ask_reply,
    clean_session,
    connect,
    connection_successful,
    disconnect,
    load_user_env,
    persist_user_session,
    restore_existing_session,
    restore_pending_ask,
    resume_thread,
    stop,
)
from chainlit.types import AskSpec
from chainlit.user_session import user_sessions


class TestGetTokenFromCookie:
    """Test suite for _get_token_from_cookie function."""

    def test_get_token_from_cookie_with_valid_cookie(self):
        """Test extracting token from valid cookie header."""
        with patch("chainlit.socket.get_token_from_cookies") as mock_get_token:
            mock_get_token.return_value = "test_token"
            environ = {"HTTP_COOKIE": "session=abc123; token=test_token"}

            result = _get_token_from_cookie(environ)

            assert result == "test_token"
            mock_get_token.assert_called_once()

    def test_get_token_from_cookie_without_cookie(self):
        """Test when no cookie header is present."""
        environ = {}
        result = _get_token_from_cookie(environ)
        assert result is None

    def test_get_token_from_cookie_with_empty_cookie(self):
        """Test with empty cookie header."""
        with patch("chainlit.socket.get_token_from_cookies") as mock_get_token:
            mock_get_token.return_value = None
            environ = {"HTTP_COOKIE": ""}

            result = _get_token_from_cookie(environ)

            assert result is None


class TestGetToken:
    """Test suite for _get_token function."""

    def test_get_token_calls_get_token_from_cookie(self):
        """Test that _get_token delegates to _get_token_from_cookie."""
        with patch("chainlit.socket._get_token_from_cookie") as mock_get_cookie:
            mock_get_cookie.return_value = "token_value"
            environ = {"HTTP_COOKIE": "token=token_value"}

            result = _get_token(environ)

            assert result == "token_value"
            mock_get_cookie.assert_called_once_with(environ)


class TestAuthenticateConnection:
    """Test suite for _authenticate_connection function."""

    @pytest.mark.asyncio
    async def test_authenticate_connection_with_valid_token(self):
        """Test authentication with valid token."""
        mock_user = Mock()
        mock_user.identifier = "user123"

        with patch("chainlit.socket._get_token") as mock_get_token:
            with patch("chainlit.socket.get_current_user") as mock_get_user:
                mock_get_token.return_value = "valid_token"
                mock_get_user.return_value = mock_user

                environ = {"HTTP_COOKIE": "token=valid_token"}
                user, token = await _authenticate_connection(environ)

                assert user == mock_user
                assert token == "valid_token"
                mock_get_user.assert_called_once_with(token="valid_token")

    @pytest.mark.asyncio
    async def test_authenticate_connection_without_token(self):
        """Test authentication without token."""
        with patch("chainlit.socket._get_token") as mock_get_token:
            mock_get_token.return_value = None

            environ = {}
            user, token = await _authenticate_connection(environ)

            assert user is None
            assert token is None

    @pytest.mark.asyncio
    async def test_authenticate_connection_with_invalid_token(self):
        """Test authentication with invalid token."""
        with patch("chainlit.socket._get_token") as mock_get_token:
            with patch("chainlit.socket.get_current_user") as mock_get_user:
                mock_get_token.return_value = "invalid_token"
                mock_get_user.return_value = None

                environ = {"HTTP_COOKIE": "token=invalid_token"}
                user, token = await _authenticate_connection(environ)

                assert user is None
                assert token is None


class TestRestoreExistingSession:
    """Test suite for restore_existing_session function."""

    def test_restore_existing_session_success(self):
        """Test restoring an existing session."""
        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = None
        emit_fn = Mock()
        emit_call_fn = Mock()
        emit_ask_fn = Mock()
        environ = {"HTTP_COOKIE": "token=token"}

        with patch.object(WebsocketSession, "get_by_id") as mock_get:
            mock_get.return_value = mock_session

            result = restore_existing_session(
                "new_sid",
                "session_123",
                emit_fn,
                emit_call_fn,
                environ,
                emit_ask_fn=emit_ask_fn,
            )

            assert result is True
            mock_session.restore.assert_called_once_with(new_socket_id="new_sid")
            assert mock_session.emit == emit_fn
            assert mock_session.emit_call == emit_call_fn
            assert mock_session.emit_ask == emit_ask_fn
            assert mock_session.environ == environ

    def test_restore_existing_session_with_matching_user(self):
        """Test restoring a session for its authenticated owner."""
        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = Mock(identifier="user123")
        authenticated_user = Mock(identifier="user123")

        with patch.object(WebsocketSession, "get_by_id") as mock_get:
            mock_get.return_value = mock_session

            result = restore_existing_session(
                "new_sid",
                "session_123",
                Mock(),
                Mock(),
                {"HTTP_COOKIE": "token=token"},
                user=authenticated_user,
                emit_ask_fn=Mock(),
            )

            assert result is True
            mock_session.restore.assert_called_once_with(new_socket_id="new_sid")

    def test_restore_existing_session_rejects_different_user(self):
        """Test that a session cannot be restored by another user."""
        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = Mock(identifier="victim")
        authenticated_user = Mock(identifier="attacker")

        with patch.object(WebsocketSession, "get_by_id") as mock_get:
            mock_get.return_value = mock_session

            with pytest.raises(ConnectionRefusedError, match="authorization failed"):
                restore_existing_session(
                    "new_sid",
                    "session_123",
                    Mock(),
                    Mock(),
                    {"HTTP_COOKIE": "token=token"},
                    user=authenticated_user,
                    emit_ask_fn=Mock(),
                )

            mock_session.restore.assert_not_called()

    def test_restore_existing_session_not_found(self):
        """Test when session is not found."""
        with patch.object(WebsocketSession, "get_by_id") as mock_get:
            mock_get.return_value = None

            result = restore_existing_session(
                "new_sid",
                "session_123",
                Mock(),
                Mock(),
                {"HTTP_COOKIE": "token=token"},
                emit_ask_fn=Mock(),
            )

            assert result is False


class TestPersistUserSession:
    """Test suite for persist_user_session function."""

    @pytest.mark.asyncio
    async def test_persist_user_session_with_data_layer(self):
        """Test persisting user session with data layer."""
        mock_data_layer = AsyncMock()

        with patch("chainlit.socket.get_data_layer") as mock_get_dl:
            mock_get_dl.return_value = mock_data_layer

            metadata = {"key": "value"}
            await persist_user_session("thread_123", metadata)

            mock_data_layer.update_thread.assert_called_once_with(
                thread_id="thread_123", metadata=metadata
            )

    @pytest.mark.asyncio
    async def test_persist_user_session_without_data_layer(self):
        """Test persisting when no data layer is available."""
        with patch("chainlit.socket.get_data_layer") as mock_get_dl:
            mock_get_dl.return_value = None

            # Should not raise an error
            await persist_user_session("thread_123", {"key": "value"})


class TestResumeThread:
    """Test suite for resume_thread function."""

    @pytest.mark.asyncio
    async def test_resume_thread_without_data_layer(self):
        """Test resume thread when no data layer exists."""
        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = Mock()
        mock_session.thread_id_to_resume = "thread_123"

        with patch("chainlit.socket.get_data_layer") as mock_get_dl:
            mock_get_dl.return_value = None

            result = await resume_thread(mock_session)

            assert result is None

    @pytest.mark.asyncio
    async def test_resume_thread_without_user(self):
        """Test resume thread when session has no user."""
        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = None
        mock_session.thread_id_to_resume = "thread_123"

        result = await resume_thread(mock_session)

        assert result is None

    @pytest.mark.asyncio
    async def test_resume_thread_without_thread_id(self):
        """Test resume thread when no thread_id_to_resume."""
        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = Mock()
        mock_session.thread_id_to_resume = None

        result = await resume_thread(mock_session)

        assert result is None

    @pytest.mark.asyncio
    async def test_resume_thread_thread_not_found(self):
        """Test resume thread when thread doesn't exist."""
        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = Mock(identifier="user123")
        mock_session.thread_id_to_resume = "thread_123"
        mock_session.id = "session_123"

        mock_data_layer = AsyncMock()
        mock_data_layer.get_thread.return_value = None

        with patch("chainlit.socket.get_data_layer") as mock_get_dl:
            mock_get_dl.return_value = mock_data_layer

            result = await resume_thread(mock_session)

            assert result is None
            mock_data_layer.get_thread.assert_called_once_with(thread_id="thread_123")

    @pytest.mark.asyncio
    async def test_resume_thread_user_not_author(self):
        """Test resume thread when user is not the thread author."""
        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = Mock(identifier="user123")
        mock_session.thread_id_to_resume = "thread_123"
        mock_session.id = "session_123"

        thread = {"userIdentifier": "different_user", "metadata": {}}
        mock_data_layer = AsyncMock()
        mock_data_layer.get_thread.return_value = thread

        with patch("chainlit.socket.get_data_layer") as mock_get_dl:
            mock_get_dl.return_value = mock_data_layer

            result = await resume_thread(mock_session)

            assert result is None

    @pytest.mark.asyncio
    async def test_resume_thread_success(self):
        """Test successful thread resumption."""
        from chainlit.user_session import user_sessions

        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = Mock(identifier="user123")
        mock_session.thread_id_to_resume = "thread_123"
        mock_session.id = "session_123"

        metadata = {
            "chat_profile": "gpt-4",
            "chat_settings": {"temperature": 0.7},
        }
        thread = {"userIdentifier": "user123", "metadata": metadata}

        mock_data_layer = AsyncMock()
        mock_data_layer.get_thread.return_value = thread

        original_sessions = user_sessions.copy()
        try:
            with patch("chainlit.socket.get_data_layer") as mock_get_dl:
                mock_get_dl.return_value = mock_data_layer

                result = await resume_thread(mock_session)

                assert result == thread
                assert mock_session.chat_profile == "gpt-4"
                assert mock_session.chat_settings == {"temperature": 0.7}
                assert user_sessions.get("session_123") == metadata
        finally:
            user_sessions.clear()
            user_sessions.update(original_sessions)

    @pytest.mark.asyncio
    async def test_resume_thread_with_string_metadata(self):
        """Test thread resumption with JSON string metadata."""
        from chainlit.user_session import user_sessions

        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = Mock(identifier="user123")
        mock_session.thread_id_to_resume = "thread_123"
        mock_session.id = "session_123"

        metadata_dict = {"chat_profile": "gpt-4"}
        thread = {
            "userIdentifier": "user123",
            "metadata": json.dumps(metadata_dict),
        }

        mock_data_layer = AsyncMock()
        mock_data_layer.get_thread.return_value = thread

        original_sessions = user_sessions.copy()
        try:
            with patch("chainlit.socket.get_data_layer") as mock_get_dl:
                mock_get_dl.return_value = mock_data_layer

                result = await resume_thread(mock_session)

                assert result == thread
                assert mock_session.chat_profile == "gpt-4"
        finally:
            user_sessions.clear()
            user_sessions.update(original_sessions)


class TestLoadUserEnv:
    """Test suite for load_user_env function."""

    def test_load_user_env_with_valid_json(self):
        """Test loading valid user environment JSON."""
        user_env = '{"API_KEY": "secret", "ENV_VAR": "value"}'

        with patch("chainlit.socket.config") as mock_config:
            mock_config.project.user_env = []

            result = load_user_env(user_env)

            assert result == {"API_KEY": "secret", "ENV_VAR": "value"}

    def test_load_user_env_with_required_keys(self):
        """Test loading user env with required keys."""
        user_env = '{"API_KEY": "secret", "OTHER_KEY": "value"}'

        with patch("chainlit.socket.config") as mock_config:
            mock_config.project.user_env = ["API_KEY", "OTHER_KEY"]

            result = load_user_env(user_env)

            assert result == {"API_KEY": "secret", "OTHER_KEY": "value"}

    def test_load_user_env_missing_required_key(self):
        """Test error when required key is missing."""
        user_env = '{"API_KEY": "secret"}'

        with patch("chainlit.socket.config") as mock_config:
            mock_config.project.user_env = ["API_KEY", "MISSING_KEY"]

            with pytest.raises(
                ConnectionRefusedError, match="Missing user environment variable"
            ):
                load_user_env(user_env)

    def test_load_user_env_none_with_required_keys(self):
        """Test error when user_env is None but keys are required."""
        with patch("chainlit.socket.config") as mock_config:
            mock_config.project.user_env = ["API_KEY"]

            with pytest.raises(
                ConnectionRefusedError, match="Missing user environment variables"
            ):
                load_user_env(None)

    def test_load_user_env_none_without_required_keys(self):
        """Test when user_env is None and no keys are required."""
        with patch("chainlit.socket.config") as mock_config:
            mock_config.project.user_env = []

            result = load_user_env(None)

            assert result == {}


class TestCleanSession:
    """Test suite for clean_session function."""

    @pytest.mark.asyncio
    async def test_clean_session_with_existing_session(self):
        """Test marking session for cleanup."""
        mock_session = Mock(spec=WebsocketSession)
        mock_session.to_clear = False

        with patch.object(WebsocketSession, "get") as mock_get:
            mock_get.return_value = mock_session

            await clean_session("socket_123")

            assert mock_session.to_clear is True
            mock_get.assert_called_once_with("socket_123")

    @pytest.mark.asyncio
    async def test_clean_session_without_session(self):
        """Test clean_session when session doesn't exist."""
        with patch.object(WebsocketSession, "get") as mock_get:
            mock_get.return_value = None

            # Should not raise an error
            await clean_session("socket_123")


class TestSocketEdgeCases:
    """Test suite for socket edge cases."""

    def test_restore_existing_session_with_none_session_id(self):
        """Test restore with None session_id."""
        with patch.object(WebsocketSession, "get_by_id") as mock_get:
            mock_get.return_value = None

            result = restore_existing_session(
                None, None, Mock(), Mock(), None, emit_ask_fn=Mock()
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_persist_user_session_with_empty_metadata(self):
        """Test persisting empty metadata."""
        mock_data_layer = AsyncMock()

        with patch("chainlit.socket.get_data_layer") as mock_get_dl:
            mock_get_dl.return_value = mock_data_layer

            await persist_user_session("thread_123", {})

            mock_data_layer.update_thread.assert_called_once_with(
                thread_id="thread_123", metadata={}
            )

    def test_load_user_env_with_empty_json(self):
        """Test loading empty user environment."""
        user_env = "{}"

        with patch("chainlit.socket.config") as mock_config:
            mock_config.project.user_env = []

            result = load_user_env(user_env)

            assert result == {}

    @pytest.mark.asyncio
    async def test_resume_thread_with_empty_metadata(self):
        """Test resuming thread with empty metadata."""
        from chainlit.user_session import user_sessions

        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = Mock(identifier="user123")
        mock_session.thread_id_to_resume = "thread_123"
        mock_session.id = "session_123"

        thread = {"userIdentifier": "user123", "metadata": {}}

        mock_data_layer = AsyncMock()
        mock_data_layer.get_thread.return_value = thread

        original_sessions = user_sessions.copy()
        try:
            with patch("chainlit.socket.get_data_layer") as mock_get_dl:
                mock_get_dl.return_value = mock_data_layer

                result = await resume_thread(mock_session)

                assert result == thread
                assert user_sessions.get("session_123") == {}
        finally:
            user_sessions.clear()
            user_sessions.update(original_sessions)

    @pytest.mark.asyncio
    async def test_authenticate_connection_with_exception(self):
        """Test authentication when get_current_user raises exception."""
        with patch("chainlit.socket._get_token") as mock_get_token:
            with patch("chainlit.socket.get_current_user") as mock_get_user:
                mock_get_token.return_value = "token"
                mock_get_user.side_effect = Exception("Auth error")

                environ = {"HTTP_COOKIE": "token=token"}

                # Should propagate the exception
                with pytest.raises(Exception, match="Auth error"):
                    await _authenticate_connection(environ)


class TestConnectionSuccessfulIdempotency:
    """Regression tests: on_chat_start must fire exactly once per
    WebsocketSession, even when connection_successful is dispatched multiple
    times (Socket.IO reconnect, React StrictMode double-mount, reverse-proxy
    WS 101 retry).

    Refs chainlit#2535, chainlit#2549, chainlit#2228.
    """

    @pytest.mark.asyncio
    async def test_on_chat_start_not_duplicated_on_reconnect(
        self, mock_session_factory
    ):
        """Reconnect path (session.restored=True): on_chat_start scheduled once."""
        on_chat_start = AsyncMock()

        session = mock_session_factory(has_first_interaction=False)
        session.restored = True
        session.chat_started = False
        session.current_task = None
        session.thread_id_to_resume = None

        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()

        mock_config = Mock()
        mock_config.code.on_chat_start = on_chat_start
        mock_config.code.on_chat_resume = None
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None

        with (
            patch("chainlit.socket.init_ws_context", return_value=mock_context),
            patch("chainlit.socket.config", mock_config),
        ):
            await connection_successful("sid-1")
            # Simulate reconnect: same session object, chat_started now True.
            await connection_successful("sid-1")

        assert on_chat_start.call_count == 1, (
            "on_chat_start must be scheduled exactly once per WebsocketSession"
        )
        assert session.chat_started is True

    @pytest.mark.asyncio
    async def test_on_chat_start_fires_once_on_fresh_session(
        self, mock_session_factory
    ):
        """Normal one-connect path still greets exactly once."""
        on_chat_start = AsyncMock()

        session = mock_session_factory(has_first_interaction=False)
        session.restored = False
        session.chat_started = False
        session.current_task = None
        session.thread_id_to_resume = None

        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()

        mock_config = Mock()
        mock_config.code.on_chat_start = on_chat_start
        mock_config.code.on_chat_resume = None
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None

        with (
            patch("chainlit.socket.init_ws_context", return_value=mock_context),
            patch("chainlit.socket.config", mock_config),
        ):
            await connection_successful("sid-1")

        assert on_chat_start.call_count == 1
        assert session.chat_started is True

    @pytest.mark.asyncio
    async def test_on_chat_start_not_duplicated_on_fresh_then_reconnect(
        self, mock_session_factory
    ):
        """Fresh connect followed by a reconnect still fires exactly once."""
        on_chat_start = AsyncMock()

        session = mock_session_factory(has_first_interaction=False)
        session.restored = False
        session.chat_started = False
        session.current_task = None
        session.thread_id_to_resume = None

        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()

        mock_config = Mock()
        mock_config.code.on_chat_start = on_chat_start
        mock_config.code.on_chat_resume = None
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None

        with (
            patch("chainlit.socket.init_ws_context", return_value=mock_context),
            patch("chainlit.socket.config", mock_config),
        ):
            await connection_successful("sid-1")
            session.restored = True
            await connection_successful("sid-1")

        assert on_chat_start.call_count == 1


class TestSendParentThread:
    """connection_successful delivers the session's parent thread id.

    A fresh transit thread has its parent only on the session until the
    first interaction persists it, so the client must receive it on every
    (re)connect; the resumed-thread case travels via thread metadata
    instead and needs nothing here.
    """

    SESSION_ID = "parent_thread_session_id"

    @pytest.fixture(autouse=True)
    def clean_state(self):
        transit.clear()
        user_sessions.pop(self.SESSION_ID, None)
        yield
        transit.clear()
        user_sessions.pop(self.SESSION_ID, None)

    def make_context(self, mock_session_factory, **session_kwargs):
        session_kwargs.setdefault("id", self.SESSION_ID)
        session_kwargs.setdefault("user", None)
        session_kwargs.setdefault("has_first_interaction", False)
        session = mock_session_factory(**session_kwargs)
        session.restored = False
        session.chat_started = False
        session.current_task = None
        session.thread_id_to_resume = None

        context = Mock()
        context.session = session
        context.emitter = AsyncMock()
        return context

    def patch_environment(self, context):
        mock_config = Mock()
        mock_config.code.on_chat_start = None
        mock_config.code.on_chat_resume = None
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None
        return (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", mock_config),
        )

    @pytest.mark.asyncio
    async def test_fresh_transit_thread_receives_parent(self, mock_session_factory):
        context = self.make_context(mock_session_factory)
        transit.store(self.SESSION_ID, None, None, parent="thread-a")

        ws_patch, config_patch = self.patch_environment(context)
        with ws_patch, config_patch:
            await connection_successful("sid-1")

        context.emitter.emit.assert_any_await(
            "parent_thread", {"parentThreadId": "thread-a"}
        )

    @pytest.mark.asyncio
    async def test_reconnect_re_delivers_parent(self, mock_session_factory):
        # The restored-session branch of connection_successful must send the
        # parent again: the client's copy died with the previous socket.
        context = self.make_context(mock_session_factory)
        context.session.restored = True
        context.session.parent_thread_id = "thread-a"

        ws_patch, config_patch = self.patch_environment(context)
        with ws_patch, config_patch:
            await connection_successful("sid-1")

        context.emitter.emit.assert_any_await(
            "parent_thread", {"parentThreadId": "thread-a"}
        )

    @pytest.mark.asyncio
    async def test_session_without_parent_stays_silent(self, mock_session_factory):
        context = self.make_context(mock_session_factory)

        ws_patch, config_patch = self.patch_environment(context)
        with ws_patch, config_patch:
            await connection_successful("sid-1")

        assert not any(
            call.args[0] == "parent_thread"
            for call in context.emitter.emit.await_args_list
        )


class TestApplyTransitMessage:
    """apply_transit_message: a claimed transit record enters the new session."""

    SESSION_ID = "transit_session_id"

    @pytest.fixture(autouse=True)
    def clean_state(self):
        transit.clear()
        user_sessions.pop(self.SESSION_ID, None)
        yield
        transit.clear()
        user_sessions.pop(self.SESSION_ID, None)

    def make_context(self, mock_session_factory, **session_kwargs):
        session_kwargs.setdefault("id", self.SESSION_ID)
        session_kwargs.setdefault("user", None)
        session_kwargs.setdefault("has_first_interaction", False)
        session = mock_session_factory(**session_kwargs)
        session.parent_thread_id = None

        context = Mock()
        context.session = session
        context.emitter = AsyncMock()
        return context

    @pytest.mark.asyncio
    async def test_message_record_opens_thread(self, mock_session_factory):
        context = self.make_context(mock_session_factory)
        transit.store(self.SESSION_ID, "searching knife", None, parent="thread-a")

        await apply_transit_message(context)

        assert user_sessions[self.SESSION_ID]["transit_message"] == "searching knife"
        assert context.session.has_first_interaction is True
        assert context.session.parent_thread_id == "thread-a"
        context.emitter.init_thread.assert_awaited_once_with("searching knife")

    @pytest.mark.asyncio
    async def test_parent_only_record_stashes_parent_without_opening_thread(
        self, mock_session_factory
    ):
        # A switch without a transit message: the parent link waits on the
        # session, the thread is still created lazily on the first message.
        context = self.make_context(mock_session_factory)
        transit.store(self.SESSION_ID, None, None, parent="thread-a")

        await apply_transit_message(context)

        assert context.session.parent_thread_id == "thread-a"
        assert context.session.has_first_interaction is False
        context.emitter.init_thread.assert_not_awaited()
        assert "transit_message" not in user_sessions.get(self.SESSION_ID, {})
        # The record was consumed, not left behind for a later reconnect.
        assert transit.pop(self.SESSION_ID, None) is transit.NO_TRANSIT

    @pytest.mark.asyncio
    async def test_no_record_is_a_noop(self, mock_session_factory):
        context = self.make_context(mock_session_factory)

        await apply_transit_message(context)

        assert context.session.parent_thread_id is None
        assert context.session.has_first_interaction is False
        context.emitter.init_thread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_session_with_interaction_leaves_record_alone(
        self, mock_session_factory
    ):
        # A flap of the emitting session's socket re-enters this function on
        # the old session, which must not swallow the record it just parked
        # for its successor.
        context = self.make_context(mock_session_factory, has_first_interaction=True)
        transit.store(self.SESSION_ID, "for the successor", None, parent="thread-a")

        await apply_transit_message(context)

        context.emitter.init_thread.assert_not_awaited()
        assert transit.pop(self.SESSION_ID, None).value == "for the successor"

    @pytest.mark.asyncio
    async def test_foreign_owner_record_is_not_applied(self, mock_session_factory):
        context = self.make_context(mock_session_factory)
        transit.store(self.SESSION_ID, "secret", "someone_else", parent="thread-a")

        await apply_transit_message(context)

        assert context.session.parent_thread_id is None
        assert context.session.has_first_interaction is False
        context.emitter.init_thread.assert_not_awaited()
        assert "transit_message" not in user_sessions.get(self.SESSION_ID, {})

    @pytest.mark.asyncio
    async def test_non_string_transit_names_thread_after_profile(
        self, mock_session_factory
    ):
        context = self.make_context(mock_session_factory, chat_profile="Search")
        payload = {"query": "knife"}
        transit.store(self.SESSION_ID, payload, None)

        await apply_transit_message(context)

        assert user_sessions[self.SESSION_ID]["transit_message"] == payload
        context.emitter.init_thread.assert_awaited_once_with("Search")


class TestAskSurvivesReconnect:
    """A pending ask must be re-emitted on reconnect and resolved by ask_reply."""

    @staticmethod
    def _pending_ask(timeout=60, **overrides):
        defaults = dict(
            step_dict={"id": "step-1", "parentId": "parent-1"},
            spec=AskSpec(timeout=timeout, type="action", step_id="step-1"),
            future=asyncio.get_event_loop().create_future(),
            deadline=time.monotonic() + timeout,
        )
        defaults.update(overrides)
        return PendingAsk(**defaults)

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
        mock_config.code.on_profile_start = None
        return mock_config

    @pytest.mark.asyncio
    async def test_connection_successful_reemits_live_pending_ask(
        self, mock_session_factory
    ):
        action_dict = {"id": "a1", "name": "continue", "forId": "step-1"}
        # spec says 60s but only ~5s remain: the re-emitted spec must carry
        # the remaining time, not the original timeout.
        pending = self._pending_ask(
            timeout=60,
            restore_actions=[action_dict],
            restore_element=None,
            deadline=time.monotonic() + 5,
        )
        session = mock_session_factory(pending_ask=pending)
        session.restored = True
        session.chat_started = True
        session.thread_id_to_resume = None
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config()),
        ):
            await connection_successful("sid-1")

        emitted = {
            call.args[0]: call.args[1] for call in context.emitter.emit.call_args_list
        }
        assert emitted["action"] == action_dict
        ask_payload, legacy_ack = session.emit_ask.await_args.args
        assert ask_payload["msg"] == pending.step_dict
        assert 1 <= ask_payload["spec"]["timeout"] <= 5
        assert callable(legacy_ack)
        cleared = [call.args[0] for call in context.emitter.clear.call_args_list]
        assert "clear_ask" not in cleared
        assert "clear_call_fn" in cleared

    @pytest.mark.asyncio
    async def test_connection_successful_reemits_element_for_element_ask(
        self, mock_session_factory
    ):
        element_dict = {"id": "el-1", "forId": "step-1"}
        element = Mock()
        element.to_dict = Mock(return_value=element_dict)
        pending = self._pending_ask(restore_element=element)
        session = mock_session_factory(pending_ask=pending)
        session.restored = True
        session.chat_started = True
        session.thread_id_to_resume = None
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config()),
        ):
            await connection_successful("sid-1")

        emitted = [call.args[0] for call in context.emitter.emit.call_args_list]
        assert "element" in emitted
        session.emit_ask.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connection_successful_clears_ask_when_no_pending(
        self, mock_session_factory
    ):
        session = mock_session_factory(pending_ask=None)
        session.restored = True
        session.chat_started = True
        session.thread_id_to_resume = None
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config()),
        ):
            await connection_successful("sid-1")

        cleared = [call.args[0] for call in context.emitter.clear.call_args_list]
        assert "clear_ask" in cleared
        emitted = [call.args[0] for call in context.emitter.emit.call_args_list]
        assert "ask" not in emitted

    @pytest.mark.asyncio
    async def test_connection_successful_clears_ask_when_pending_expired(
        self, mock_session_factory
    ):
        pending = self._pending_ask(deadline=time.monotonic() - 1)
        session = mock_session_factory(pending_ask=pending)
        session.restored = True
        session.chat_started = True
        session.thread_id_to_resume = None
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config()),
        ):
            await connection_successful("sid-1")

        cleared = [call.args[0] for call in context.emitter.clear.call_args_list]
        assert "clear_ask" in cleared
        emitted = [call.args[0] for call in context.emitter.emit.call_args_list]
        assert "ask" not in emitted

    @pytest.mark.asyncio
    async def test_ask_reply_resolves_pending_future(self, mock_session_factory):
        pending = self._pending_ask()
        session = mock_session_factory(pending_ask=pending)

        with patch.object(WebsocketSession, "get", return_value=session):
            await ask_reply("sid-1", {"stepId": "step-1", "value": {"name": "go"}})

        assert pending.future.done()
        assert pending.future.result() == {"name": "go"}

    @pytest.mark.asyncio
    async def test_ask_reply_ignores_stale_step_id(self, mock_session_factory):
        pending = self._pending_ask()
        session = mock_session_factory(pending_ask=pending)

        with patch.object(WebsocketSession, "get", return_value=session):
            await ask_reply("sid-1", {"stepId": "other-step", "value": "x"})

        assert not pending.future.done()

    @pytest.mark.asyncio
    async def test_ask_reply_ignores_duplicate(self, mock_session_factory):
        pending = self._pending_ask()
        pending.future.set_result("first")
        session = mock_session_factory(pending_ask=pending)

        with patch.object(WebsocketSession, "get", return_value=session):
            await ask_reply("sid-1", {"stepId": "step-1", "value": "second"})

        assert pending.future.result() == "first"

    @pytest.mark.asyncio
    async def test_ask_reply_without_session_does_not_crash(self):
        with patch.object(WebsocketSession, "get", return_value=None):
            await ask_reply("sid-unknown", {"stepId": "step-1", "value": "x"})

    @pytest.mark.asyncio
    async def test_ask_reply_without_pending_ask_does_not_crash(
        self, mock_session_factory
    ):
        session = mock_session_factory(pending_ask=None)

        with patch.object(WebsocketSession, "get", return_value=session):
            await ask_reply("sid-1", {"stepId": "step-1", "value": "x"})

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_ask_and_clears_ui(self, mock_session_factory):
        pending = self._pending_ask()
        session = mock_session_factory(pending_ask=pending)
        session.current_task = None
        context = self._context(session)
        mock_config = self._config()
        mock_config.code.on_stop = None

        with (
            patch.object(WebsocketSession, "get", return_value=session),
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", mock_config),
            patch("chainlit.socket.Message") as mock_message,
        ):
            mock_message.return_value.send = AsyncMock()
            await stop("sid-1")

        assert pending.future.cancelled()
        cleared = [call.args[0] for call in context.emitter.clear.call_args_list]
        assert "clear_ask" in cleared


class TestAskRestoreEdgeCases:
    """Edge cases of restore_pending_ask and ask_reply hardening."""

    _pending_ask = staticmethod(TestAskSurvivesReconnect._pending_ask)

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
        mock_config.code.on_profile_start = None
        return mock_config

    @pytest.mark.asyncio
    async def test_reconnect_of_loaded_page_reemits_actions_but_not_element(
        self, mock_session_factory
    ):
        """Plain transport reconnect (the client kept its UI state): the
        buttons are re-emitted anyway — "no page load" never proves they
        arrived (emits into a dying socket are silently dropped), and the
        client upserts actions by id so nothing duplicates. The element
        stays gated: re-emitting it would roll a live form back to a
        snapshot (a client-driven updateElement never mutates the
        server-side snapshot object) and wipe in-progress input."""
        element = Mock()
        element.to_dict = Mock(return_value={"id": "el-1"})
        pending = self._pending_ask(
            restore_actions=[{"id": "a1"}], restore_element=element
        )
        session = mock_session_factory(pending_ask=pending, fresh_page_load=False)
        session.restored = True
        session.chat_started = True
        session.thread_id_to_resume = None
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config()),
        ):
            await connection_successful("sid-1")

        emitted = {
            call.args[0]: call.args[1] for call in context.emitter.emit.call_args_list
        }
        assert emitted.get("action") == {"id": "a1"}
        assert "element" not in emitted
        element.to_dict.assert_not_called()
        session.emit_ask.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_actions_reemitted_before_ask_payload(self, mock_session_factory):
        """Order: the buttons must be in the client's atom before the ask
        payload makes askUser.spec.keys filter by them."""
        pending = self._pending_ask(restore_actions=[{"id": "a1"}, {"id": "a2"}])
        session = mock_session_factory(pending_ask=pending, fresh_page_load=False)
        session.restored = True
        session.chat_started = True
        session.thread_id_to_resume = None
        context = self._context(session)

        order = []
        context.emitter.emit = AsyncMock(
            side_effect=lambda event, data: order.append(event)
        )
        session.emit_ask = AsyncMock(side_effect=lambda *a, **k: order.append("ask"))

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config()),
        ):
            await connection_successful("sid-1")

        action_positions = [i for i, event in enumerate(order) if event == "action"]
        assert len(action_positions) == 2
        assert "ask" in order
        assert order.index("ask") > max(action_positions)

    @pytest.mark.asyncio
    async def test_answered_pending_ask_is_not_reemitted(self, mock_session_factory):
        """A resolved-but-not-yet-cleared ask must not resurrect its form."""
        pending = self._pending_ask()
        pending.future.set_result({"name": "done"})
        session = mock_session_factory(pending_ask=pending)
        session.restored = True
        session.chat_started = True
        session.thread_id_to_resume = None
        context = self._context(session)

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config()),
        ):
            await connection_successful("sid-1")

        emitted = [call.args[0] for call in context.emitter.emit.call_args_list]
        assert "ask" not in emitted
        cleared = [call.args[0] for call in context.emitter.clear.call_args_list]
        assert "clear_ask" in cleared

    @pytest.mark.asyncio
    async def test_pending_ask_survives_thread_resume_branch(
        self, mock_session_factory
    ):
        """resume_thread replaces the client's state wholesale: the ask must
        be re-emitted AFTER it, not clobbered by it."""
        pending = self._pending_ask()
        session = mock_session_factory(pending_ask=pending)
        # Force the plain-emit fallback so the ordering below can be read
        # off a single mock's call log.
        del session.emit_ask
        session.restored = True
        session.chat_started = True
        session.thread_id_to_resume = "thread-1"
        session.user = None
        context = self._context(session)

        mock_config = Mock()
        mock_config.code.on_chat_start = None
        mock_config.code.on_chat_resume = AsyncMock()
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None
        thread = {"id": "thread-1", "steps": []}

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", mock_config),
            patch("chainlit.socket.resume_thread", AsyncMock(return_value=thread)),
        ):
            await connection_successful("sid-1")

        emitted = [call.args[0] for call in context.emitter.emit.call_args_list]
        assert "ask" in emitted
        context.emitter.resume_thread.assert_awaited_once()
        # The ask must be re-emitted AFTER resume_thread replaced the client
        # state, or the restored form would be wiped by the resume payload.
        call_names = [name for name, _args, _kwargs in context.emitter.mock_calls]
        ask_emit_index = next(
            i
            for i, (name, args, _kwargs) in enumerate(context.emitter.mock_calls)
            if name == "emit" and args and args[0] == "ask"
        )
        assert call_names.index("resume_thread") < ask_emit_index

    @pytest.mark.asyncio
    async def test_ask_reply_with_none_payload_is_ignored(self, mock_session_factory):
        pending = self._pending_ask()
        session = mock_session_factory(pending_ask=pending)

        with patch.object(WebsocketSession, "get", return_value=session):
            await ask_reply("sid-1", None)

        assert not pending.future.done()

    @pytest.mark.asyncio
    async def test_ask_reply_on_cancelled_future_is_ignored(self, mock_session_factory):
        pending = self._pending_ask()
        pending.future.cancel()
        session = mock_session_factory(pending_ask=pending)

        with patch.object(WebsocketSession, "get", return_value=session):
            await ask_reply("sid-1", {"stepId": "step-1", "value": "late"})

        assert pending.future.cancelled()


class TestPageLoadGate:
    """A reloaded page reconnects to its old session only to rescue live
    work (a pending ask or a running task); otherwise F5 means a fresh
    chat under the same id."""

    def _auth(self, page_load):
        return {
            "sessionId": "session_123",
            "clientType": "webapp",
            "userEnv": None,
            "chatProfile": None,
            "threadId": None,
            "pageLoad": page_load,
        }

    def _stale_session(self, pending_ask=None, current_task=None):
        stale = Mock(spec=WebsocketSession)
        stale.id = "session_123"
        stale.socket_id = "old_sid"
        stale.user = None
        stale.pending_ask = pending_ask
        stale.current_task = current_task
        stale.delete = AsyncMock()
        return stale

    @pytest.mark.asyncio
    async def test_page_load_with_idle_session_drops_it(self):
        stale = self._stale_session()
        user_sessions["session_123"] = {"stale": True}
        order = []

        async def record_delete():
            order.append("delete")

        stale.delete = AsyncMock(side_effect=record_delete)
        try:
            with (
                patch("chainlit.socket.require_login", return_value=False),
                patch("chainlit.socket.WebsocketSession") as mock_ws,
                patch("chainlit.socket.restore_existing_session") as mock_restore,
            ):
                mock_ws.get_by_id.return_value = stale
                mock_ws.return_value.resolve_config = AsyncMock()
                mock_ws.side_effect = lambda **kwargs: (
                    order.append("create"),
                    mock_ws.return_value,
                )[-1]
                await connect("sid-1", {}, self._auth(page_load=True))

            stale.delete.assert_awaited_once()
            assert "session_123" not in user_sessions
            mock_restore.assert_not_called()
            # The old session must be FULLY deleted before the successor is
            # created under the same id — a deferred cleanup would wipe the
            # new session's registry entry, context and files.
            assert order == ["delete", "create"]
        finally:
            user_sessions.pop("session_123", None)

    @pytest.mark.asyncio
    async def test_page_load_with_live_ask_keeps_session(self):
        pending = Mock()
        pending.is_live = True
        stale = self._stale_session(pending_ask=pending)

        with (
            patch("chainlit.socket.require_login", return_value=False),
            patch.object(WebsocketSession, "get_by_id", return_value=stale),
            patch(
                "chainlit.socket.restore_existing_session", return_value=True
            ) as mock_restore,
        ):
            await connect("sid-1", {}, self._auth(page_load=True))

        stale.delete.assert_not_awaited()
        mock_restore.assert_called_once()

    @pytest.mark.asyncio
    async def test_page_load_with_running_task_keeps_session(self):
        """F5 in the middle of a paid pipeline must not kill it."""
        task = Mock()
        task.done.return_value = False
        stale = self._stale_session(current_task=task)

        with (
            patch("chainlit.socket.require_login", return_value=False),
            patch.object(WebsocketSession, "get_by_id", return_value=stale),
            patch(
                "chainlit.socket.restore_existing_session", return_value=True
            ) as mock_restore,
        ):
            await connect("sid-1", {}, self._auth(page_load=True))

        stale.delete.assert_not_awaited()
        mock_restore.assert_called_once()

    @pytest.mark.asyncio
    async def test_transport_reconnect_keeps_session_without_ask(self):
        stale = self._stale_session()

        with (
            patch("chainlit.socket.require_login", return_value=False),
            patch.object(WebsocketSession, "get_by_id", return_value=stale),
            patch(
                "chainlit.socket.restore_existing_session", return_value=True
            ) as mock_restore,
        ):
            await connect("sid-1", {}, self._auth(page_load=False))

        stale.delete.assert_not_awaited()
        mock_restore.assert_called_once()

    @pytest.mark.asyncio
    async def test_expired_pending_ask_and_finished_task_count_as_idle(self):
        pending = Mock()
        pending.is_live = False
        task = Mock()
        task.done.return_value = True
        stale = self._stale_session(pending_ask=pending, current_task=task)

        with (
            patch("chainlit.socket.require_login", return_value=False),
            patch("chainlit.socket.WebsocketSession") as mock_ws,
            patch("chainlit.socket.restore_existing_session") as mock_restore,
        ):
            mock_ws.get_by_id.return_value = stale
            mock_ws.return_value.resolve_config = AsyncMock()
            await connect("sid-1", {}, self._auth(page_load=True))
            await asyncio.sleep(0)

        stale.delete.assert_awaited_once()
        mock_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_foreign_user_cannot_drop_someone_elses_session(self):
        """The owner check must run BEFORE any destruction."""
        stale = self._stale_session()
        stale.user = Mock(identifier="victim")
        attacker = Mock(identifier="attacker")

        with (
            patch("chainlit.socket.require_login", return_value=True),
            patch(
                "chainlit.socket._authenticate_connection",
                AsyncMock(return_value=(attacker, "token")),
            ),
            patch.object(WebsocketSession, "get_by_id", return_value=stale),
        ):
            with pytest.raises(
                ConnectionRefusedError, match="session authorization failed"
            ):
                await connect("sid-1", {}, self._auth(page_load=True))

        stale.delete.assert_not_awaited()


class TestTranscriptReplay:
    """A fresh page load restores the transcript, not just the ask form."""

    def _context(self, session):
        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()
        return mock_context

    @pytest.mark.asyncio
    async def test_transcript_replayed_before_ask_on_fresh_load(
        self, mock_session_factory
    ):
        pending = TestAskSurvivesReconnect._pending_ask()
        session = mock_session_factory(pending_ask=pending, restored=True)
        # Force the plain-emit fallback so the ordering below can be read
        # off a single mock's call log.
        del session.emit_ask
        context = self._context(session)
        message = Mock()
        message.to_dict = Mock(return_value={"id": "m1", "output": "paid result"})
        message.elements = []
        message._active_wait_payload = None

        with patch("chainlit.socket.chat_context") as mock_chat_context:
            mock_chat_context.get.return_value = [message]
            await restore_pending_ask(context, client_has_ui_state=False)

        context.emitter.send_step.assert_awaited_once_with(
            {"id": "m1", "output": "paid result"}
        )
        # The transcript must land BEFORE the ask so the form appears under
        # the replayed results, not above them.
        call_names = [name for name, _args, _kwargs in context.emitter.mock_calls]
        ask_emit_index = next(
            i
            for i, (name, args, _kwargs) in enumerate(context.emitter.mock_calls)
            if name == "emit" and args and args[0] == "ask"
        )
        assert call_names.index("send_step") < ask_emit_index

    @pytest.mark.asyncio
    async def test_transcript_replayed_on_transport_reconnect_of_restored_session(
        self, mock_session_factory
    ):
        """Messages emitted into a dead socket are dropped by the server:
        even a plain transport reconnect (client kept its UI state) must
        converge — the client upserts new_message by id, so the re-emission
        is idempotent."""
        pending = TestAskSurvivesReconnect._pending_ask()
        session = mock_session_factory(pending_ask=pending, restored=True)
        context = self._context(session)
        message = Mock()
        message.to_dict = Mock(return_value={"id": "m1", "output": "late finale"})
        message.elements = []
        message._active_wait_payload = None

        with patch("chainlit.socket.chat_context") as mock_chat_context:
            mock_chat_context.get.return_value = [message]
            await restore_pending_ask(context, client_has_ui_state=True)

        context.emitter.send_step.assert_awaited_once_with(
            {"id": "m1", "output": "late finale"}
        )

    @pytest.mark.asyncio
    async def test_replay_emits_stored_element_dicts(self, mock_session_factory):
        """A Message rebuilt from a thread payload carries no element
        objects — the replay reads the attachments from the session's
        element-dict map recorded at repopulation time."""
        session = mock_session_factory(
            restored=True,
            transcript_element_dicts={"m1": [{"id": "el-1", "forId": "m1"}]},
        )
        context = self._context(session)
        message = Mock()
        message.id = "m1"
        message.to_dict = Mock(return_value={"id": "m1", "output": "with attachment"})
        message.elements = []
        message._active_wait_payload = None

        with patch("chainlit.socket.chat_context") as mock_chat_context:
            mock_chat_context.get.return_value = [message]
            await restore_pending_ask(context, client_has_ui_state=False)

        context.emitter.send_element.assert_awaited_once_with(
            {"id": "el-1", "forId": "m1"}
        )

    @pytest.mark.asyncio
    async def test_replay_dedups_live_and_stored_elements(self, mock_session_factory):
        """A live element object wins over the stored dict of the same id;
        stored-only attachments are still emitted."""
        session = mock_session_factory(
            restored=True,
            transcript_element_dicts={
                "m1": [
                    {"id": "el-1", "forId": "m1", "name": "stale"},
                    {"id": "el-2", "forId": "m1"},
                ]
            },
        )
        context = self._context(session)
        live_element = Mock()
        live_element.to_dict = Mock(
            return_value={"id": "el-1", "forId": "m1", "name": "live"}
        )
        message = Mock()
        message.id = "m1"
        message.to_dict = Mock(return_value={"id": "m1", "output": "x"})
        message.elements = [live_element]
        message._active_wait_payload = None

        with patch("chainlit.socket.chat_context") as mock_chat_context:
            mock_chat_context.get.return_value = [message]
            await restore_pending_ask(context, client_has_ui_state=False)

        emitted = [
            call.args[0] for call in context.emitter.send_element.await_args_list
        ]
        assert emitted == [
            {"id": "el-1", "forId": "m1", "name": "live"},
            {"id": "el-2", "forId": "m1"},
        ]

    @pytest.mark.asyncio
    async def test_replay_carries_active_wait_payload(self, mock_session_factory):
        """The client force-overwrites the transient `wait` field on every
        new_message — a replayed message still in wait mode must carry the
        payload of its original emit or the shimmer dies on reconnect."""
        session = mock_session_factory(restored=True)
        context = self._context(session)
        wait_payload = {"texts": ["thinking"], "intervalMs": 5000, "loop": False}
        message = Mock()
        message.id = "m1"
        message.to_dict = Mock(return_value={"id": "m1", "output": ""})
        message.elements = []
        message._active_wait_payload = wait_payload

        with patch("chainlit.socket.chat_context") as mock_chat_context:
            mock_chat_context.get.return_value = [message]
            await restore_pending_ask(context, client_has_ui_state=False)

        context.emitter.send_step.assert_awaited_once_with(
            {"id": "m1", "output": "", "wait": wait_payload}
        )

    @pytest.mark.asyncio
    async def test_transcript_not_replayed_for_unrestored_session(
        self, mock_session_factory
    ):
        """A fresh session never lost anything — nothing to resync."""
        pending = TestAskSurvivesReconnect._pending_ask()
        session = mock_session_factory(pending_ask=pending, restored=False)
        context = self._context(session)

        with patch("chainlit.socket.chat_context") as mock_chat_context:
            mock_chat_context.get.return_value = [Mock()]
            await restore_pending_ask(context, client_has_ui_state=False)

        context.emitter.send_step.assert_not_awaited()


class TestTranscriptResyncFallback:
    """When the in-memory transcript is empty, the reconnect resync reads
    the thread back from the data layer (after the persist barrier)."""

    THREAD = {
        "id": "test_thread_id",
        "steps": [
            {"id": "s1", "type": "user_message", "output": "hi"},
            {"id": "s2", "type": "assistant_message", "output": "done"},
            {"id": "r1", "type": "run", "output": ""},
        ],
        "elements": [
            {"id": "el-1", "forId": "s2"},
            {"id": "el-run", "forId": "r1"},
        ],
    }

    def _context(self, session):
        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()
        return mock_context

    def _session(self, mock_session_factory):
        return mock_session_factory(
            restored=True, has_first_interaction=True, thread_id="test_thread_id"
        )

    @pytest.mark.asyncio
    async def test_fallback_reads_data_layer_when_transcript_empty(
        self, mock_session_factory
    ):
        session = self._session(mock_session_factory)
        context = self._context(session)
        order = []

        data_layer = AsyncMock()

        async def get_thread(thread_id):
            order.append("get_thread")
            return self.THREAD

        data_layer.get_thread.side_effect = get_thread

        async def record_wait(thread_id, *args, **kwargs):
            order.append("wait_for_persist")

        with (
            patch("chainlit.socket.chat_context") as mock_chat_context,
            patch("chainlit.socket.get_data_layer", return_value=data_layer),
            patch(
                "chainlit.socket.wait_for_persist", AsyncMock(side_effect=record_wait)
            ),
            patch("chainlit.socket.Message") as mock_message,
        ):
            mock_chat_context.get.return_value = []
            mock_message.from_dict.side_effect = lambda d: Mock(id=d["id"])
            await restore_pending_ask(context, client_has_ui_state=True)

        # The barrier runs BEFORE the read, so fresh steps still sitting in
        # background persist tasks make it into the payload.
        assert order == ["wait_for_persist", "get_thread"]
        data_layer.get_thread.assert_awaited_once_with(thread_id="test_thread_id")

        # Only message-type steps are replayed, with their elements.
        replayed = [
            call.args[0]["id"] for call in context.emitter.send_step.await_args_list
        ]
        assert replayed == ["s1", "s2"]
        context.emitter.send_element.assert_awaited_once_with(
            {"id": "el-1", "forId": "s2"}
        )

        # The in-memory transcript is repopulated so the NEXT reconnect
        # replays from memory.
        added = [call.args[0].id for call in mock_chat_context.add.call_args_list]
        assert added == ["s1", "s2"]

    @pytest.mark.asyncio
    async def test_fallback_skipped_without_data_layer(self, mock_session_factory):
        session = self._session(mock_session_factory)
        context = self._context(session)

        with (
            patch("chainlit.socket.chat_context") as mock_chat_context,
            patch("chainlit.socket.get_data_layer", return_value=None),
            patch("chainlit.socket.wait_for_persist", AsyncMock()) as mock_wait,
        ):
            mock_chat_context.get.return_value = []
            await restore_pending_ask(context, client_has_ui_state=True)

        context.emitter.send_step.assert_not_awaited()
        mock_wait.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fallback_skipped_when_thread_missing(self, mock_session_factory):
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()
        data_layer.get_thread.return_value = None

        with (
            patch("chainlit.socket.chat_context") as mock_chat_context,
            patch("chainlit.socket.get_data_layer", return_value=data_layer),
            patch("chainlit.socket.wait_for_persist", AsyncMock()),
        ):
            mock_chat_context.get.return_value = []
            await restore_pending_ask(context, client_has_ui_state=True)

        context.emitter.send_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fallback_skipped_before_first_interaction(
        self, mock_session_factory
    ):
        session = mock_session_factory(restored=True, has_first_interaction=False)
        context = self._context(session)
        data_layer = AsyncMock()

        with (
            patch("chainlit.socket.chat_context") as mock_chat_context,
            patch("chainlit.socket.get_data_layer", return_value=data_layer),
        ):
            mock_chat_context.get.return_value = []
            await restore_pending_ask(context, client_has_ui_state=True)

        data_layer.get_thread.assert_not_awaited()
        context.emitter.send_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fallback_does_not_delete_flagged_steps(self, mock_session_factory):
        """A live restored session's resume='delete' steps are legitimately
        alive — the resync path re-emits them and never deletes."""
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()
        data_layer.get_thread.return_value = {
            "id": "test_thread_id",
            "steps": [
                {
                    "id": "flagged-1",
                    "type": "assistant_message",
                    "output": "offer",
                    "metadata": {"resume_policy": "delete"},
                }
            ],
            "elements": [],
        }

        with (
            patch("chainlit.socket.chat_context") as mock_chat_context,
            patch("chainlit.socket.get_data_layer", return_value=data_layer),
            patch("chainlit.socket.wait_for_persist", AsyncMock()),
            patch("chainlit.socket.Message") as mock_message,
        ):
            mock_chat_context.get.return_value = []
            mock_message.from_dict.side_effect = lambda d: Mock(id=d["id"])
            await restore_pending_ask(context, client_has_ui_state=True)

        context.emitter.send_step.assert_awaited_once()
        assert context.emitter.send_step.await_args.args[0]["id"] == "flagged-1"
        data_layer.delete_step.assert_not_awaited()
        data_layer.delete_element.assert_not_awaited()
        context.emitter.delete_step.assert_not_awaited()


class _FakeChatContext:
    """Real list semantics for tests running connection_successful whole."""

    def __init__(self, messages=None):
        self.messages = list(messages or [])

    def get(self):
        return list(self.messages)

    def add(self, message):
        self.messages.append(message)


def _fake_transcript_message(step_id):
    return Mock(
        id=step_id,
        elements=[],
        _active_wait_payload=None,
        to_dict=Mock(return_value={"id": step_id}),
    )


class TestResumeSnapshotSkipsReplay:
    """A fresh resume_thread snapshot already rebuilt the client's feed —
    re-emitting the transcript on top of it is redundant wire volume."""

    def _context(self, session):
        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()
        return mock_context

    @pytest.mark.asyncio
    async def test_no_transcript_replay_after_resume_snapshot(
        self, mock_session_factory
    ):
        session = mock_session_factory(
            thread_id="thread_123", restored=True, resume_processed=True
        )
        session.has_first_interaction = True
        session.chat_started = True
        session.current_task = None
        session.thread_id_to_resume = "thread_123"
        context = self._context(session)
        thread = {"id": "thread_123", "steps": [], "elements": []}
        fake_chat_context = _FakeChatContext([_fake_transcript_message("m1")])

        mock_config = Mock()
        mock_config.code.on_chat_start = None
        mock_config.code.on_chat_resume = AsyncMock()
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", mock_config),
            patch("chainlit.socket.resume_thread", AsyncMock(return_value=thread)),
            patch("chainlit.socket.get_data_layer", return_value=AsyncMock()),
            patch("chainlit.socket.chat_context", fake_chat_context),
            patch("chainlit.socket.Message"),
        ):
            await connection_successful("sid-1")

        context.emitter.resume_thread.assert_awaited_once()
        context.emitter.send_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_plain_reconnect_without_resume_branch_still_replays(
        self, mock_session_factory
    ):
        """A restored session without a thread to resume (no snapshot in
        this cycle) still gets the transcript resync."""
        session = mock_session_factory(restored=True)
        session.has_first_interaction = True
        session.chat_started = True
        session.current_task = None
        session.thread_id_to_resume = None
        context = self._context(session)
        fake_chat_context = _FakeChatContext([_fake_transcript_message("m1")])

        mock_config = Mock()
        mock_config.code.on_chat_start = None
        mock_config.code.on_chat_resume = None
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", mock_config),
            patch("chainlit.socket.chat_context", fake_chat_context),
        ):
            await connection_successful("sid-1")

        context.emitter.send_step.assert_awaited_once_with({"id": "m1"})


class TestResumeDeleteFlag:
    """Steps flagged resume="delete" are stripped and deleted on the resume
    of a dead session, while a live pending ask protects its step."""

    THREAD_ID = "thread_123"

    def _thread(self, steps=None, elements=None):
        return {
            "id": self.THREAD_ID,
            "userIdentifier": "test_user_identifier",
            "metadata": {},
            "steps": steps if steps is not None else [],
            "elements": elements if elements is not None else [],
        }

    def _flagged_step(self, step_id, metadata=None):
        return {
            "id": step_id,
            "type": "assistant_message",
            "output": "flagged",
            "metadata": (
                metadata if metadata is not None else {"resume_policy": "delete"}
            ),
        }

    def _plain_step(self, step_id):
        return {
            "id": step_id,
            "type": "assistant_message",
            "output": "plain",
            "metadata": {},
        }

    def _session(self, mock_session_factory):
        session = mock_session_factory(
            has_first_interaction=False, thread_id=self.THREAD_ID
        )
        session.restored = False
        session.chat_started = False
        session.current_task = None
        session.thread_id_to_resume = self.THREAD_ID
        return session

    def _context(self, session):
        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()
        return mock_context

    def _config(self, on_chat_resume):
        mock_config = Mock()
        mock_config.code.on_chat_start = None
        mock_config.code.on_chat_resume = on_chat_resume
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None
        return mock_config

    async def _run_resume(self, thread, session, context, data_layer):
        on_chat_resume = AsyncMock()
        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config(on_chat_resume)),
            patch("chainlit.socket.resume_thread", AsyncMock(return_value=thread)),
            patch("chainlit.socket.get_data_layer", return_value=data_layer),
            patch("chainlit.socket.chat_context"),
            patch("chainlit.socket.Message"),
        ):
            await connection_successful("sid-1")
        return on_chat_resume

    @pytest.mark.asyncio
    async def test_flagged_step_deleted_on_resume(self, mock_session_factory):
        """A flagged step is stripped from the payload, deleted from the
        data layer with its elements, and delete_message is emitted."""
        doomed = self._flagged_step("doomed-1")
        kept = self._plain_step("keep-1")
        thread = self._thread(
            steps=[kept, doomed],
            elements=[
                {"id": "el-keep", "forId": "keep-1"},
                {"id": "el-doomed", "forId": "doomed-1"},
            ],
        )
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        on_chat_resume = await self._run_resume(thread, session, context, data_layer)

        # The app and the resume_thread emit both see the cleaned payload.
        resumed = on_chat_resume.await_args.args[0]
        assert [s["id"] for s in resumed["steps"]] == ["keep-1"]
        assert [e["id"] for e in resumed["elements"]] == ["el-keep"]
        emitted = context.emitter.resume_thread.await_args.args[0]
        assert [s["id"] for s in emitted["steps"]] == ["keep-1"]
        assert [e["id"] for e in emitted["elements"]] == ["el-keep"]

        # Data layer cleanup: element first, then the step; only the doomed one.
        data_layer.delete_element.assert_awaited_once_with("el-doomed", self.THREAD_ID)
        data_layer.delete_step.assert_awaited_once_with("doomed-1")

        # delete_message is emitted for the doomed step, after resume_thread.
        context.emitter.delete_step.assert_awaited_once()
        assert context.emitter.delete_step.await_args.args[0]["id"] == "doomed-1"
        call_names = [name for name, _, _ in context.emitter.mock_calls]
        assert call_names.index("resume_thread") < call_names.index("delete_step")

    @pytest.mark.asyncio
    async def test_unflagged_thread_untouched(self, mock_session_factory):
        """A thread with no flagged steps triggers no deletion at all."""
        thread = self._thread(
            steps=[self._plain_step("keep-1"), self._plain_step("keep-2")],
            elements=[{"id": "el-1", "forId": "keep-1"}],
        )
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        on_chat_resume = await self._run_resume(thread, session, context, data_layer)

        resumed = on_chat_resume.await_args.args[0]
        assert [s["id"] for s in resumed["steps"]] == ["keep-1", "keep-2"]
        assert [e["id"] for e in resumed["elements"]] == ["el-1"]
        data_layer.delete_step.assert_not_awaited()
        data_layer.delete_element.assert_not_awaited()
        context.emitter.delete_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_live_pending_ask_protects_flagged_step(self, mock_session_factory):
        """A flagged step held by a live pending ask of a session on this
        thread is neither stripped nor deleted."""
        import chainlit.session as session_module

        doomed = self._flagged_step("doomed-1")
        protected = self._flagged_step("ask-step")
        thread = self._thread(
            steps=[protected, doomed],
            elements=[{"id": "el-ask", "forId": "ask-step"}],
        )
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        holder = Mock(spec=WebsocketSession)
        holder.thread_id = self.THREAD_ID
        holder.pending_ask = PendingAsk(
            step_dict={"id": "ask-step"},
            spec=AskSpec(timeout=60, type="element", step_id="ask-step"),
            future=asyncio.get_event_loop().create_future(),
            deadline=time.monotonic() + 60,
        )

        with patch.dict(
            session_module.ws_sessions_id, {"holder-session": holder}, clear=True
        ):
            on_chat_resume = await self._run_resume(
                thread, session, context, data_layer
            )

        resumed = on_chat_resume.await_args.args[0]
        assert [s["id"] for s in resumed["steps"]] == ["ask-step"]
        assert [e["id"] for e in resumed["elements"]] == ["el-ask"]
        data_layer.delete_step.assert_awaited_once_with("doomed-1")
        assert context.emitter.delete_step.await_args.args[0]["id"] == "doomed-1"

    @pytest.mark.asyncio
    async def test_string_metadata_recognized(self, mock_session_factory):
        """SQLite/SQLAlchemy returns metadata as a JSON string."""
        doomed = self._flagged_step(
            "doomed-1", metadata=json.dumps({"resume_policy": "delete"})
        )
        thread = self._thread(steps=[doomed, self._plain_step("keep-1")])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        on_chat_resume = await self._run_resume(thread, session, context, data_layer)

        resumed = on_chat_resume.await_args.args[0]
        assert [s["id"] for s in resumed["steps"]] == ["keep-1"]
        data_layer.delete_step.assert_awaited_once_with("doomed-1")

    @pytest.mark.asyncio
    async def test_delete_step_failure_warns_without_crash(self, mock_session_factory):
        """An already-deleted step (double resume, tab race) must not crash
        the resume — a warning is logged and the flow completes."""
        doomed = self._flagged_step("doomed-1")
        thread = self._thread(steps=[doomed])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()
        data_layer.delete_step.side_effect = RuntimeError("already gone")

        with patch("chainlit.socket.logger") as mock_logger:
            await self._run_resume(thread, session, context, data_layer)

        assert mock_logger.warning.called
        # The client-side delete is still emitted despite the failure.
        context.emitter.delete_step.assert_awaited_once()
        context.emitter.resume_thread.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_second_entry_skips_deletion(self, mock_session_factory):
        """Only the FIRST entry into the resume branch deletes. A re-entry
        of the same session (F5, transport reconnect — thread_id_to_resume
        is never cleared) must not strip or delete flagged steps: a running
        task's live resume="delete" message would be killed and later
        resurrected as an orphan row by its own update()."""
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        # First entry: the flagged step is stripped and deleted.
        first_thread = self._thread(
            steps=[self._plain_step("keep-1"), self._flagged_step("doomed-1")]
        )
        await self._run_resume(first_thread, session, context, data_layer)
        data_layer.delete_step.assert_awaited_once_with("doomed-1")
        assert session.resume_processed is True

        # Second entry (now a restored, live session): a flagged live step
        # stays in the payload and nothing is deleted.
        data_layer.reset_mock()
        context.emitter.reset_mock()
        session.restored = True
        session.has_first_interaction = True
        doomed = self._flagged_step("live-1")
        thread = self._thread(
            steps=[self._plain_step("keep-1"), doomed],
            elements=[{"id": "el-live", "forId": "live-1"}],
        )

        on_chat_resume = await self._run_resume(thread, session, context, data_layer)

        resumed = on_chat_resume.await_args.args[0]
        assert [s["id"] for s in resumed["steps"]] == ["keep-1", "live-1"]
        assert [e["id"] for e in resumed["elements"]] == ["el-live"]
        emitted = context.emitter.resume_thread.await_args.args[0]
        assert [s["id"] for s in emitted["steps"]] == ["keep-1", "live-1"]
        data_layer.delete_step.assert_not_awaited()
        data_layer.delete_element.assert_not_awaited()
        context.emitter.delete_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_first_entry_on_restored_transport_still_deletes(
        self, mock_session_factory
    ):
        """A first entry that happens on an already-restored transport (the
        connection blipped between connect and connection_successful) is
        still a genuine resume of a dead session — it must filter and
        delete."""
        doomed = self._flagged_step("doomed-1")
        thread = self._thread(steps=[self._plain_step("keep-1"), doomed])
        session = self._session(mock_session_factory)
        session.restored = True
        session.has_first_interaction = True
        session.resume_processed = False
        context = self._context(session)
        data_layer = AsyncMock()

        on_chat_resume = await self._run_resume(thread, session, context, data_layer)

        resumed = on_chat_resume.await_args.args[0]
        assert [s["id"] for s in resumed["steps"]] == ["keep-1"]
        data_layer.delete_step.assert_awaited_once_with("doomed-1")
        assert session.resume_processed is True

    @pytest.mark.asyncio
    async def test_resume_thread_emitted_before_on_chat_resume(
        self, mock_session_factory
    ):
        """The snapshot must reach the client BEFORE the handler runs: the
        handler can block for minutes and send messages of its own — a
        stale snapshot emitted afterwards would wipe them. The doomed-step
        cleanup stays between the emit and the handler."""
        doomed = self._flagged_step("doomed-1")
        thread = self._thread(steps=[self._plain_step("keep-1"), doomed])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()
        order = []

        context.emitter.resume_thread = AsyncMock(
            side_effect=lambda _thread: order.append("resume_thread")
        )
        context.emitter.delete_step = AsyncMock(
            side_effect=lambda _step: order.append("delete_step")
        )
        on_chat_resume = AsyncMock(
            side_effect=lambda _thread: order.append("on_chat_resume")
        )

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config(on_chat_resume)),
            patch("chainlit.socket.resume_thread", AsyncMock(return_value=thread)),
            patch("chainlit.socket.get_data_layer", return_value=data_layer),
            patch("chainlit.socket.chat_context"),
            patch("chainlit.socket.Message"),
        ):
            await connection_successful("sid-1")

        assert order == ["resume_thread", "delete_step", "on_chat_resume"]
        # Nothing re-emits the snapshot after the handler: whatever the
        # handler sent stays on screen.
        context.emitter.resume_thread.assert_awaited_once()

    def test_live_task_on_thread_protects_all_flagged_steps(self):
        """A thread with a running task on ANY of its sessions is alive:
        nothing is doomed — a second tab resuming the thread must not
        delete the first tab's live flagged messages (the task's later
        update() would resurrect the row and the feeds would diverge)."""
        import chainlit.session as session_module
        from chainlit.resume_policy import split_resume_delete

        doomed = self._flagged_step("live-1")
        thread = self._thread(steps=[self._plain_step("keep-1"), doomed])

        holder = Mock(spec=WebsocketSession)
        holder.thread_id = self.THREAD_ID
        holder.pending_ask = None
        live_task = Mock()
        live_task.done.return_value = False
        holder.current_task = live_task

        with patch.dict(
            session_module.ws_sessions_id, {"holder-session": holder}, clear=True
        ):
            new_thread, doomed_steps, doomed_elements = split_resume_delete(thread)

        assert new_thread is thread
        assert doomed_steps == []
        assert doomed_elements == []

    def test_done_task_does_not_protect_flagged_steps(self):
        """A finished task means the thread is idle — flagged steps are
        doomed as before."""
        import chainlit.session as session_module
        from chainlit.resume_policy import split_resume_delete

        doomed = self._flagged_step("live-1")
        thread = self._thread(steps=[self._plain_step("keep-1"), doomed])

        holder = Mock(spec=WebsocketSession)
        holder.thread_id = self.THREAD_ID
        holder.pending_ask = None
        finished_task = Mock()
        finished_task.done.return_value = True
        holder.current_task = finished_task

        with patch.dict(
            session_module.ws_sessions_id, {"holder-session": holder}, clear=True
        ):
            new_thread, doomed_steps, _doomed_elements = split_resume_delete(thread)

        assert [s["id"] for s in new_thread["steps"]] == ["keep-1"]
        assert [s["id"] for s in doomed_steps] == ["live-1"]

    @pytest.mark.asyncio
    async def test_failed_deletion_is_retried_on_next_entry(self, mock_session_factory):
        """First entry: element deletion fails → the step is kept in the DB
        and stored for retry. Second entry: the kept step is hidden from
        the payload (plain id filter — no split re-run) and the deletion is
        retried; fresh live flagged steps are untouched."""
        doomed = self._flagged_step("doomed-1")
        thread1 = self._thread(
            steps=[self._plain_step("keep-1"), doomed],
            elements=[{"id": "el-doomed", "forId": "doomed-1"}],
        )
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()
        data_layer.delete_element.side_effect = RuntimeError("storage down")

        await self._run_resume(thread1, session, context, data_layer)

        retry_steps, retry_elements = session.resume_delete_retry
        assert [s["id"] for s in retry_steps] == ["doomed-1"]
        assert [e["id"] for e in retry_elements] == ["el-doomed"]
        assert session.resume_processed is True
        data_layer.delete_step.assert_not_awaited()

        # Second entry: deletion now succeeds.
        data_layer = AsyncMock()
        context.emitter.reset_mock()
        session.restored = True
        session.has_first_interaction = True
        fresh_flagged = self._flagged_step("fresh-live")
        thread2 = self._thread(
            steps=[self._plain_step("keep-1"), doomed, fresh_flagged],
            elements=[{"id": "el-doomed", "forId": "doomed-1"}],
        )

        await self._run_resume(thread2, session, context, data_layer)

        emitted = context.emitter.resume_thread.await_args.args[0]
        assert [s["id"] for s in emitted["steps"]] == ["keep-1", "fresh-live"]
        assert emitted["elements"] == []
        data_layer.delete_element.assert_awaited_once_with("el-doomed", self.THREAD_ID)
        data_layer.delete_step.assert_awaited_once_with("doomed-1")
        assert session.resume_delete_retry == ([], [])

    @pytest.mark.asyncio
    async def test_chat_context_repopulation_dedups_across_entries(
        self, mock_session_factory
    ):
        """Two entries into the resume branch must not duplicate the
        in-memory transcript (Message objects compare by identity)."""
        thread = self._thread(steps=[self._plain_step("s1"), self._plain_step("s2")])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        class FakeChatContext:
            def __init__(self):
                self.messages = []

            def get(self):
                return list(self.messages)

            def add(self, message):
                self.messages.append(message)

        fake_chat_context = FakeChatContext()
        on_chat_resume = AsyncMock()

        def fake_from_dict(step):
            return Mock(
                id=step["id"],
                elements=[],
                to_dict=Mock(return_value=step),
            )

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config(on_chat_resume)),
            patch("chainlit.socket.resume_thread", AsyncMock(return_value=thread)),
            patch("chainlit.socket.get_data_layer", return_value=data_layer),
            patch("chainlit.socket.chat_context", fake_chat_context),
            patch("chainlit.socket.Message") as mock_message,
        ):
            mock_message.from_dict.side_effect = fake_from_dict
            await connection_successful("sid-1")
            session.restored = True
            await connection_successful("sid-1")

        assert [m.id for m in fake_chat_context.messages] == ["s1", "s2"]

    @pytest.mark.asyncio
    async def test_data_layer_thread_dict_not_mutated(self, mock_session_factory):
        """The dict returned by the data layer may be a live reference to
        its internal state — filtering must work on a copy."""
        import copy

        thread = self._thread(
            steps=[self._plain_step("keep-1"), self._flagged_step("doomed-1")],
            elements=[
                {"id": "el-keep", "forId": "keep-1"},
                {"id": "el-doomed", "forId": "doomed-1"},
            ],
        )
        snapshot = copy.deepcopy(thread)
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        await self._run_resume(thread, session, context, data_layer)

        assert thread == snapshot
        # The filtering still happened — on the copy.
        emitted = context.emitter.resume_thread.await_args.args[0]
        assert [s["id"] for s in emitted["steps"]] == ["keep-1"]

    @pytest.mark.asyncio
    async def test_nested_steps_deleted_with_parent(self, mock_session_factory):
        """Steps nested under a doomed step (transitively) are doomed too,
        along with their elements — no dangling parentId rows."""
        doomed = self._flagged_step("doomed-1")
        child = dict(self._plain_step("child-1"), parentId="doomed-1")
        grandchild = dict(self._plain_step("grand-1"), parentId="child-1")
        kept = self._plain_step("keep-1")
        thread = self._thread(
            steps=[kept, doomed, child, grandchild],
            elements=[
                {"id": "el-child", "forId": "child-1"},
                {"id": "el-keep", "forId": "keep-1"},
            ],
        )
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        await self._run_resume(thread, session, context, data_layer)

        emitted = context.emitter.resume_thread.await_args.args[0]
        assert [s["id"] for s in emitted["steps"]] == ["keep-1"]
        assert [e["id"] for e in emitted["elements"]] == ["el-keep"]
        deleted_steps = {
            call.args[0] for call in data_layer.delete_step.await_args_list
        }
        assert deleted_steps == {"doomed-1", "child-1", "grand-1"}
        data_layer.delete_element.assert_awaited_once_with("el-child", self.THREAD_ID)
        deleted_msgs = {
            call.args[0]["id"] for call in context.emitter.delete_step.await_args_list
        }
        assert deleted_msgs == {"doomed-1", "child-1", "grand-1"}

    @pytest.mark.asyncio
    async def test_element_failure_skips_step_delete(self, mock_session_factory):
        """If an element deletion fails, the step must be kept (retryable
        on the next resume) — deleting it would orphan the element forever.
        The client-side delete_message is still emitted."""
        doomed = self._flagged_step("doomed-1")
        thread = self._thread(
            steps=[doomed],
            elements=[{"id": "el-doomed", "forId": "doomed-1"}],
        )
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()
        data_layer.delete_element.side_effect = RuntimeError("storage down")

        with patch("chainlit.socket.logger") as mock_logger:
            await self._run_resume(thread, session, context, data_layer)

        assert mock_logger.warning.called
        data_layer.delete_step.assert_not_awaited()
        context.emitter.delete_step.assert_awaited_once()
        context.emitter.resume_thread.assert_awaited_once()


class TestSupersedeAbandonedAskSessions:
    """A new session's first resume entry evicts abandoned ask-parked
    sessions of the same thread, so the resume="delete" cleanup can find
    the thread genuinely idle again (48h offer asks used to block it
    forever)."""

    THREAD_ID = "thread_123"

    def _thread(self, steps=None, elements=None):
        return {
            "id": self.THREAD_ID,
            "userIdentifier": "test_user_identifier",
            "metadata": {},
            "steps": steps if steps is not None else [],
            "elements": elements if elements is not None else [],
        }

    def _flagged_step(self, step_id):
        return {
            "id": step_id,
            "type": "assistant_message",
            "output": "flagged",
            "metadata": {"resume_policy": "delete"},
        }

    def _plain_step(self, step_id):
        return {
            "id": step_id,
            "type": "assistant_message",
            "output": "plain",
            "metadata": {},
        }

    def _session(self, mock_session_factory):
        session = mock_session_factory(
            has_first_interaction=False, thread_id=self.THREAD_ID
        )
        session.restored = False
        session.chat_started = False
        session.current_task = None
        session.thread_id_to_resume = self.THREAD_ID
        return session

    def _context(self, session):
        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()
        return mock_context

    def _config(self, on_chat_resume):
        mock_config = Mock()
        mock_config.code.on_chat_start = None
        mock_config.code.on_chat_resume = on_chat_resume
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None
        return mock_config

    async def _run_resume(self, thread, session, context, data_layer):
        on_chat_resume = AsyncMock()
        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", self._config(on_chat_resume)),
            patch("chainlit.socket.resume_thread", AsyncMock(return_value=thread)),
            patch("chainlit.socket.get_data_layer", return_value=data_layer),
            patch("chainlit.socket.chat_context"),
            patch("chainlit.socket.Message"),
        ):
            await connection_successful("sid-1")
        return on_chat_resume

    def _live_ask(self, step_id):
        return PendingAsk(
            step_dict={"id": step_id},
            spec=AskSpec(timeout=60, type="action", step_id=step_id),
            future=asyncio.get_event_loop().create_future(),
            deadline=time.monotonic() + 60,
        )

    def _live_task(self):
        task = Mock()
        task.done.return_value = False
        return task

    def _holder(
        self,
        session_id="abandoned-1",
        *,
        thread_id=None,
        disconnected=True,
        pending_ask="live",
        thread_ready_task="live",
        current_task=None,
    ):
        """An old session left behind on the thread. Its delete() mimics
        the real one's synchronous prefix: pop from the registry, cancel
        the ask — that pop is exactly what lets the cleanup gate recompute
        on the same entry."""
        import chainlit.session as session_module

        holder = Mock(spec=WebsocketSession)
        holder.id = session_id
        holder.thread_id = thread_id or self.THREAD_ID
        if disconnected is not None:
            holder.socket_disconnected = disconnected
        holder.pending_ask = (
            self._live_ask(f"ask-step-{session_id}")
            if pending_ask == "live"
            else pending_ask
        )
        holder.thread_ready_task = (
            self._live_task() if thread_ready_task == "live" else thread_ready_task
        )
        holder.current_task = current_task

        async def fake_delete():
            session_module.ws_sessions_id.pop(holder.id, None)
            if holder.pending_ask is not None:
                holder.pending_ask.cancel()
                holder.pending_ask = None

        holder.delete = AsyncMock(side_effect=fake_delete)
        return holder

    @pytest.mark.asyncio
    async def test_abandoned_ask_session_superseded_and_cleanup_fires(
        self, mock_session_factory
    ):
        """The core prod repro: a disconnected session whose hook is parked
        on a 48h offer ask no longer blocks the cleanup — it is deleted
        (user_sessions entry included) and the old offer step is doomed on
        the SAME entry."""
        import chainlit.session as session_module

        holder = self._holder("abandoned-1")
        old_offer = self._flagged_step("ask-step-abandoned-1")
        thread = self._thread(steps=[self._plain_step("keep-1"), old_offer])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        with (
            patch.dict(
                session_module.ws_sessions_id,
                {holder.id: holder, session.id: session},
                clear=True,
            ),
            patch.dict(user_sessions, {holder.id: {"stale": True}}),
        ):
            on_chat_resume = await self._run_resume(
                thread, session, context, data_layer
            )
            assert holder.id not in user_sessions

        holder.delete.assert_awaited_once()
        resumed = on_chat_resume.await_args.args[0]
        assert [s["id"] for s in resumed["steps"]] == ["keep-1"]
        data_layer.delete_step.assert_awaited_once_with("ask-step-abandoned-1")

    @pytest.mark.asyncio
    async def test_connected_ask_session_survives_and_blocks_cleanup(
        self, mock_session_factory
    ):
        """A second CONNECTED tab with a live ask: not superseded, and the
        cleanup stays blocked entirely (accepted parity)."""
        import chainlit.session as session_module

        holder = self._holder("second-tab", disconnected=False)
        offer = self._flagged_step("ask-step-second-tab")
        thread = self._thread(steps=[self._plain_step("keep-1"), offer])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        with patch.dict(
            session_module.ws_sessions_id,
            {holder.id: holder, session.id: session},
            clear=True,
        ):
            on_chat_resume = await self._run_resume(
                thread, session, context, data_layer
            )

        holder.delete.assert_not_awaited()
        resumed = on_chat_resume.await_args.args[0]
        assert [s["id"] for s in resumed["steps"]] == [
            "keep-1",
            "ask-step-second-tab",
        ]
        data_layer.delete_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_flag_attribute_treated_as_connected(
        self, mock_session_factory
    ):
        """The flag is read tolerantly (getattr default False): a
        Mock(spec=WebsocketSession) without the instance attribute — the
        shape every pre-existing test fake has — counts as connected."""
        import chainlit.session as session_module

        holder = self._holder("bare-fake", disconnected=None)
        thread = self._thread(steps=[self._flagged_step("live-1")])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        with patch.dict(
            session_module.ws_sessions_id,
            {holder.id: holder, session.id: session},
            clear=True,
        ):
            await self._run_resume(thread, session, context, data_layer)

        holder.delete.assert_not_awaited()
        data_layer.delete_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disconnected_pipeline_between_asks_not_superseded(
        self, mock_session_factory
    ):
        """A disconnected session whose task runs BETWEEN asks
        (pending_ask is None) is a working pipeline — untouched, and it
        keeps blocking the cleanup as designed."""
        import chainlit.session as session_module

        holder = self._holder(
            "pipeline",
            pending_ask=None,
            thread_ready_task=None,
            current_task=self._live_task(),
        )
        thread = self._thread(steps=[self._flagged_step("loader-1")])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        with patch.dict(
            session_module.ws_sessions_id,
            {holder.id: holder, session.id: session},
            clear=True,
        ):
            await self._run_resume(thread, session, context, data_layer)

        holder.delete.assert_not_awaited()
        data_layer.delete_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_expired_ask_not_superseded(self, mock_session_factory):
        """An expired ask fails the live-ask criterion: the session is about to die
        on its own timeout path — no eviction."""
        import chainlit.session as session_module

        expired = PendingAsk(
            step_dict={"id": "old-ask"},
            spec=AskSpec(timeout=60, type="action", step_id="old-ask"),
            future=asyncio.get_event_loop().create_future(),
            deadline=time.monotonic() - 1,
        )
        holder = self._holder("expired", pending_ask=expired, thread_ready_task=None)
        thread = self._thread(steps=[self._plain_step("keep-1")])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        with patch.dict(
            session_module.ws_sessions_id,
            {holder.id: holder, session.id: session},
            clear=True,
        ):
            await self._run_resume(thread, session, context, data_layer)

        holder.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_other_thread_session_untouched(self, mock_session_factory):
        """Criterion (в): a disconnected ask session of ANOTHER thread —
        potentially another user's — is never touched."""
        import chainlit.session as session_module

        holder = self._holder("foreign", thread_id="other_thread")
        thread = self._thread(steps=[self._plain_step("keep-1")])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        with patch.dict(
            session_module.ws_sessions_id,
            {holder.id: holder, session.id: session},
            clear=True,
        ):
            await self._run_resume(thread, session, context, data_layer)

        holder.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_incoming_session_never_superseded(self, mock_session_factory):
        """Identity exclusion: even a (theoretically) flagged incoming
        session with a live ask must not evict itself."""
        import chainlit.session as session_module

        session = self._session(mock_session_factory)
        session.socket_disconnected = True
        session.pending_ask = self._live_ask("own-ask")
        thread = self._thread(steps=[self._plain_step("keep-1")])
        context = self._context(session)
        data_layer = AsyncMock()

        with patch.dict(
            session_module.ws_sessions_id, {session.id: session}, clear=True
        ):
            await self._run_resume(thread, session, context, data_layer)

        session.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recheck_prevents_deleting_reconnected_candidate(
        self, mock_session_factory
    ):
        """delete() awaits (MCP close); a later candidate can reconnect
        meanwhile. The criteria are re-checked synchronously right before
        each delete — the revived session survives."""
        import chainlit.session as session_module

        first = self._holder("a-first")
        second = self._holder("b-second")

        async def slow_delete_reviving_second():
            session_module.ws_sessions_id.pop(first.id, None)
            # While the first delete is in flight, the second candidate's
            # socket comes back (restore() clears the flag).
            second.socket_disconnected = False

        first.delete = AsyncMock(side_effect=slow_delete_reviving_second)
        thread = self._thread(steps=[self._plain_step("keep-1")])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        with patch.dict(
            session_module.ws_sessions_id,
            {first.id: first, second.id: second, session.id: session},
            clear=True,
        ):
            await self._run_resume(thread, session, context, data_layer)

        first.delete.assert_awaited_once()
        second.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_failure_does_not_break_resume(self, mock_session_factory):
        """A zombie whose delete() raises (dirty files dir) must not break
        the incoming user's handshake — the resume completes and the
        failure is logged."""
        import chainlit.session as session_module

        holder = self._holder("dirty")
        holder.delete = AsyncMock(side_effect=RuntimeError("files dir race"))
        thread = self._thread(steps=[self._plain_step("keep-1")])
        session = self._session(mock_session_factory)
        context = self._context(session)
        data_layer = AsyncMock()

        with (
            patch.dict(
                session_module.ws_sessions_id,
                {holder.id: holder, session.id: session},
                clear=True,
            ),
            patch("chainlit.socket.logger") as mock_logger,
        ):
            on_chat_resume = await self._run_resume(
                thread, session, context, data_layer
            )

        holder.delete.assert_awaited_once()
        on_chat_resume.assert_awaited_once()
        context.emitter.resume_thread.assert_awaited_once()
        assert mock_logger.exception.called

    @pytest.mark.asyncio
    async def test_no_supersede_on_reentry(self, mock_session_factory):
        """Re-entries of the same session never supersede — consistent
        with the first-entry-only cleanup they exist to unblock."""
        import chainlit.session as session_module

        holder = self._holder("abandoned-1")
        thread = self._thread(steps=[self._plain_step("keep-1")])
        session = self._session(mock_session_factory)
        session.resume_processed = True
        session.restored = True
        session.has_first_interaction = True
        context = self._context(session)
        data_layer = AsyncMock()

        with patch.dict(
            session_module.ws_sessions_id,
            {holder.id: holder, session.id: session},
            clear=True,
        ):
            await self._run_resume(thread, session, context, data_layer)

        holder.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disconnect_sets_flag_before_hooks(self, mock_session_factory):
        """The flag is set at the very top of the disconnect handler —
        before on_chat_end/persist, which may hang for a long time."""
        session = mock_session_factory(has_first_interaction=False)
        session.to_clear = True
        session.socket_disconnected = False
        flag_at_hook_time = []

        async def record_flag():
            flag_at_hook_time.append(session.socket_disconnected)

        mock_config = Mock()
        mock_config.code.on_chat_end = record_flag

        with (
            patch.object(WebsocketSession, "get", return_value=session),
            patch("chainlit.socket.init_ws_context"),
            patch("chainlit.socket.config", mock_config),
        ):
            await disconnect("sid-gone")

        assert session.socket_disconnected is True
        assert flag_at_hook_time == [True]

    def test_restore_clears_disconnected_flag(self):
        """restore() is the single revival path: it must clear the flag
        set by a previous disconnect. A fresh session starts connected."""
        import chainlit.session as session_module

        session = WebsocketSession(
            id=str(uuid.uuid4()),
            socket_id="sid-flag-old",
            emit=Mock(),
            emit_call=Mock(),
            user_env={},
            client_type="webapp",
        )
        try:
            assert session.socket_disconnected is False
            session.socket_disconnected = True
            session.restore(new_socket_id="sid-flag-new")
            assert session.socket_disconnected is False
        finally:
            session_module.ws_sessions_id.pop(session.id, None)
            session_module.ws_sessions_sid.pop(session.socket_id, None)


class TestResumeThreadPersistBarrier:
    """The resume snapshot read waits for in-flight background persists."""

    @pytest.mark.asyncio
    async def test_resume_thread_waits_for_pending_persist(self):
        from chainlit.persist_barrier import _pending_persists, create_persist_task

        thread_id = "barrier_thread"
        mock_session = Mock(spec=WebsocketSession)
        mock_session.user = Mock(identifier="user123")
        mock_session.thread_id_to_resume = thread_id
        mock_session.id = "session_123"

        persisted = {"done": False}

        async def slow_persist():
            await asyncio.sleep(0.05)
            persisted["done"] = True

        observed = {}

        async def get_thread(thread_id):
            observed["persist_done"] = persisted["done"]
            return None

        data_layer = AsyncMock()
        data_layer.get_thread.side_effect = get_thread

        try:
            create_persist_task(slow_persist(), thread_id=thread_id)
            with patch("chainlit.socket.get_data_layer", return_value=data_layer):
                await resume_thread(mock_session)
        finally:
            _pending_persists.pop(thread_id, None)

        # get_thread ran only after the pending create_step-style task
        # finished — the snapshot cannot outrun the write anymore.
        assert observed["persist_done"] is True


class TestOrphanAskReplyConversion:
    """An ask_reply that cannot reach a live ask is rescued as a plain message.

    The client's send buffer flushes buffered replies BEFORE it emits
    connection_successful, so a reply typed into a dead form must not be
    silently dropped (the historical behavior) — it is converted into the
    same path a client_message takes, deferred until the handshake finished.
    """

    _pending_ask = staticmethod(TestAskSurvivesReconnect._pending_ask)

    @staticmethod
    def _text_value(**overrides):
        value = {
            "id": str(uuid.uuid4()),
            "threadId": "",
            "type": "user_message",
            "name": "user",
            "output": "typed into a dead form",
            "createdAt": "2026-08-24T10:00:00.000Z",
            "metadata": {"location": "http://localhost/"},
        }
        value.update(overrides)
        return value

    def _context(self, session):
        mock_context = Mock()
        mock_context.session = session
        mock_context.emitter = AsyncMock()
        return mock_context

    @staticmethod
    def _cleared(context):
        return [call.args[0] for call in context.emitter.clear.call_args_list]

    # -- Acceptance 0: the buffered reply survives and waits for the handshake

    @pytest.mark.asyncio
    async def test_buffered_orphan_text_reply_waits_for_handshake_then_converts(
        self, mock_session_factory
    ):
        gate = asyncio.Event()  # connection_successful has not finished yet
        session = mock_session_factory(pending_ask=None, connection_inited=gate)
        context = self._context(session)
        value = self._text_value()

        with (
            patch.object(WebsocketSession, "get", return_value=session),
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.process_message", new=AsyncMock()) as mock_process,
        ):
            await ask_reply("sid-1", {"stepId": "dead-step", "value": value})

            parked = list(session.deferred_ask_reply_tasks)
            assert parked, "the orphaned reply must be parked, not dropped"

            # Not converted while the handshake is still running: an
            # immediate conversion would land in a half-initialized session
            # (fresh thread / renamed resumed thread).
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            mock_process.assert_not_awaited()

            gate.set()
            await asyncio.gather(*parked)

            mock_process.assert_awaited_once()
            payload = mock_process.await_args.args[1]
            assert payload["message"]["output"] == value["output"]
            # The reply's parentId pointed into the dead ask — must not leak.
            assert payload["message"]["parentId"] is None
            assert "clear_ask" in self._cleared(context)
            # resume_thread wiped the local echo; the converted message must
            # be emitted explicitly or it stays invisible until the next
            # reconnect.
            context.emitter.send_step.assert_awaited_once()
            assert session.current_task is not None

    # -- Acceptance 1/2: reply typed after the handshake converts immediately

    @pytest.mark.asyncio
    async def test_orphan_text_reply_converts_after_open_handshake(
        self, mock_session_factory
    ):
        session = mock_session_factory(pending_ask=None)
        context = self._context(session)
        value = self._text_value()

        with (
            patch.object(WebsocketSession, "get", return_value=session),
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.process_message", new=AsyncMock()) as mock_process,
        ):
            await ask_reply("sid-1", {"stepId": "dead-step", "value": value})
            await asyncio.gather(*list(session.deferred_ask_reply_tasks))

            mock_process.assert_awaited_once()
            assert "clear_ask" in self._cleared(context)

    # -- Acceptance 3: redelivery of an already answered reply is not converted

    @pytest.mark.asyncio
    async def test_redelivered_answered_reply_is_not_converted(
        self, mock_session_factory
    ):
        session = mock_session_factory(
            pending_ask=None, last_resolved_ask_step_id="step-1"
        )
        context = self._context(session)

        with (
            patch.object(WebsocketSession, "get", return_value=session),
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.process_message", new=AsyncMock()) as mock_process,
        ):
            await ask_reply("sid-1", {"stepId": "step-1", "value": self._text_value()})
            await asyncio.gather(*list(session.deferred_ask_reply_tasks))

        mock_process.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resolving_reply_records_last_resolved_step_id(
        self, mock_session_factory
    ):
        pending = self._pending_ask()
        session = mock_session_factory(pending_ask=pending)

        with patch.object(WebsocketSession, "get", return_value=session):
            await ask_reply("sid-1", {"stepId": "step-1", "value": {"name": "go"}})

        assert pending.future.result() == {"name": "go"}
        assert session.last_resolved_ask_step_id == "step-1"

    @pytest.mark.asyncio
    async def test_legacy_ack_records_last_resolved_step_id(self, mock_session_factory):
        from chainlit.emitter import _make_legacy_ask_ack

        session = mock_session_factory()
        future = asyncio.get_event_loop().create_future()

        ack = _make_legacy_ask_ack(session, future, "step-1")
        ack({"name": "go"})

        assert future.result() == {"name": "go"}
        assert session.last_resolved_ask_step_id == "step-1"

    # -- Acceptance 5: mismatch with a LIVE ask is ignored (immediate path)

    @pytest.mark.asyncio
    async def test_mismatch_with_live_pending_is_ignored(self, mock_session_factory):
        pending = self._pending_ask()
        session = mock_session_factory(pending_ask=pending)
        context = self._context(session)

        with (
            patch.object(WebsocketSession, "get", return_value=session),
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.process_message", new=AsyncMock()) as mock_process,
        ):
            await ask_reply(
                "sid-1", {"stepId": "old-step", "value": self._text_value()}
            )

        assert not pending.future.done()
        assert not session.deferred_ask_reply_tasks
        mock_process.assert_not_awaited()
        # A clear_ask here would wipe the live form the server still waits on.
        assert "clear_ask" not in self._cleared(context)

    # -- Acceptance 6: expired-but-still-in-slot reply resolves, not converts

    @pytest.mark.asyncio
    async def test_expired_but_pending_reply_still_resolves(self, mock_session_factory):
        pending = self._pending_ask(deadline=time.monotonic() - 1)
        session = mock_session_factory(pending_ask=pending)
        value = self._text_value()

        with (
            patch.object(WebsocketSession, "get", return_value=session),
            patch("chainlit.socket.process_message", new=AsyncMock()) as mock_process,
        ):
            await ask_reply("sid-1", {"stepId": "step-1", "value": value})

        assert pending.future.result() == value
        assert not session.deferred_ask_reply_tasks
        mock_process.assert_not_awaited()

    # -- Acceptance 7: text typed right after stop converts, not drops

    @pytest.mark.asyncio
    async def test_reply_after_stop_converts(self, mock_session_factory):
        pending = self._pending_ask()
        session = mock_session_factory(pending_ask=pending)
        session.current_task = None
        context = self._context(session)
        mock_config = Mock()
        mock_config.code.on_stop = None

        with (
            patch.object(WebsocketSession, "get", return_value=session),
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", mock_config),
            patch("chainlit.socket.Message") as mock_message,
            patch("chainlit.socket.process_message", new=AsyncMock()) as mock_process,
        ):
            mock_message.return_value.send = AsyncMock()
            await stop("sid-1")

            # stop must NOT poison the dedup memory: the cancelled ask was
            # never answered, and a reply typed in the ~RTT window after the
            # stop click is live input.
            assert session.last_resolved_ask_step_id is None

            await ask_reply("sid-1", {"stepId": "step-1", "value": self._text_value()})
            await asyncio.gather(*list(session.deferred_ask_reply_tasks))

            mock_process.assert_awaited_once()

    # -- Acceptance 8/10: non-text orphans only clear the stale form

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "value",
        [
            # file-ask reply: a list, must not blow up on .get
            [{"id": "file-1", "name": "a.pdf", "path": "/tmp/a", "size": 1}],
            # element-ask reply: arbitrary app props + submitted flag
            {
                "submitted": True,
                "id": str(uuid.uuid4()),
                "type": "user_message",
                "output": "sneaky",
                "createdAt": "2026-08-24T10:00:00.000Z",
            },
            # action-ask reply
            {"id": "a1", "name": "continue", "forId": "step-1"},
            # garbage
            "plain string",
            None,
        ],
    )
    async def test_non_text_orphans_only_clear_ask(self, mock_session_factory, value):
        session = mock_session_factory(pending_ask=None)
        context = self._context(session)

        with (
            patch.object(WebsocketSession, "get", return_value=session),
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.process_message", new=AsyncMock()) as mock_process,
        ):
            await ask_reply("sid-1", {"stepId": "dead-step", "value": value})

        assert not session.deferred_ask_reply_tasks
        mock_process.assert_not_awaited()
        assert "clear_ask" in self._cleared(context)

    # -- Acceptance 11: live successor at execution time — convert, no clear_ask

    @pytest.mark.asyncio
    async def test_deferred_conversion_with_live_successor_keeps_its_form(
        self, mock_session_factory
    ):
        gate = asyncio.Event()
        session = mock_session_factory(pending_ask=None, connection_inited=gate)
        context = self._context(session)
        value = self._text_value()

        with (
            patch.object(WebsocketSession, "get", return_value=session),
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.process_message", new=AsyncMock()) as mock_process,
        ):
            await ask_reply("sid-1", {"stepId": "dead-step", "value": value})
            parked = list(session.deferred_ask_reply_tasks)
            assert parked

            # A background task (e.g. the consumer's offers) installed a new
            # live ask before the handshake finished.
            successor = self._pending_ask(
                step_dict={"id": "step-B", "parentId": "parent-B"},
                spec=AskSpec(timeout=60, type="action", step_id="step-B"),
            )
            session.pending_ask = successor

            gate.set()
            await asyncio.gather(*parked)

            mock_process.assert_awaited_once()
            # The successor's form must survive: no clear_ask, slot untouched.
            assert "clear_ask" not in self._cleared(context)
            assert session.pending_ask is successor
            assert not successor.future.done()

    # -- Acceptance 14: the gate opens even when a handshake branch raises

    @pytest.mark.asyncio
    async def test_handshake_gate_opens_when_branch_raises(self, mock_session_factory):
        gate = asyncio.Event()
        session = mock_session_factory(pending_ask=None, connection_inited=gate)
        session.restored = False
        session.chat_started = True
        session.thread_id_to_resume = None
        context = self._context(session)
        mock_config = Mock()
        mock_config.code.on_chat_start = None
        mock_config.code.on_chat_resume = None
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", mock_config),
            patch(
                "chainlit.socket.apply_transit_message",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            with pytest.raises(RuntimeError):
                await connection_successful("sid-1")

        assert gate.is_set()

    @pytest.mark.asyncio
    async def test_handshake_gate_opens_when_restore_itself_raises(
        self, mock_session_factory
    ):
        gate = asyncio.Event()
        session = mock_session_factory(pending_ask=None, connection_inited=gate)
        session.restored = False
        session.chat_started = True
        session.thread_id_to_resume = None
        context = self._context(session)
        mock_config = Mock()
        mock_config.code.on_chat_start = None
        mock_config.code.on_chat_resume = None
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", mock_config),
            patch(
                "chainlit.socket.restore_pending_ask",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            with pytest.raises(RuntimeError):
                await connection_successful("sid-1")

        assert gate.is_set()

    @pytest.mark.asyncio
    async def test_normal_handshake_opens_the_gate(self, mock_session_factory):
        gate = asyncio.Event()
        session = mock_session_factory(pending_ask=None, connection_inited=gate)
        session.restored = True
        session.chat_started = True
        session.thread_id_to_resume = None
        context = self._context(session)

        mock_config = Mock()
        mock_config.code.on_chat_start = None
        mock_config.code.on_chat_resume = None
        mock_config.code.on_thread_ready = None
        mock_config.code.on_profile_start = None

        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", mock_config),
        ):
            await connection_successful("sid-1")

        assert gate.is_set()
