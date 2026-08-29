"""The bridge with a database behind it: a socket, the plugin, PostgreSQL.

``tests/test_runner.py`` pins the seam between the socket and
``config.code`` with no persistence at all. These tests put the real
``Persistence`` behind the same plugin and pin what reaches the rows: the
thread that comes into being on the first message, the resume that reads
it back, the profile handoff that links two threads, the blob that goes to
storage, and the state a disconnect writes down.

Everything the test *reads* from the database goes through a throwaway
engine under ``asyncio.run`` -- the application's own engine lives on the
test client's loop and asyncpg connections are bound to the loop they were
opened on.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

import pytest
from litestar.testing import create_test_client
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import chainlit as cl
from chainlit.persistence import Persistence
from chainlit.persistence.models import SCHEMA_NAME
from chainlit.persistence.records import ThreadDetail
from chainlit.plugin import ChainlitPlugin
from chainlit.security import ChainlitAuth, chainlit_auth
from tests.persistence.conftest import (  # noqa: F401 - fixture re-export
    TABLE_NAMES,
    database_url,
)
from tests.test_runner import (  # noqa: F401 - fixture re-export
    frontend_dir,
    open_session,
    read_until,
)

pytestmark = pytest.mark.usefixtures("test_config")

SECRET = "s" * 32
ALICE = "alice"
BOB = "bob"

T = TypeVar("T")

# A 1x1 PNG, the smallest blob that is unmistakably an image.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c636000010000050001a5f645400000000049454e44ae426082"
)


def message(text: str, *, id: Optional[str] = None) -> str:
    """``test_runner.message`` with an id the database accepts: a UUID."""
    return json.dumps(
        {
            "t": "message.send",
            "message": {
                "id": id or str(uuid.uuid4()),
                "type": "user_message",
                "output": text,
                "name": "User",
                "createdAt": "2026-08-28T00:00:00.000000Z",
            },
        }
    )


# ----------------------------------------------------------------- database


async def _truncate(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        qualified = ", ".join(f'"{SCHEMA_NAME}".{name}' for name in TABLE_NAMES)
        async with engine.connect() as connection:
            await connection.execute(
                text(f"TRUNCATE {qualified} RESTART IDENTITY CASCADE")
            )
            await connection.commit()
    finally:
        await engine.dispose()


async def _with_db(url: str, fn: Callable[[Persistence], Awaitable[T]]) -> T:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        return await fn(Persistence.from_engine(engine))
    finally:
        await engine.dispose()


def db(url: str, fn: Callable[[Persistence], Awaitable[T]]) -> T:
    """Run one read or write against the database on a throwaway engine."""
    return asyncio.run(_with_db(url, fn))


def seed_user(url: str, identifier: str) -> str:
    async def save(persistence: Persistence) -> str:
        async with persistence.uow() as unit:
            return (await unit.users.save(identifier)).id

    return db(url, save)


def thread_detail(url: str, thread_id: str) -> Optional[ThreadDetail]:
    async def read(persistence: Persistence) -> Optional[ThreadDetail]:
        async with persistence.uow() as unit:
            return await unit.threads.get_detail(thread_id)

    return db(url, read)


def thread_ids(url: str) -> List[str]:
    async def read(persistence: Persistence) -> List[str]:
        async with persistence.uow() as unit:
            rows = await unit.session.execute(
                text(f'SELECT id FROM "{SCHEMA_NAME}".threads')
            )
            return [str(row[0]) for row in rows]

    return db(url, read)


def wait_for_thread(
    url: str,
    thread_id: str,
    ready: Callable[[ThreadDetail], bool],
    *,
    timeout: float = 5.0,
) -> ThreadDetail:
    """Poll until the thread row satisfies ``ready``; the writer is async."""
    deadline = time.monotonic() + timeout
    detail = thread_detail(url, thread_id)
    while time.monotonic() < deadline:
        if detail is not None and ready(detail):
            return detail
        time.sleep(0.05)
        detail = thread_detail(url, thread_id)
    raise AssertionError(f"thread {thread_id} never became ready: {detail!r}")


def settle(seconds: float = 0.3) -> None:
    """Give the writer a moment to commit whatever it would commit."""
    time.sleep(seconds)


def wait_until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition never became true")


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def db_url(database_url: str) -> str:  # noqa: F811 - the imported fixture
    """The migrated database, emptied for this test."""
    asyncio.run(_truncate(database_url))
    return database_url


@pytest.fixture
def auth() -> ChainlitAuth:
    return chainlit_auth(SECRET)


class FakeStorage:
    """A storage client that remembers what it was given."""

    def __init__(self) -> None:
        self.uploads: List[Dict[str, Any]] = []

    async def upload_file(
        self,
        object_key: str,
        data: Any,
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.uploads.append({"object_key": object_key, "data": data, "mime": mime})
        return {"url": f"https://bucket.test/{object_key}", "object_key": object_key}

    async def delete_file(self, object_key: str) -> bool:
        return True

    async def get_read_url(self, object_key: str) -> str:
        return f"https://bucket.test/{object_key}"

    async def close(self) -> None:
        return None


@pytest.fixture
def make_plugin(
    test_config: Any,
    frontend_dir: Path,  # noqa: F811 - the imported fixture
    auth: ChainlitAuth,
    db_url: str,
) -> Callable[..., ChainlitPlugin]:
    def build(storage: Any = None) -> ChainlitPlugin:
        return ChainlitPlugin(
            test_config,
            persistence=Persistence.from_url(db_url, storage=storage),
            frontend_dir=frontend_dir,
            auth=auth,
        )

    return build


@pytest.fixture
def plugin(make_plugin: Callable[..., ChainlitPlugin]) -> ChainlitPlugin:
    return make_plugin()


def login(client: Any, auth: ChainlitAuth, identifier: str) -> None:
    client.cookies.set(auth.key, auth.create_token(identifier))


def tags(frames: List[dict]) -> List[str]:
    return [frame["t"] for frame in frames]


def first(frames: List[dict], tag: str) -> dict:
    for frame in frames:
        if frame["t"] == tag:
            return frame
    raise AssertionError(f"no {tag!r} in {tags(frames)}")


def send_and_read_reply(ws: Any, text_: str, **kw: Any) -> List[dict]:
    """Send a message and read up to the assistant's reply."""
    ws.send_text(message(text_, **kw))
    frames = read_until(ws, "step.upsert")
    while frames[-1]["step"].get("type") == "user_message":
        frames.extend(read_until(ws, "step.upsert"))
    return frames


