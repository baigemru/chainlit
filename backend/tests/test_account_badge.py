"""The unread count: who is told, how many of them, and what a bad hook costs.

The badge is pushed, so every test here is about a frame arriving somewhere
nobody asked for it. Three call sites recompute and push -- the handshake, the
account route and the emitter -- and the interesting property is the fan-out:
a person is usually in two tabs, and the one that changed the count is not the
one showing it.

The fan-out goes through the real `SessionRegistry.sessions_of` rather than a
stub, because the thing being asserted is the ownership predicate, and a stub
that filtered would only be asserting itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from litestar.testing import create_test_client

import chainlit as cl
from chainlit.account_badge import push_account_badge
from chainlit.emitter import Emitter
from chainlit.plugin import ChainlitPlugin
from chainlit.protocol.server import AccountBadge
from chainlit.security import chainlit_auth
from chainlit.ws.registry import SessionRegistry

pytestmark = pytest.mark.usefixtures("test_config")

SECRET = "test-secret-not-a-real-one-but-long-enough-for-hs256"
COOKIE = "access_token"


class _User:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier


class _Session:
    """The five things the registry indexes, plus the queue the push writes."""

    def __init__(self, id: str, user: Optional[str]) -> None:
        self.id = id
        self.user = _User(user) if user is not None else None
        self.sent: List[Any] = []
        self.has_live_ask = False
        self.has_live_task = False
        self.has_parked_reply = False
        self.live_ask_step_ids: List[str] = []

    def send(self, msg: Any) -> bool:
        self.sent.append(msg)
        return True


def _registry_with(*sessions: tuple[str, Optional[str]]) -> tuple[Any, List[Any]]:
    registry = SessionRegistry()
    built = []
    for index, (thread, owner) in enumerate(sessions):
        session = _Session(f"s{index}", owner)
        registry.register(session, thread_id=thread, user_identifier=owner)
        built.append(session)
    return registry, built


# --- the fan-out -------------------------------------------------------------


async def test_the_frame_reaches_every_session_of_that_user_and_no_other(
    test_config: Any,
) -> None:
    @cl.on_account_badge
    async def badge(user) -> int:
        return 4

    registry, (ada_a, ada_b, bob) = _registry_with(
        ("t1", "ada"), ("t2", "ada"), ("t3", "bob")
    )

    await push_account_badge(registry, _User("ada"))

    assert ada_a.sent == [AccountBadge(count=4)]
    assert ada_b.sent == [AccountBadge(count=4)]
    assert bob.sent == [], "the count of one user reached another"


async def test_without_a_hook_no_frame_is_ever_sent(test_config: Any) -> None:
    """The client renders nothing for "no badge", which is the right display
    for an application that has no such concept. A zero would be a claim."""
    registry, (session,) = _registry_with(("t1", "ada"))

    await push_account_badge(registry, _User("ada"))

    assert session.sent == []


async def test_a_hook_that_does_not_answer_an_int_is_a_type_error(
    test_config: Any,
) -> None:
    """A Struct constructor validates nothing, so `True` would go out as
    `{"count": 1}` and the badge would read "1 unread" forever."""

    @cl.on_account_badge
    async def badge(user) -> Any:
        return True

    registry, (session,) = _registry_with(("t1", "ada"))

    with pytest.raises(TypeError):
        await push_account_badge(registry, _User("ada"))
    assert session.sent == []


# --- the account route -------------------------------------------------------


def _auth():
    return chainlit_auth(token_secret=SECRET)


def test_reading_the_account_page_pushes_to_the_users_other_tabs(
    test_config: Any,
) -> None:
    """The application marks things seen inside `on_account_load`, so the
    count the page is answered with is stale the moment it is built -- which
    is exactly why the recompute happens after it and goes to the chat tab."""
    import msgspec

    class Account(msgspec.Struct):
        plan: str = "free"

    seen: List[str] = []
    test_config.code.account = Account

    @cl.on_account_load
    async def load(user):
        seen.append("marked")
        return None

    @cl.on_account_badge
    async def badge(user) -> int:
        return 0 if seen else 9

    async def on_chat_start() -> None:  # the plugin refuses an app with no entry
        return None

    test_config.code.on_chat_start = on_chat_start
    plugin = ChainlitPlugin(test_config, auth=_auth())
    registry = plugin.runner.registry
    session = _Session("s0", "ada")
    registry.register(session, thread_id="t1", user_identifier="ada")

    with create_test_client(route_handlers=[], plugins=[plugin]) as client:
        client.cookies.set(COOKIE, _auth().create_token(identifier="ada"))
        response = client.get("/project/account")

    assert response.status_code == 200
    assert session.sent == [AccountBadge(count=0)]


def test_a_badge_hook_that_raises_on_the_get_route_fails_the_request(
    test_config: Any,
) -> None:
    """The other half of the handshake's swallow. Here the application asked:
    a count it could not compute is a 500, not a page served with a badge
    silently missing, which nobody would ever notice was broken."""
    import msgspec

    class Account(msgspec.Struct):
        plan: str = "free"

    test_config.code.account = Account

    @cl.on_account_badge
    async def badge(user) -> int:
        raise RuntimeError("the counter is down")

    async def on_chat_start() -> None:
        return None

    test_config.code.on_chat_start = on_chat_start
    plugin = ChainlitPlugin(test_config, auth=_auth())

    with create_test_client(
        route_handlers=[], plugins=[plugin], raise_server_exceptions=False
    ) as client:
        client.cookies.set(COOKIE, _auth().create_token(identifier="ada"))
        response = client.get("/project/account")

    assert response.status_code == 500


# --- the emitter -------------------------------------------------------------


async def test_the_emitter_recomputes_and_pushes_for_its_own_user(
    test_config: Any,
) -> None:
    """What a run in the chat calls after it changed something on the page.
    The number is not an argument: the hook is the only thing that knows it."""
    counts = iter([5, 2])

    @cl.on_account_badge
    async def badge(user) -> int:
        return next(counts)

    registry, (chat, page) = _registry_with(("t1", "ada"), ("t2", "ada"))

    class _Runner:
        pass

    runner = _Runner()
    runner.registry = registry  # type: ignore[attr-defined]
    chat.runner = runner  # type: ignore[attr-defined]

    await Emitter(chat).refresh_account_badge()  # type: ignore[arg-type]

    assert chat.sent == [AccountBadge(count=5)]
    assert page.sent == [AccountBadge(count=5)]


# --- the handshake -----------------------------------------------------------


@pytest.fixture
def frontend_dir(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    return dist


def _read_until(ws: Any, tag: str, *, limit: int = 30) -> List[Dict[str, Any]]:
    frames: List[Dict[str, Any]] = []
    for _ in range(limit):
        frames.append(json.loads(ws.receive_text(timeout=5.0)))
        if frames[-1]["t"] == tag:
            return frames
    raise AssertionError(f"never saw {tag!r}: {[f['t'] for f in frames]}")


def test_the_badge_reaches_a_real_socket_after_session_ready(
    test_config: Any, frontend_dir: Path
) -> None:
    """A browser that has been shut for a day arrives knowing nothing about
    the page and there is no route to ask, so the frame has to be on the
    handshake -- after `session.ready`, which is what the client flushes its
    buffer on, and therefore after the replay that follows it."""

    @cl.on_account_badge
    async def badge(user) -> int:
        return 11

    async def on_chat_start() -> None:
        return None

    test_config.code.on_chat_start = on_chat_start
    plugin = ChainlitPlugin(test_config, frontend_dir=frontend_dir, auth=None)

    with (
        create_test_client(plugins=[plugin]) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_text(json.dumps({"t": "hello", "pageLoad": True}))
        frames = _read_until(ws, "account.badge")

    tags = [frame["t"] for frame in frames]
    assert tags[0] == "session.ready"
    assert frames[-1]["count"] == 11


def test_a_badge_hook_that_raises_on_hello_does_not_take_the_socket_down(
    test_config: Any, frontend_dir: Path
) -> None:
    """Nobody asked for this number on a handshake; it is offered. An
    exception escaping `on_ready` lands in `_serve`'s task group, which
    cancels the reader and the heartbeat with it -- so a badge hook having a
    bad day would close the socket of every user who opened the app."""

    @cl.on_account_badge
    async def badge(user) -> int:
        raise RuntimeError("the counter is down")

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo: {msg.content}").send()

    test_config.code.on_message = on_message
    plugin = ChainlitPlugin(test_config, frontend_dir=frontend_dir, auth=None)

    with (
        create_test_client(plugins=[plugin]) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_text(json.dumps({"t": "hello", "pageLoad": True}))
        handshake = _read_until(ws, "task.indicator")
        # The socket is still usable afterwards, which is the actual claim:
        # a cancelled task group would have closed it by now.
        ws.send_text(
            json.dumps(
                {
                    "t": "message.send",
                    "message": {
                        "id": "m1",
                        "type": "user_message",
                        "output": "hi",
                        "name": "User",
                        "createdAt": "2026-09-19T00:00:00.000000Z",
                    },
                }
            )
        )
        echoed = _read_until(ws, "step.upsert")

    assert handshake[0]["t"] == "session.ready"
    assert "account.badge" not in [frame["t"] for frame in handshake]
    assert echoed[-1]["step"]["output"] == "echo: hi"
