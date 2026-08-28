"""The decorators register on ``config.code`` and wrap the hook, nothing more."""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio

from chainlit import callbacks, config
from chainlit.action import Action
from chainlit.message import Message
from chainlit.protocol.server import StepUpsert
from chainlit.types import ChatProfile, Starter, StarterCategory, ThreadDict
from chainlit.user import User
from tests.conftest import bind_context

# ``chainlit.step`` the attribute is the decorator; the module is only
# reachable by name.
step_module = sys.modules["chainlit.step"]


def _hook(callback: Any) -> Any:
    """A registered hook, asserted present so mypy lets it be called."""
    assert callback is not None
    return callback


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


THREAD: ThreadDict = {
    "id": "test_thread_id",
    "createdAt": "2023-01-01T00:00:00Z",
    "name": "Test Thread",
    "userId": "test_user_id",
    "userIdentifier": "test_user",
    "tags": [],
    "metadata": {},
    "steps": [],
    "elements": [],
}


async def test_password_auth_callback(test_config: config.ChainlitConfig):
    @callbacks.password_auth_callback
    async def auth_func(username: str, password: str) -> User | None:
        if username == "testuser" and password == "testpass":  # nosec B105
            return User(identifier="testuser")
        return None

    result = await _hook(test_config.code.password_auth_callback)(
        "testuser", "testpass"
    )
    assert isinstance(result, User)
    assert result.identifier == "testuser"
    assert await _hook(test_config.code.password_auth_callback)("x", "y") is None


async def test_oauth_callback(test_config: config.ChainlitConfig):
    with patch(
        "chainlit.callbacks.get_configured_oauth_providers", return_value=["google"]
    ):

        @callbacks.oauth_callback
        async def auth_func(
            provider_id: str,
            token: str,
            raw_user_data: dict,
            default_app_user: User,
            id_token: str | None = None,
        ) -> User | None:
            if provider_id == "google" and token == "valid_token":  # nosec B105
                return User(identifier="oauth_user")
            return None

    result = await _hook(test_config.code.oauth_callback)(
        "google", "valid_token", {}, User(identifier="default_user")
    )
    assert isinstance(result, User)
    assert result.identifier == "oauth_user"


async def test_oauth_callback_without_a_provider_refuses(test_config):
    with (
        patch("chainlit.callbacks.get_configured_oauth_providers", return_value=[]),
        pytest.raises(ValueError, match="oauth provider"),
    ):
        callbacks.oauth_callback(lambda *a: None)


async def test_on_message_runs_inside_a_run_step(
    ctx, session, frames, test_config: config.ChainlitConfig, monkeypatch
):
    monkeypatch.setattr(step_module, "config", test_config)
    received = None

    @callbacks.on_message
    async def handle_message(message: Message):
        nonlocal received
        received = message

    user_message = Message(content="Test message", author="User")
    await _hook(test_config.code.on_message)(user_message)

    assert received is user_message
    [run] = [f for f in frames(session, StepUpsert) if f.step.type == "run"]
    assert run.step.name == "on_message"
    assert run.step.parent_id == user_message.id


async def test_on_message_without_a_parameter(ctx, test_config, monkeypatch):
    monkeypatch.setattr(step_module, "config", test_config)
    called = False

    @callbacks.on_message
    async def handle_message():
        nonlocal called
        called = True

    await _hook(test_config.code.on_message)(Message(content="x"))
    assert called


async def test_on_stop(test_config: config.ChainlitConfig):
    called = False

    @callbacks.on_stop
    async def handle_stop():
        nonlocal called
        called = True

    await _hook(test_config.code.on_stop)()
    assert called


async def test_action_callback(test_config: config.ChainlitConfig):
    seen = None

    @callbacks.action_callback("test_action")
    async def handle_action(action: Action):
        nonlocal seen
        seen = action

    action = Action(name="test_action", payload={"value": "test_value"})
    await test_config.code.action_callbacks["test_action"](action)
    assert seen is action


async def test_author_rename(test_config: config.ChainlitConfig):
    @callbacks.author_rename
    async def rename_author(author: str) -> str:
        return "Assistant" if author == "AI" else author

    assert await _hook(test_config.code.author_rename)("AI") == "Assistant"
    assert await _hook(test_config.code.author_rename)("Human") == "Human"


async def test_on_app_startup_and_shutdown_take_sync_and_async(test_config):
    calls = []

    @callbacks.on_app_startup
    def sync_startup():
        calls.append("startup")

    @callbacks.on_app_shutdown
    async def async_shutdown():
        await asyncio.sleep(0)
        calls.append("shutdown")

    await _hook(test_config.code.on_app_startup)()
    await _hook(test_config.code.on_app_shutdown)()
    assert calls == ["startup", "shutdown"]


async def test_on_chat_start_runs_inside_a_run_step(
    ctx, session, frames, test_config, monkeypatch
):
    monkeypatch.setattr(step_module, "config", test_config)
    started = False

    @callbacks.on_chat_start
    async def handle_chat_start():
        nonlocal started
        started = True

    await _hook(test_config.code.on_chat_start)()
    assert started
    assert [f.step.name for f in frames(session, StepUpsert)] == ["on_chat_start"]