# ------------------------------------------------------- 1. first interaction


def test_the_first_interaction_persists_the_thread(
    plugin: ChainlitPlugin, test_config: Any, auth: ChainlitAuth, db_url: str
) -> None:
    seed_user(db_url, ALICE)
    test_config.project.persist_user_env = True

    async def on_message(msg: cl.Message) -> None:
        cl.user_session.set("counter", 7)
        await cl.Message(content=f"echo: {msg.content}").send()

    test_config.code.on_message = on_message

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws, chatProfile="A", userEnv={"KEY": "v"})
            frames = send_and_read_reply(ws, "first words")
        thread_id = handshake[0]["threadId"]
        assert first(frames, "thread.first_interaction")["threadId"] == thread_id

        # The disconnect patch lands after the socket closes; the writer
        # commits on its own schedule.
        detail = wait_for_thread(
            db_url,
            thread_id,
            lambda d: "counter" in (d.metadata or {}) and len(d.steps) == 2,
        )

    assert detail.name == "first words"
    assert detail.user_identifier == ALICE
    assert detail.user_id is not None
    assert sorted(step.type for step in detail.steps) == [
        "assistant_message",
        "user_message",
    ]
    assert {step.output for step in detail.steps} == {
        "first words",
        "echo: first words",
    }
    metadata = detail.metadata or {}
    assert metadata["chat_profile"] == "A"
    assert metadata["client_type"] == "webapp"
    assert metadata["env"] == {"KEY": "v"}
    assert metadata["counter"] == 7


