"""The unread count: who is told, how many of them, and what a bad hook costs.

The badge is pushed, so every test here is about a frame arriving somewhere
nobody asked for it. Three call sites recompute and push -- the handshake, the
account route and the emitter -- and the interesting property is the fan-out:
a person is usually in two tabs, and the one that changed the count is not the
one showing it.

The fan-out goes through the real `SessionRegistry.sessions_of` rather than a
stub, because the thing being asserted is the ownership predicate, and a stub
that filtered would only be asserting itself.

The second thing asserted here is that the hook is *handed* the account values
at all three call sites, and that on the route they are the ones the load hook
has just marked -- that write belongs to the request's session and commits
after the response is built, so a hook that read the row itself would push a
number from before the mark.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

import msgspec
import pytest
from litestar.testing import create_test_client
from msgspec import Meta

import chainlit as cl
from chainlit.account_badge import push_account_badge
from chainlit.emitter import Emitter
from chainlit.plugin import ChainlitPlugin
from chainlit.protocol.server import AccountBadge
from chainlit.runner import ApplicationRunner
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
    async def badge(user, account) -> int:
        return 4

    registry, (ada_a, ada_b, bob) = _registry_with(
        ("t1", "ada"), ("t2", "ada"), ("t3", "bob")
    )

    await push_account_badge(registry, _User("ada"), None)

    assert ada_a.sent == [AccountBadge(count=4)]
    assert ada_b.sent == [AccountBadge(count=4)]
    assert bob.sent == [], "the count of one user reached another"


async def test_without_a_hook_no_frame_is_ever_sent(test_config: Any) -> None:
    """The client renders nothing for "no badge", which is the right display
    for an application that has no such concept. A zero would be a claim."""
    registry, (session,) = _registry_with(("t1", "ada"))

    await push_account_badge(registry, _User("ada"), None)

    assert session.sent == []


async def test_a_hook_that_does_not_answer_an_int_is_a_type_error(
    test_config: Any,
) -> None:
    """A Struct constructor validates nothing, so `True` would go out as
    `{"count": 1}` and the badge would read "1 unread" forever."""

    @cl.on_account_badge
    async def badge(user, account) -> Any:
        return True

    registry, (session,) = _registry_with(("t1", "ada"))

    with pytest.raises(TypeError):
        await push_account_badge(registry, _User("ada"), None)
    assert session.sent == []


# --- the account route -------------------------------------------------------


def _auth():
    return chainlit_auth(token_secret=SECRET)


def test_reading_the_account_page_pushes_the_count_of_the_marked_values(
    test_config: Any,
) -> None:
    """The load hook marks the feed seen, and the badge hook is handed *that*
    -- so the number that reaches the chat tab is 0 in the same request.

    The mark is a write of the request's own session, which commits after the
    response is built; a hook that counted by reading `users.account` through
    a session of its own would answer 9 here and be one page load behind.
    """

    class Account(msgspec.Struct):
        seen: bool = False

    test_config.code.account = Account

    @cl.on_account_load
    async def load(user, account, tab):
        return msgspec.structs.replace(account, seen=True)

    @cl.on_account_badge
    async def badge(user, account) -> int:
        return 0 if account.seen else 9

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


def test_the_route_hands_the_hook_the_stored_shape_not_the_rendered_one(
    test_config: Any,
) -> None:
    """One shape at all three call sites. A `readOnly` leaf is derived and
    never stored, so the two socket-side callers -- which read the row --
    could not hand one over; the route strips it too rather than making the
    hook guess which caller it is answering."""

    class Account(msgspec.Struct):
        plan: Annotated[str, Meta(extra_json_schema={"readOnly": True})] = "free"

    seen: List[Any] = []
    test_config.code.account = Account

    @cl.on_account_load
    async def load(user, account, tab):
        return msgspec.structs.replace(account, plan="pro")

    @cl.on_account_badge
    async def badge(user, account) -> int:
        seen.append(account)
        return 1

    async def on_chat_start() -> None:
        return None

    test_config.code.on_chat_start = on_chat_start
    plugin = ChainlitPlugin(test_config, auth=_auth())

    with create_test_client(route_handlers=[], plugins=[plugin]) as client:
        client.cookies.set(COOKIE, _auth().create_token(identifier="ada"))
        response = client.get("/project/account")

    # Shown as "pro", counted as what the store holds.
    assert response.json()["values"]["plan"] == "pro"
    assert seen[0].plan == "free"


def test_a_badge_hook_that_raises_on_the_get_route_fails_the_request(
    test_config: Any,
) -> None:
    """The other half of the handshake's swallow. Here the application asked:
    a count it could not compute is a 500, not a page served with a badge
    silently missing, which nobody would ever notice was broken."""

    class Account(msgspec.Struct):
        plan: str = "free"

    test_config.code.account = Account

    @cl.on_account_badge
    async def badge(user, account) -> int:
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
    async def badge(user, account) -> int:
        return next(counts)

    registry, (chat, page) = _registry_with(("t1", "ada"), ("t2", "ada"))

    class _Runner:
        async def stored_account(self, user: Any) -> Any:
            return None

    runner = _Runner()
    runner.registry = registry  # type: ignore[attr-defined]
    chat.runner = runner  # type: ignore[attr-defined]

    await Emitter(chat).refresh_account_badge()  # type: ignore[arg-type]

    assert chat.sent == [AccountBadge(count=5)]
    assert page.sent == [AccountBadge(count=5)]


async def test_the_emitter_hands_the_hook_what_the_runner_read(
    test_config: Any,
) -> None:
    """The run in the chat changed something the page counts; the values come
    from the runner's persistence, not from a session the hook opens."""

    class Account(msgspec.Struct):
        unseen: int = 0

    test_config.code.account = Account
    seen: List[Any] = []

    @cl.on_account_badge
    async def badge(user, account) -> int:
        seen.append(account)
        return account.unseen

    registry, (chat,) = _registry_with(
        ("t1", "ada"),
    )

    class _Runner:
        async def stored_account(self, user: Any) -> Any:
            return Account(unseen=3)

    runner = _Runner()
    runner.registry = registry  # type: ignore[attr-defined]
    chat.runner = runner  # type: ignore[attr-defined]

    await Emitter(chat).refresh_account_badge()  # type: ignore[arg-type]

    assert seen[0] == Account(unseen=3)
    assert chat.sent == [AccountBadge(count=3)]


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

    class Account(msgspec.Struct):
        unseen: int = 11

    test_config.code.account = Account

    @cl.on_account_badge
    async def badge(user, account) -> int:
        # From the values the engine read, not from a count of its own: this
        # is the end-to-end proof that the hello call site hands them over.
        return account.unseen

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
    async def badge(user, account) -> int:
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


