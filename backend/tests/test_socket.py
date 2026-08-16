import asyncio
import json
import time
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

        with (
            patch("chainlit.socket.init_ws_context", return_value=mock_context),
            patch("chainlit.socket.config", mock_config),
        ):
            await connection_successful("sid-1")
            session.restored = True
            await connection_successful("sid-1")

        assert on_chat_start.call_count == 1


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
        return mock_config

    @pytest.mark.asyncio
    async def test_reconnect_of_loaded_page_skips_actions_and_element(
        self, mock_session_factory
    ):
        """Plain transport reconnect: the client still has its UI state — re-emitting
        the element would roll a live form back to a snapshot."""
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

        emitted = [call.args[0] for call in context.emitter.emit.call_args_list]
        assert "action" not in emitted
        assert "element" not in emitted
        session.emit_ask.assert_awaited_once()

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
                mock_ws.side_effect = lambda **kwargs: order.append("create")
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
    async def test_transcript_not_replayed_on_transport_reconnect(
        self, mock_session_factory
    ):
        pending = TestAskSurvivesReconnect._pending_ask()
        session = mock_session_factory(pending_ask=pending)
        context = self._context(session)

        with patch("chainlit.socket.chat_context") as mock_chat_context:
            mock_chat_context.get.return_value = [Mock()]
            await restore_pending_ask(context, client_has_ui_state=True)

        context.emitter.send_step.assert_not_awaited()