# ------------------------------------------------------------------ 2. resume


def test_a_resume_replays_the_thread_then_runs_the_hooks_in_order(
    plugin: ChainlitPlugin, test_config: Any, auth: ChainlitAuth, db_url: str
) -> None:
    seed_user(db_url, ALICE)
    hooks: List[tuple] = []

    async def on_message(msg: cl.Message) -> None:
        cl.user_session.set("counter", 7)
        await cl.Message(content=f"echo: {msg.content}").send()

    async def on_chat_resume(thread: Dict[str, Any]) -> None:
        hooks.append(("resume", thread["id"], cl.user_session.get("counter")))
        await cl.Message(content="welcome back").send()

    async def on_thread_ready(thread: Dict[str, Any]) -> None:
        hooks.append(("ready", thread["id"], cl.user_session.get("counter")))

    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = on_chat_resume
    test_config.code.on_thread_ready = on_thread_ready

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            send_and_read_reply(ws, "first words")
        thread_id = handshake[0]["threadId"]
        wait_for_thread(db_url, thread_id, lambda d: "counter" in (d.metadata or {}))

        with client.websocket_connect("/ws") as ws:
            replay = open_session(ws, sessionId="s2", threadId=thread_id)
            greeting = read_until(ws, "step.upsert")
            wait_until(lambda: len(hooks) == 2)
            send_and_read_reply(ws, "second words")
        # Two from the first session, the greeting, and two more.
        detail = wait_for_thread(db_url, thread_id, lambda d: len(d.steps) == 5)

    assert replay[0]["t"] == "session.ready"
    assert replay[0]["threadId"] == thread_id
    assert replay[0]["sessionId"] == "s2"
    assert first(replay, "thread.first_interaction")["interaction"] == "resume"
    # The stored thread arrives as one snapshot inside the handshake, before
    # the hooks have said a word -- a resume replaces the client's feed.
    [snapshot] = [f for f in replay if f["t"] == "thread.resume"]
    assert snapshot["thread"]["id"] == thread_id
    replayed = [step["output"] for step in snapshot["thread"]["steps"]]
    assert replayed == ["first words", "echo: first words"]
    assert not [f for f in replay if f["t"] == "step.upsert"]
    assert greeting[-1]["step"]["output"] == "welcome back"

    assert hooks == [("resume", thread_id, 7), ("ready", thread_id, 7)]

    # The resumed session writes into the same thread, not a new one.
    assert thread_ids(db_url) == [thread_id]
    assert [s.output for s in detail.steps] == [
        "first words",
        "echo: first words",
        "welcome back",
        "second words",
        "echo: second words",
    ]


# ---------------------------------------------------- 3. foreign thread resume


def test_a_foreign_thread_is_not_replayed_to_another_user(
    plugin: ChainlitPlugin, test_config: Any, auth: ChainlitAuth, db_url: str
) -> None:
    seed_user(db_url, ALICE)
    seed_user(db_url, BOB)
    hooks: List[str] = []

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo: {msg.content}").send()

    async def on_chat_resume(thread: Dict[str, Any]) -> None:
        hooks.append("resume")

    async def on_thread_ready(thread: Dict[str, Any]) -> None:
        hooks.append("ready")

    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = on_chat_resume
    test_config.code.on_thread_ready = on_thread_ready

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            send_and_read_reply(ws, "alice's secret")
        thread_id = handshake[0]["threadId"]
        wait_for_thread(db_url, thread_id, lambda d: len(d.steps) == 2)

        login(client, auth, BOB)
        with client.websocket_connect("/ws") as ws:
            foreign = open_session(ws, sessionId="s-bob", threadId=thread_id)
            settle()

    assert hooks == []
    assert "thread.first_interaction" not in tags(foreign)
    assert [f for f in foreign if f["t"] == "step.upsert"] == [], tags(foreign)