async def test_on_chat_resume_and_thread_ready_get_the_thread(test_config):
    seen = []

    @callbacks.on_chat_resume
    async def handle_chat_resume(thread: ThreadDict):
        seen.append(("resume", thread["id"]))

    @callbacks.on_thread_ready
    async def handle_thread_ready(thread: ThreadDict):
        seen.append(("ready", thread["id"]))

    await _hook(test_config.code.on_chat_resume)(THREAD)
    await _hook(test_config.code.on_thread_ready)(THREAD)
    assert seen == [("resume", "test_thread_id"), ("ready", "test_thread_id")]


async def test_on_chat_end(test_config):
    ended = False

    @callbacks.on_chat_end
    async def handle_chat_end():
        nonlocal ended
        ended = True

    await _hook(test_config.code.on_chat_end)()
    assert ended


async def test_a_raising_hook_is_logged_not_propagated(test_config):
    @callbacks.on_chat_end
    async def handle_chat_end():
        raise RuntimeError("boom")

    assert await _hook(test_config.code.on_chat_end)() is None


async def test_set_chat_profiles_with_language(test_config):
    @callbacks.set_chat_profiles
    async def get_chat_profiles(user, language):
        if language == "fr-CA":
            return [
                ChatProfile(name="Profil de test", markdown_description="Un profil")
            ]
        return [ChatProfile(name="Test Profile", markdown_description="A test profile")]

    [profile] = await _hook(test_config.code.set_chat_profiles)(None, "fr-CA")
    assert profile.name == "Profil de test"
    [profile] = await _hook(test_config.code.set_chat_profiles)(None, None)
    assert profile.name == "Test Profile"


async def test_set_chat_profiles_with_one_parameter(test_config):
    @callbacks.set_chat_profiles
    async def get_chat_profiles(user):
        return [ChatProfile(name="P", markdown_description="d")]

    # ``wrap_user_function`` drops what the hook does not declare.
    [profile] = await _hook(test_config.code.set_chat_profiles)(None, "fr-CA")
    assert profile.name == "P"


async def test_set_starters(test_config):
    @callbacks.set_starters
    async def get_starters(user, language):
        return [Starter(label="Test Label", message="Test message", icon="i")]

    [starter] = await _hook(test_config.code.set_starters)(None, None)
    assert starter.label == "Test Label"
    assert starter.to_dict() == {
        "label": "Test Label",
        "message": "Test message",
        "command": None,
        "icon": "i",
    }


async def test_set_starter_categories_with_chat_profile(test_config):
    @callbacks.set_starter_categories
    async def get_categories(user, language, chat_profile):
        return [
            StarterCategory(
                label="Cat",
                icon="https://example.com/profile.png",
                starters=[Starter(label=f"Starter for {chat_profile}", message="m")],
            )
        ]

    [category] = await _hook(test_config.code.set_starter_categories)(
        None, None, "test-profile"
    )
    assert category.to_dict()["starters"] == [
        {
            "label": "Starter for test-profile",
            "message": "m",
            "command": None,
            "icon": None,
        }
    ]


async def test_on_shared_thread_view(test_config):
    @callbacks.on_shared_thread_view
    async def allow(thread, viewer: User | None):
        if viewer is None:
            raise ValueError("Viewer not allowed")
        return viewer.identifier == "friend"

    friend = User(identifier="friend")
    assert await _hook(test_config.code.on_shared_thread_view)(THREAD, friend) is True
    assert not await _hook(test_config.code.on_shared_thread_view)(
        THREAD, User(identifier="x")
    )
    # The wrapper swallows the error; the route treats None as a refusal.
    assert not await _hook(test_config.code.on_shared_thread_view)(THREAD, None)


async def test_on_feedback(test_config):
    from chainlit.types import Feedback

    seen = None

    @callbacks.on_feedback
    async def handle(feedback: Feedback):
        nonlocal seen
        seen = feedback

    feedback = Feedback(forId="s1", value=1)
    await _hook(test_config.code.on_feedback)(feedback)
    assert seen is feedback


def test_chat_profile_with_config_overrides():
    from chainlit.config import ChainlitConfigOverrides, UISettings

    basic = ChatProfile(name="Basic", markdown_description="d")
    assert basic.config_overrides is None

    profile = ChatProfile(
        name="Custom",
        markdown_description="d",
        config_overrides=ChainlitConfigOverrides(
            ui=UISettings(name="Custom App Name", default_theme="light")
        ),
    )
    assert profile.config_overrides.ui.name == "Custom App Name"
    assert profile.to_dict()["name"] == "Custom"


def test_deleted_hooks_are_gone():
    """The FastAPI-shaped decorators are deleted, not deprecated."""
    for name in (
        "server_route",
        "header_auth_callback",
        "on_logout",
        "on_audio_start",
        "on_audio_chunk",
        "on_audio_end",
        "on_window_message",
        "send_window_message",
        "on_settings_update",
        "on_settings_edit",
    ):
        assert not hasattr(callbacks, name), name
