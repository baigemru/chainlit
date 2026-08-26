"""Tests for the on_profile_start hook and the in-place profile switch.

Covers the acceptance items of the task doc that are automatable at this
level: the feature gate (П1), the basic switch (П2), the ask slot handover
(П4), stop (П10), serialization (П11), self-switch (П12), switching from
on_message (П13), an unknown profile (П14) and — the regression that
motivated ред. 2 — switching while on_chat_start is still live (П18).
"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from chainlit.config import config
from chainlit.session import WebsocketSession
from chainlit.socket import perform_profile_switch
from chainlit.types import ProfileStartInfo
from chainlit.user_session import user_sessions


class _Profile:
    def __init__(self, name):
        self.name = name


@pytest.fixture
def switch_env(mock_session_factory, monkeypatch):
    """A session on profile A, with profiles A/B declared and the flag on."""
    session = mock_session_factory(chat_profile="A", has_first_interaction=False)
    session.config = config
    session.resolve_config = AsyncMock()
    session.to_persistable = Mock(return_value={})
    session.language = "en-US"
    session.task_counter = 0

    monkeypatch.setattr(config.features, "hot_swap_chat_profile", True)
    monkeypatch.setattr(
        config.code,
        "set_chat_profiles",
        AsyncMock(return_value=[_Profile("A"), _Profile("B")]),
    )
    monkeypatch.setattr(config.code, "on_profile_start", None)

    emitter = Mock()
    emitter.emit = AsyncMock()
    emitter.clear = AsyncMock()
    emitter.task_start = AsyncMock()
    emitter.task_end = AsyncMock()

    ctx = Mock()
    ctx.emitter = emitter
    ctx.session = session
    monkeypatch.setattr("chainlit.socket.context", ctx)

    # The procedure re-checks the registry after every await, so a session
    # that is not registered reads as evicted.
    from chainlit.session import ws_sessions_id

    ws_sessions_id[session.id] = session
    user_sessions[session.id] = {}
    yield session, emitter
    user_sessions.pop(session.id, None)
    ws_sessions_id.pop(session.id, None)


class TestFeatureGate:
    async def test_flag_off_changes_nothing(self, switch_env, monkeypatch):
        """П1: with the flag off nothing is mutated and nothing is emitted."""
        session, emitter = switch_env
        monkeypatch.setattr(config.features, "hot_swap_chat_profile", False)

        assert await perform_profile_switch(session, "B") is False
        assert session.chat_profile == "A"
        emitter.emit.assert_not_called()

    async def test_unknown_profile_refused(self, switch_env):
        """П14: a name outside this user's profiles is refused, silently."""
        session, emitter = switch_env

        assert await perform_profile_switch(session, "нет такого") is False
        assert session.chat_profile == "A"
        emitter.emit.assert_not_called()

    async def test_empty_name_refused(self, switch_env):
        session, _ = switch_env
        assert await perform_profile_switch(session, "") is False
        assert session.chat_profile == "A"

    async def test_noop_switch_does_not_run_hook(self, switch_env, monkeypatch):
        """Switching to the current profile is a no-op, not a hook restart."""
        session, emitter = switch_env
        hook = AsyncMock()
        monkeypatch.setattr(config.code, "on_profile_start", hook)

        assert await perform_profile_switch(session, "A") is True
        hook.assert_not_called()
        emitter.emit.assert_not_called()


class TestBasicSwitch:
    async def test_switch_sets_profile_and_emits(self, switch_env):
        """П2: profile changes, client is told, config is re-resolved."""
        session, emitter = switch_env

        assert await perform_profile_switch(session, "B") is True

        assert session.chat_profile == "B"
        assert user_sessions[session.id]["chat_profile"] == "B"
        session.resolve_config.assert_awaited_once()
        payload = emitter.emit.await_args.args[1]
        assert payload["chatProfile"] == "B"
        assert payload["previous"] == "A"
        assert payload["sync"] is False

    async def test_chat_settings_cleared(self, switch_env):
        """Step 6a: the old profile's settings form must not survive."""
        session, _ = switch_env
        session.chat_settings = {"model": "old"}

        await perform_profile_switch(session, "B")

        assert session.chat_settings == {}

    async def test_hook_receives_info(self, switch_env, monkeypatch):
        seen = []

        async def hook(info):
            seen.append(info)

        monkeypatch.setattr(config.code, "on_profile_start", hook)

        await perform_profile_switch(session=switch_env[0], name="B", payload={"q": 1})
        await switch_env[0].profile_start_task

        assert len(seen) == 1
        info: ProfileStartInfo = seen[0]
        assert (info.profile, info.previous, info.payload) == ("B", "A", {"q": 1})
        assert info.source == "client"

    async def test_no_persist_before_first_interaction(self, switch_env, monkeypatch):
        """Step 8 guard: update_thread upserts, so an early persist would
        create an empty thread row."""
        session, _ = switch_env
        persist = AsyncMock()
        monkeypatch.setattr("chainlit.socket.persist_user_session", persist)

        session.to_persistable = Mock(
            side_effect=lambda: {"chat_profile": session.chat_profile}
        )

        await perform_profile_switch(session, "B")
        persist.assert_not_called()

        session.has_first_interaction = True
        await perform_profile_switch(session, "A")
        persist.assert_awaited_once()
        # The snapshot must be of the NEW profile: persisting before step 6
        # would write the old one.
        assert persist.await_args.args == (session.thread_id, {"chat_profile": "A"})