def test_a_foreign_thread_is_not_written_into_by_another_user(
    plugin: ChainlitPlugin, test_config: Any, auth: ChainlitAuth, db_url: str
) -> None:
    seed_user(db_url, ALICE)
    seed_user(db_url, BOB)

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo: {msg.content}").send()

    async def on_chat_resume(thread: Dict[str, Any]) -> None:
        pass

    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = on_chat_resume

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            send_and_read_reply(ws, "alice's secret")
        thread_id = handshake[0]["threadId"]
        wait_for_thread(db_url, thread_id, lambda d: len(d.steps) == 2)

        login(client, auth, BOB)
        with client.websocket_connect("/ws") as ws:
            foreign = open_session(ws, sessionId="s-bob", threadId=thread_id)
            frames = send_and_read_reply(ws, "bob was here")
        bob_thread = first(frames, "thread.first_interaction")["threadId"]
        # Bob got a thread of his own ...
        assert foreign[0]["threadId"] != thread_id
        assert bob_thread != thread_id
        wait_for_thread(db_url, bob_thread, lambda d: len(d.steps) == 2)
        settle()

    # ... and Alice's is untouched.
    alice = thread_detail(db_url, thread_id)
    assert alice is not None
    assert alice.name == "alice's secret"
    assert alice.user_identifier == ALICE
    assert {s.output for s in alice.steps} == {"alice's secret", "echo: alice's secret"}


# --------------------------------------------------------- 4. profile handoff


def _handoff_app(test_config: Any, starts: List[Any], *, switch_on: str) -> None:
    """Profile A hands over to B, from ``on_chat_start`` or ``on_message``."""

    async def switch() -> None:
        await cl.context.emitter.set_chat_profile("B", transit_message={"k": 1})

    async def on_chat_start() -> None:
        session = cl.context.session
        # Profile A reads its state directly: ``cl.user_session.get`` has a
        # persistence bug of its own (pinned by
        # ``test_user_session_get_after_a_message_still_persists_the_thread``)
        # that would stop A's thread from being named, and this test is
        # about the handoff. The successor reads through the public API.
        transit = (
            cl.user_session.get("transit_message")
            if session.chat_profile == "B"
            else session.state.get("transit_message")
        )
        starts.append((session.id, session.chat_profile, transit))
        if session.chat_profile == "A" and switch_on == "start":
            await switch()

    async def on_message(msg: cl.Message) -> None:
        if cl.context.session.chat_profile == "A" and switch_on == "message":
            await switch()
            return
        await cl.Message(content=f"echo: {msg.content}").send()

    test_config.code.on_chat_start = on_chat_start
    test_config.code.on_message = on_message


def test_a_handoff_before_any_interaction_opens_an_orphan_thread(
    plugin: ChainlitPlugin, test_config: Any, auth: ChainlitAuth, db_url: str
) -> None:
    seed_user(db_url, ALICE)
    starts: List[Any] = []
    _handoff_app(test_config, starts, switch_on="start")

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            open_session(ws, chatProfile="A")
            handoff = read_until(ws, "session.handoff")[-1]
        next_id = handoff["nextSessionId"]
        assert next_id
        assert next_id != "s1"
        assert handoff["hasTransitMessage"] is True
        assert handoff["chatProfile"] == "B"

        with client.websocket_connect("/ws") as ws:
            successor = open_session(ws, sessionId=next_id, chatProfile="B")
            wait_until(lambda: len(starts) == 2)
        new_thread = successor[0]["threadId"]
        detail = wait_for_thread(db_url, new_thread, lambda d: d.name is not None)

        # The record is consumed: a third hello on the same id gets nothing.
        with client.websocket_connect("/ws") as ws:
            third = open_session(ws, sessionId=next_id, chatProfile="B")
            wait_until(lambda: len(starts) == 3)

    assert starts[0][1:] == ("A", None)
    assert starts[1] == (next_id, "B", {"k": 1})
    assert starts[2] == (next_id, "B", None)
    assert "thread.first_interaction" not in tags(third)
    assert "thread.parent" not in tags(successor)

    interaction = first(successor, "thread.first_interaction")
    assert interaction["interaction"] == "B"
    assert interaction["threadId"] == new_thread
    assert detail.name == "B"
    assert detail.user_identifier == ALICE
    # The first session never interacted, so there is no parent to link.
    assert detail.parent_thread_id is None
    assert "transit_message" not in (detail.metadata or {})
    assert thread_ids(db_url) == [new_thread]