# --- what the two socket-side call sites read --------------------------------


class _FakeUsers:
    def __init__(self, stored: Dict[str, Any]) -> None:
        self.stored = stored
        self.asked: List[str] = []

    async def get_account(self, identifier: str) -> Dict[str, Any]:
        self.asked.append(identifier)
        return self.stored


class _FakePersistence:
    """`uow()` is the whole surface the runner uses for this read."""

    def __init__(self, stored: Dict[str, Any]) -> None:
        self.users = _FakeUsers(stored)

    @asynccontextmanager
    async def uow(self, session: Any = None) -> Any:
        yield self


def _runner(config: Any, persistence: Any = None) -> ApplicationRunner:
    return ApplicationRunner(
        config, registry=SessionRegistry(), persistence=persistence
    )


async def test_the_runner_reads_the_row_and_decodes_it_for_the_hook(
    test_config: Any,
) -> None:
    """The hello and the emitter have no injected session, so the runner --
    which is what owns `persistence` -- opens one and hands the values over.
    Decoded leniently, like the route's read: a key the Struct retired must
    not take the badge down on every handshake."""

    class Account(msgspec.Struct):
        unseen: int = 0

    test_config.code.account = Account
    persistence = _FakePersistence({"unseen": 7, "retired": "x"})

    account = await _runner(test_config, persistence).stored_account(_User("ada"))

    assert account == Account(unseen=7)
    assert persistence.users.asked == ["ada"]


async def test_the_runner_answers_the_defaults_when_there_is_no_store(
    test_config: Any,
) -> None:
    """The same answer the route builds for an app with no data layer."""

    class Account(msgspec.Struct):
        unseen: int = 0

    test_config.code.account = Account

    assert await _runner(test_config).stored_account(_User("ada")) == Account()


async def test_without_a_registered_account_the_hook_is_handed_none(
    test_config: Any,
) -> None:
    """No `@cl.account` is no page, and there is nothing to hand over. The
    frame still goes out: the hook is what decides whether there is a number."""
    persistence = _FakePersistence({"unseen": 7})

    assert await _runner(test_config, persistence).stored_account(_User("ada")) is None
    assert persistence.users.asked == [], (
        "the row was read with nothing to decode it as"
    )


async def test_an_anonymous_session_is_not_looked_up(test_config: Any) -> None:
    """These values are stored per identifier; a session with no user has
    nobody to read them for."""

    class Account(msgspec.Struct):
        unseen: int = 0

    test_config.code.account = Account
    persistence = _FakePersistence({"unseen": 7})

    assert await _runner(test_config, persistence).stored_account(None) == Account()
    assert persistence.users.asked == []


def test_a_one_argument_badge_hook_is_refused_at_registration(
    test_config: Any,
) -> None:
    """The retired signature, caught where the application is read rather
    than as a 500 on the first page load."""
    with pytest.raises(TypeError, match=r"takes \(user, account\)"):

        @cl.on_account_badge  # type: ignore[arg-type]
        async def badge(user):  # pragma: no cover - never registered
            return 0

    assert test_config.code.on_account_badge is None


async def test_no_badge_hook_costs_no_read_on_hello(test_config: Any) -> None:
    """The values are gathered to be handed *to* the hook, so "no hook, no
    frame" has to become "no hook, nothing at all" before the argument is
    evaluated -- or every hello of an app with an account page and no badge
    buys a query for a frame nobody sends."""

    class Account(msgspec.Struct):
        unseen: int = 0

    test_config.code.account = Account
    persistence = _FakePersistence({"unseen": 7})
    session = _Session("s0", "ada")

    await _runner(test_config, persistence)._push_account_badge(session)  # type: ignore[arg-type]

    assert persistence.users.asked == []
    assert session.sent == []


async def test_no_badge_hook_costs_no_read_in_the_emitter(test_config: Any) -> None:
    """`refresh_account_badge()` is a call an application may make without
    ever registering a count."""

    class Account(msgspec.Struct):
        unseen: int = 0

    test_config.code.account = Account
    registry, (chat,) = _registry_with(("t1", "ada"))
    reads: List[str] = []

    class _Runner:
        async def stored_account(self, user: Any) -> Any:
            reads.append("read")
            return Account()

    runner = _Runner()
    runner.registry = registry  # type: ignore[attr-defined]
    chat.runner = runner  # type: ignore[attr-defined]

    await Emitter(chat).refresh_account_badge()  # type: ignore[arg-type]

    assert reads == []
    assert chat.sent == []