class TestAskSlot:
    async def test_live_ask_is_quenched(self, switch_env):
        """П4: without this the hook's first ask is refused and returns None."""
        session, emitter = switch_env
        ask = Mock()
        ask.cancel = Mock()
        ask.is_live = True
        session.pending_ask = ask

        await perform_profile_switch(session, "B")

        ask.cancel.assert_called_once()
        assert session.pending_ask is None
        emitter.clear.assert_awaited_once_with("clear_ask")


class TestTaskSlots:
    async def _sleeper(self):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    async def test_previous_hook_cancelled(self, switch_env):
        session, _ = switch_env
        old = asyncio.create_task(self._sleeper())
        session.profile_start_task = old

        await perform_profile_switch(session, "B")
        await asyncio.sleep(0)

        assert old.cancelled() or old.cancelling()

    async def test_thread_ready_task_cancelled(self, switch_env):
        session, _ = switch_env
        hook = asyncio.create_task(self._sleeper())
        session.thread_ready_task = hook

        await perform_profile_switch(session, "B")
        await asyncio.sleep(0)

        assert hook.cancelled() or hook.cancelling()

    async def test_live_on_chat_start_is_cancelled(self, switch_env):
        """П18 — the ред. 2 regression.

        At cold start current_task holds on_chat_start. A wizard blocked on
        an ask must not keep running under the profile the user just left.
        """
        session, _ = switch_env
        chat_start = asyncio.create_task(self._sleeper())
        session.current_task = chat_start

        await perform_profile_switch(session, "B")
        await asyncio.sleep(0)

        assert chat_start.cancelled() or chat_start.cancelling()

    async def test_caller_is_never_cancelled(self, switch_env):
        """П13: switching from inside on_message must not kill the caller."""
        session, _ = switch_env
        reached_end = []

        async def caller():
            session.current_task = asyncio.current_task()
            await perform_profile_switch(session, "B")
            reached_end.append(True)

        await asyncio.create_task(caller())

        assert reached_end == [True]
        assert session.chat_profile == "B"

    async def test_self_switch_from_hook_is_legal(self, switch_env, monkeypatch):
        """П12: the hook may switch again; it is the caller, so it lives."""
        session, _ = switch_env
        order = []

        async def hook(info):
            order.append(info.profile)
            if info.profile == "B":
                await perform_profile_switch(session, "A", source="server")
            order.append(f"done-{info.profile}")

        monkeypatch.setattr(config.code, "on_profile_start", hook)

        await perform_profile_switch(session, "B")
        first = session.profile_start_task
        await first
        second = session.profile_start_task
        if second is not first:
            await second

        assert order[0] == "B"
        assert "done-B" in order
        assert session.chat_profile == "A"


class TestSerialization:
    async def test_two_switches_are_serialized(self, switch_env, monkeypatch):
        """П11: the lock keeps two concurrent switches from both spawning a
        hook and losing the first one's slot."""
        session, _ = switch_env
        session.profile_switch_lock = asyncio.Lock()
        inside = []

        real_resolve = session.resolve_config

        async def slow_resolve():
            inside.append(len(inside))
            assert len(inside) == 1 or session.profile_switch_lock.locked()
            await asyncio.sleep(0.01)
            inside.pop()
            return await real_resolve()

        session.resolve_config = slow_resolve

        await asyncio.gather(
            perform_profile_switch(session, "B"),
            perform_profile_switch(session, "A"),
        )

        assert session.chat_profile in ("A", "B")


class TestDecoratorRegistration:
    def test_decorator_registers_and_returns_func(self, monkeypatch):
        import chainlit as cl

        monkeypatch.setattr(config.code, "on_profile_start", None)

        async def my_hook(info):
            return info

        returned = cl.on_profile_start(my_hook)

        assert returned is my_hook
        assert config.code.on_profile_start is not None
        assert config.code.on_profile_start is not my_hook


class TestSlotReaders:
    def test_live_work_sees_the_new_slot(self, switch_env):
        """The F5 keep-alive must not drop a session whose only live work is
        the profile hook."""
        from chainlit.socket import _session_has_live_work

        session, _ = switch_env
        session.pending_ask = None
        session.current_task = None
        session.thread_ready_task = None
        session.profile_start_task = None
        assert _session_has_live_work(session) is False

        task = Mock()
        task.done.return_value = False
        session.profile_start_task = task
        assert _session_has_live_work(session) is True

    def test_resume_policy_checks_the_new_slot(self, switch_env):
        """A second-tab resume must not delete steps under a live hook."""
        from chainlit.resume_policy import thread_has_live_task

        session, _ = switch_env
        session.thread_id = "t-live"
        session.current_task = None
        session.thread_ready_task = None
        task = Mock()
        task.done.return_value = False
        session.profile_start_task = task

        assert thread_has_live_task("t-live") is True
        task.done.return_value = True
        assert thread_has_live_task("t-live") is False