def test_a_handoff_after_an_interaction_links_the_successor_to_its_parent(
    plugin: ChainlitPlugin, test_config: Any, auth: ChainlitAuth, db_url: str
) -> None:
    seed_user(db_url, ALICE)
    starts: List[Any] = []
    _handoff_app(test_config, starts, switch_on="message")

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws, chatProfile="A")
            ws.send_text(message("switch me"))
            handoff = read_until(ws, "session.handoff")[-1]
        parent = handshake[0]["threadId"]
        next_id = handoff["nextSessionId"]
        wait_for_thread(db_url, parent, lambda d: d.name == "switch me")

        with client.websocket_connect("/ws") as ws:
            successor = open_session(ws, sessionId=next_id, chatProfile="B")
            wait_until(lambda: len(starts) == 2)
            send_and_read_reply(ws, "in B")
        new_thread = successor[0]["threadId"]
        detail = wait_for_thread(db_url, new_thread, lambda d: len(d.steps) == 2)

    assert new_thread != parent
    assert starts[1] == (next_id, "B", {"k": 1})
    assert first(successor, "thread.parent")["parentThreadId"] == parent
    assert first(successor, "thread.first_interaction")["interaction"] == "B"
    assert detail.parent_thread_id == parent
    assert detail.name == "B"
    assert "transit_message" not in (detail.metadata or {})
    assert sorted(thread_ids(db_url)) == sorted([parent, new_thread])


# ------------------------------------------------------------- 5. blob upload


def _image_app(test_config: Any, png: Path) -> None:
    async def on_message(msg: cl.Message) -> None:
        image = cl.Image(path=str(png), name="pic", display="inline")
        await cl.Message(content="here", elements=[image]).send()

    test_config.code.on_message = on_message


def test_an_element_blob_goes_to_storage_and_the_row_points_at_it(
    make_plugin: Callable[..., ChainlitPlugin],
    test_config: Any,
    auth: ChainlitAuth,
    db_url: str,
    tmp_path: Path,
) -> None:
    seed_user(db_url, ALICE)
    png = tmp_path / "pic.png"
    png.write_bytes(PNG)
    storage = FakeStorage()
    plugin = make_plugin(storage=storage)
    _image_app(test_config, png)

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            frames = send_and_read_reply(ws, "show me")
            frames.extend(read_until(ws, "element.upsert"))
        thread_id = handshake[0]["threadId"]
        detail = wait_for_thread(db_url, thread_id, lambda d: len(d.elements) == 1)

    element = first(frames, "element.upsert")["element"]
    row = detail.elements[0]
    assert len(storage.uploads) == 1
    assert storage.uploads[0]["data"] == PNG
    assert storage.uploads[0]["mime"] == "image/png"
    assert storage.uploads[0]["object_key"] == f"{ALICE}/{row.id}/pic"
    assert row.name == "pic"
    assert row.object_key == f"{ALICE}/{row.id}/pic"
    assert row.url == f"https://bucket.test/{ALICE}/{row.id}/pic"
    assert row.thread_id == thread_id
    # The frame goes out before the upload: it carries the session's spool
    # key, never the storage URL.
    assert element["id"] == row.id
    assert element["chainlitKey"] == row.chainlit_key
    assert element.get("url") is None


