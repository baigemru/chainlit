"""The bridge, end to end: a socket, the plugin, and ``config.code``.

Every test here opens a real websocket against a real ``ChainlitPlugin``
and reads real frames. The application is a handful of callbacks
registered on the test config. What is pinned is the seam itself: that a
message from the composer reaches ``on_message`` as a ``cl.Message``, that
``cl.Message().send()`` inside it reaches the socket, and that the hooks
fire on the side of the handshake they belong to.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
from litestar.testing import create_test_client

import chainlit as cl
from chainlit.plugin import ChainlitPlugin
from chainlit.runner import ApplicationRunner

pytestmark = pytest.mark.usefixtures("test_config")


def hello(**overrides: Any) -> str:
    frame: Dict[str, Any] = {"t": "hello", "sessionId": "s1", "pageLoad": True}
    frame.update(overrides)
    return json.dumps(frame)


def message(text: str, *, id: str = "m1") -> str:
    return json.dumps(
        {
            "t": "message.send",
            "message": {
                "id": id,
                "type": "user_message",
                "output": text,
                "name": "User",
                "createdAt": "2026-08-28T00:00:00.000000Z",
            },
        }
    )


def read_until(
    ws: Any, tag: str, *, limit: int = 50, timeout: float = 5.0
) -> List[dict]:
    """Read frames until one carries ``tag``; return everything read."""
    frames: List[dict] = []
    for _ in range(limit):
        frame = json.loads(ws.receive_text(timeout=timeout))
        frames.append(frame)
        if frame["t"] == tag:
            return frames
    raise AssertionError(f"never saw {tag!r}: {[f['t'] for f in frames]}")


def open_session(ws: Any, **hello_overrides: Any) -> List[dict]:
    ws.send_text(hello(**hello_overrides))
    return read_until(ws, "task.indicator")


@pytest.fixture
def plugin(test_config: Any, frontend_dir: Path) -> ChainlitPlugin:
    return ChainlitPlugin(test_config, frontend_dir=frontend_dir, auth=None)


@pytest.fixture
def frontend_dir(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    return dist


def test_on_chat_start_runs_after_the_handshake_and_its_message_arrives(
    plugin: ChainlitPlugin, test_config: Any
) -> None:
    started: List[str] = []

    async def on_chat_start() -> None:
        started.append(cl.context.session.id)
        await cl.Message(content="hello there").send()

    test_config.code.on_chat_start = on_chat_start

    with (
        create_test_client(plugins=[plugin]) as client,
        client.websocket_connect("/ws") as ws,
    ):
        handshake = open_session(ws)
        greeting = read_until(ws, "step.upsert")
        after = read_until(ws, "task.indicator")

    # The greeting lands after the handshake's spinner, never inside it: the
    # hook is launched only once the screen is rebuilt.
    assert handshake[0]["t"] == "session.ready"
    assert greeting[-1]["step"]["output"] == "hello there"
    assert started == ["s1"]
    # And once the hook is done the spinner goes out -- the composer is
    # locked while it is lit, so a spinner nobody puts out is a dead chat.
    assert after[-1]["running"] is False


def test_a_message_reaches_on_message_and_the_reply_reaches_the_socket(
    plugin: ChainlitPlugin, test_config: Any
) -> None:
    received: List[Any] = []

    async def on_message(msg: cl.Message) -> None:
        received.append(msg)
        await cl.Message(content=f"echo: {msg.content}").send()

    test_config.code.on_message = on_message

    with (
        create_test_client(plugins=[plugin]) as client,
        client.websocket_connect("/ws") as ws,
    ):
        open_session(ws)
        ws.send_text(message("ping"))
        frames = read_until(ws, "step.upsert")
        # The thread comes into being on the first message, and the client
        # is told before the reply.
        assert "thread.first_interaction" in [f["t"] for f in frames], frames

    assert [m.content for m in received] == ["ping"]
    assert isinstance(received[0], cl.Message)
    assert frames[-1]["step"]["output"] == "echo: ping"


def test_a_text_ask_takes_the_next_message_as_its_answer(
    plugin: ChainlitPlugin, test_config: Any
) -> None:
    answers: List[Any] = []

    async def on_chat_start() -> None:
        answer = await cl.AskUserMessage(content="name?", timeout=5).send()
        assert answer is not None
        answers.append(answer)
        await cl.Message(content=f"hi {answer['output']}").send()

    test_config.code.on_chat_start = on_chat_start

    with (
        create_test_client(plugins=[plugin]) as client,
        client.websocket_connect("/ws") as ws,
    ):
        open_session(ws)
        read_until(ws, "ask.start")
        ws.send_text(message("Ada", id="u1"))
        frames = read_until(ws, "ask.end")
        reply = read_until(ws, "step.upsert")

    assert frames[-1]["reason"] == "answered"
    assert answers[0]["output"] == "Ada"
    assert reply[-1]["step"]["output"] == "hi Ada"


def test_stop_cancels_the_running_task_and_calls_on_stop(
    plugin: ChainlitPlugin, test_config: Any
) -> None:
    stopped: List[bool] = []
    cancelled: List[bool] = []

    running: List[bool] = []

    async def on_message(msg: cl.Message) -> None:
        running.append(True)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def on_stop() -> None:
        stopped.append(True)

    test_config.code.on_message = on_message
    test_config.code.on_stop = on_stop

    with (
        create_test_client(plugins=[plugin]) as client,
        client.websocket_connect("/ws") as ws,
    ):
        open_session(ws)
        ws.send_text(message("go"))
        read_until(ws, "task.indicator")
        # Stop only once the callback is the thing running: a stop that
        # lands earlier cancels the message before it is handed over, which
        # is also right, but not what this test is about.
        for _ in range(250):
            if running:
                break
            time.sleep(0.02)
        ws.send_text(json.dumps({"t": "stop"}))
        # The cancelled task's own spinner update, then the "stopped"
        # message, then the spinner after on_stop has had its say.
        read_until(ws, "step.upsert")
        # Two spinner updates follow -- the cancelled task's and on_stop's --
        # in whichever order the loop schedules them. The hook is what the
        # test is about, so it is waited for by name.
        for _ in range(250):
            if stopped:
                break
            time.sleep(0.02)
        frames = read_until(ws, "task.indicator")

    assert stopped == [True]
    assert cancelled == [True]
    assert frames[-1]["running"] is False


def test_a_closed_socket_runs_on_chat_end_and_schedules_the_reaper(
    plugin: ChainlitPlugin, test_config: Any
) -> None:
    ended: List[str] = []

    async def on_chat_end() -> None:
        ended.append(cl.context.session.id)

    async def on_message(msg: cl.Message) -> None:
        pass

    test_config.code.on_message = on_message
    test_config.code.on_chat_end = on_chat_end
    plugin.runner.session_timeout = 0.05

    with create_test_client(plugins=[plugin]) as client:
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
        # The session survives the socket for the grace period ...
        assert plugin.runner.registry.get("s1") is not None
        for _ in range(250):
            if ended:
                break
            time.sleep(0.02)
        assert ended == ["s1"]
        # ... and not beyond it.
        deadline = 50
        while plugin.runner.registry.get("s1") is not None and deadline:
            time.sleep(0.02)
            deadline -= 1

    assert plugin.runner.registry.get("s1") is None


def test_a_reconnect_within_the_grace_period_keeps_the_session(
    plugin: ChainlitPlugin, test_config: Any
) -> None:
    starts: List[int] = []

    async def on_chat_start() -> None:
        starts.append(1)
        cl.user_session.set("counter", 42)

    test_config.code.on_chat_start = on_chat_start
    plugin.runner.session_timeout = 5

    with create_test_client(plugins=[plugin]) as client:
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
        with client.websocket_connect("/ws") as ws:
            frames = open_session(ws, pageLoad=False)

    # Not necessarily the first frame: a session kept across the gap may
    # still hold frames the last socket never took, and the queue delivers
    # those first. The client rebuilds its screen after ``session.ready``
    # regardless.
    ready = next(f for f in frames if f["t"] == "session.ready")
    assert ready["restored"] is True
    assert starts == [1], "on_chat_start ran again on a reconnect"
    session = plugin.runner.registry.get("s1")
    assert session is not None
    assert session.session.state["counter"] == 42  # type: ignore[attr-defined]


def test_the_runner_is_the_sessions_runner(
    plugin: ChainlitPlugin, test_config: Any
) -> None:
    async def on_message(msg: cl.Message) -> None:
        pass

    test_config.code.on_message = on_message
    with (
        create_test_client(plugins=[plugin]) as client,
        client.websocket_connect("/ws") as ws,
    ):
        open_session(ws)
        entry = plugin.runner.registry.get("s1")

    assert entry is not None
    assert isinstance(entry.session.runner, ApplicationRunner)  # type: ignore[attr-defined]