def test_session_declares_slot_and_lock():
    """Mock(spec=WebsocketSession) builds its spec from dir(): the class-level
    declaration is what makes the slot fakeable in every other test."""
    assert "profile_start_task" in dir(WebsocketSession)
    assert "profile_switch_lock" in dir(WebsocketSession)


class TestSurvivingMutants:
    """Regressions for mutations that the first cut of this file let live."""

    async def test_step7_resets_config_before_resolving(self, switch_env):
        """П9: without the reset, resolve_config returns the cached value and
        the new profile's config_overrides silently do not apply."""
        session, _ = switch_env
        seen = []
        session.config = object()  # a resolved, profile-specific config
        session.resolve_config = AsyncMock(
            side_effect=lambda: seen.append(session.config)
        )

        await perform_profile_switch(session, "B")

        assert seen == [config], "resolve_config must see the global config"

    async def test_emit_lands_before_the_hook_starts(self, switch_env, monkeypatch):
        """Step 9 before step 10: anything rewriting client state must land
        before the hook can emit anything of its own."""
        session, emitter = switch_env
        order = []
        emitter.emit = AsyncMock(side_effect=lambda *a, **k: order.append("emit"))

        async def hook(info):
            order.append("hook")

        monkeypatch.setattr(config.code, "on_profile_start", hook)

        await perform_profile_switch(session, "B")
        await session.profile_start_task

        assert order[0] == "emit"

    async def test_indicator_resync_runs(self, switch_env):
        """П16: step 11 is the level-triggered word on the indicator."""
        session, emitter = switch_env
        session.task_counter = 2
        session.pending_ask = None

        await perform_profile_switch(session, "B")
        emitter.task_start.assert_awaited()

        session.task_counter = 0
        await perform_profile_switch(session, "A")
        emitter.task_end.assert_awaited()

    async def test_source_server_reaches_the_hook(self, switch_env, monkeypatch):
        """П2: cl.switch_chat_profile must be distinguishable from a click."""
        session, _ = switch_env
        seen = []
        monkeypatch.setattr(
            config.code, "on_profile_start", lambda info: _collect(seen, info)
        )

        await perform_profile_switch(session, "B", source="server")
        await session.profile_start_task

        assert seen[0].source == "server"

    async def test_decorator_wraps_with_task_physics(self, monkeypatch):
        """with_task=True is what gives the hook the on_chat_start physics:
        the indicator counter, the swallowed CancelledError and the
        ErrorMessage on anything else.

        Structural on purpose — the wrapper reaches for the real ambient
        context, which this file's fixture does not provide. П3 and the e2e
        app cover the runtime behaviour.
        """
        import inspect

        import chainlit as cl

        src = inspect.getsource(cl.on_profile_start)
        assert "wrap_user_function(func, with_task=True)" in src


async def _collect(sink, info):
    sink.append(info)


class TestStopCancelsHook:
    async def test_stop_cancels_the_profile_hook(self, switch_env, monkeypatch):
        """П10 — reader one of four. The stop button must not leave the hook
        running with its question unquenched."""
        from unittest.mock import patch

        from chainlit.socket import stop

        session, emitter = switch_env
        task = Mock()
        task.done.return_value = False
        session.profile_start_task = task
        session.current_task = None
        session.thread_ready_task = None
        session.pending_ask = None

        ctx = Mock()
        ctx.emitter = emitter
        ctx.session = session
        mock_config = Mock()
        mock_config.code.on_stop = None

        with (
            patch.object(WebsocketSession, "get", return_value=session),
            patch("chainlit.socket.init_ws_context", return_value=ctx),
            patch("chainlit.socket.config", mock_config),
            patch("chainlit.socket.Message") as mock_message,
        ):
            mock_message.return_value.send = AsyncMock()
            await stop("sid-1")

        task.cancel.assert_called_once()


class TestDeleteCancelsHook:
    async def test_session_delete_cancels_the_hook(
        self, mock_websocket_session, tmp_path
    ):
        """Reader four: a surviving hook would be a zombie writing into the
        thread after its session is gone."""
        session = mock_websocket_session
        session.files_dir = tmp_path / "files"
        session.files_dir.mkdir()
        session.thread_ready_task = None
        session.pending_ask = None
        session.socket_id = "sid-del"
        session.id = "sess-del"
        session.deferred_ask_reply_tasks = []
        session.mcp_sessions = {}
        session.parked_conversions = []
        task = Mock()
        task.done.return_value = False
        session.profile_start_task = task

        await WebsocketSession.delete(session)

        task.cancel.assert_called_once()