def test_without_storage_the_row_has_no_url_and_the_spool_serves_the_key(
    plugin: ChainlitPlugin,
    test_config: Any,
    auth: ChainlitAuth,
    db_url: str,
    tmp_path: Path,
) -> None:
    seed_user(db_url, ALICE)
    png = tmp_path / "pic.png"
    png.write_bytes(PNG)
    _image_app(test_config, png)

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            frames = send_and_read_reply(ws, "show me")
            frames.extend(read_until(ws, "element.upsert"))
            thread_id = handshake[0]["threadId"]
            detail = wait_for_thread(db_url, thread_id, lambda d: len(d.elements) == 1)
            element = first(frames, "element.upsert")["element"]
            served = client.get(
                f"/project/file/{element['chainlitKey']}", params={"session_id": "s1"}
            )

    row = detail.elements[0]
    assert row.url is None
    assert row.object_key is None
    assert row.chainlit_key == element["chainlitKey"]
    assert element["chainlitKey"]
    assert served.status_code == 200
    assert served.content == PNG


# ------------------------------------------- 1b. user_session.get and the row


def test_user_session_get_after_a_message_still_persists_the_thread(
    plugin: ChainlitPlugin, test_config: Any, auth: ChainlitAuth, db_url: str
) -> None:
    seed_user(db_url, ALICE)

    async def on_chat_start() -> None:
        # Reading anything is enough: ``get`` writes ``user`` into the state.
        cl.user_session.get("chat_profile")

    async def on_message(msg: cl.Message) -> None:
        cl.user_session.set("counter", 7)
        await cl.Message(content=f"echo: {msg.content}").send()

    test_config.code.on_chat_start = on_chat_start
    test_config.code.on_message = on_message

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws, chatProfile="A")
            send_and_read_reply(ws, "first words")
        thread_id = handshake[0]["threadId"]
        detail = wait_for_thread(
            db_url,
            thread_id,
            lambda d: d.name is not None and "counter" in (d.metadata or {}),
        )

    assert detail.name == "first words"
    assert detail.user_identifier == ALICE
    assert (detail.metadata or {})["counter"] == 7


# ------------------------------------------------- 6. disconnect and reconnect


def test_a_disconnect_persists_state_and_a_reconnect_keeps_the_writer(
    plugin: ChainlitPlugin, test_config: Any, auth: ChainlitAuth, db_url: str
) -> None:
    seed_user(db_url, ALICE)
    plugin.runner.session_timeout = 5

    async def on_message(msg: cl.Message) -> None:
        cl.user_session.set("last", msg.content)
        await cl.Message(content=f"echo: {msg.content}").send()

    test_config.code.on_message = on_message

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            send_and_read_reply(ws, "one")
        thread_id = handshake[0]["threadId"]
        entry = plugin.runner.registry.get("s1")
        assert entry is not None
        writer = entry.session.writer  # type: ignore[attr-defined]
        assert writer is not None

        wait_for_thread(
            db_url, thread_id, lambda d: (d.metadata or {}).get("last") == "one"
        )

        with client.websocket_connect("/ws") as ws:
            frames = open_session(ws, pageLoad=False)
            assert first(frames, "session.ready")["restored"] is True
            assert frames[0]["threadId"] == thread_id
            entry = plugin.runner.registry.get("s1")
            assert entry is not None
            assert entry.session.writer is writer  # type: ignore[attr-defined]
            send_and_read_reply(ws, "two")
        detail = wait_for_thread(
            db_url, thread_id, lambda d: (d.metadata or {}).get("last") == "two"
        )

    assert thread_ids(db_url) == [thread_id]
    assert {s.output for s in detail.steps} == {"one", "echo: one", "two", "echo: two"}


def test_an_offer_after_a_resume_leaves_the_composer_open(
    plugin: ChainlitPlugin, test_config: Any, auth: ChainlitAuth, db_url: str
) -> None:
    """on_thread_ready parks on a question for hours; the user must still type.

    The spinner locks the composer, and it is derived from the session's
    tasks. A resume runs on_chat_resume in one task and on_thread_ready in
    another; when the first ends it resyncs the spinner -- and the second,
    waiting on an offer, is alive. Alive is not busy: a task waiting on the
    user must leave the spinner dark, or every resumed chat is locked for
    as long as the offer stands.
    """
    seed_user(db_url, ALICE)

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo: {msg.content}").send()

    async def on_chat_resume(thread: Dict[str, Any]) -> None:
        pass

    async def on_thread_ready(thread: Dict[str, Any]) -> None:
        await cl.AskActionMessage(
            content="continue?",
            actions=[cl.Action(name="yes", payload={})],
            timeout=3600,
        ).send()

    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = on_chat_resume
    test_config.code.on_thread_ready = on_thread_ready

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            send_and_read_reply(ws, "first words")
        thread_id = handshake[0]["threadId"]
        wait_for_thread(db_url, thread_id, lambda d: len(d.steps) == 2)

        with client.websocket_connect("/ws") as ws:
            open_session(ws, sessionId="s2", threadId=thread_id)
            read_until(ws, "ask.start")
            after: List[dict] = []
            for _ in range(6):
                try:
                    after.append(json.loads(ws.receive_text(timeout=0.4)))
                except Exception:
                    break

    spinner = [f["running"] for f in after if f["t"] == "task.indicator"]
    assert True not in spinner, after


def test_a_reconnect_of_a_resumed_session_does_not_start_the_chat_again(
    plugin: ChainlitPlugin, test_config: Any, auth: ChainlitAuth, db_url: str
) -> None:
    """A resumed session reconnecting is still the resumed session.

    The client rebuilds its transport when the resume tells it the thread's
    profile, and the new socket arrives as ``kept``. The resume branch does
    not run again -- correctly -- but the chat had never been marked as
    started, so ``on_ready`` ran ``on_chat_start`` over the restored
    conversation and a fresh wizard appeared under it.
    """
    seed_user(db_url, ALICE)
    started = 0

    async def on_chat_start() -> None:
        nonlocal started
        started += 1

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo: {msg.content}").send()

    async def on_chat_resume(thread: Dict[str, Any]) -> None:
        pass

    test_config.code.on_chat_start = on_chat_start
    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = on_chat_resume

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            send_and_read_reply(ws, "first words")
        thread_id = handshake[0]["threadId"]
        wait_for_thread(db_url, thread_id, lambda d: len(d.steps) == 2)
        assert started == 1

        with client.websocket_connect("/ws") as ws:
            frames = open_session(ws, sessionId="s2", threadId=thread_id)
            assert not first(frames, "session.ready").get("restored")
            assert "thread.resume" in [f["t"] for f in frames]
        with client.websocket_connect("/ws") as ws:
            frames = open_session(ws, sessionId="s2", pageLoad=False)
            assert first(frames, "session.ready")["restored"] is True
            time.sleep(0.3)

    assert started == 1


def test_a_missing_thread_is_reported_after_the_ready_frame(
    plugin: ChainlitPlugin, test_config: Any, auth: ChainlitAuth, db_url: str
) -> None:
    """A resume of a thread that is not there is told, not papered over.

    The client parks on a loader until the thread it asked for becomes
    current; a server that quietly starts a fresh chat under that id leaves
    it there for good. ``thread_not_found`` is the frame it acts on -- and
    it must follow ``session.ready``, like everything else.
    """
    seed_user(db_url, ALICE)

    async def on_message(msg: cl.Message) -> None:
        pass

    async def on_chat_resume(thread: Dict[str, Any]) -> None:
        pass

    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = on_chat_resume

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            frames = open_session(ws, threadId=str(uuid.uuid4()))
            frames += read_until(ws, "error")

    assert frames[0]["t"] == "session.ready"
    assert first(frames, "error")["code"] == "thread_not_found"
