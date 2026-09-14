"""The route, and the ways two loops on one socket go wrong.

These cover the connection's own behaviour: what the first frame has to
be, which failures close and which merely report, and that an ordinary
disconnect is not an error. The conversation-level behaviour the handshake
performs is stated in ``tests/socketspec`` and covered directly in
``test_handshake.py``.

The takeover cases at the bottom run against a real uvicorn, and they have
to. In memory, ``close`` is a queue write: it never awaits a closing
handshake, so the superseded handler always unwinds in the one order that
happens to be harmless, and the test that used to cover the takeover was
green against code that reaped live sessions on every profile change.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple

import pytest
import uvicorn
from litestar import Litestar
from litestar.enums import ScopeType
from litestar.exceptions import WebSocketDisconnect
from litestar.middleware import ASGIMiddleware
from litestar.testing import create_test_client
from litestar.types import ASGIApp, Receive, Scope, Send
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

import chainlit as cl
from chainlit.plugin import ChainlitPlugin
from chainlit.protocol.codec import MAX_FRAME_BYTES, CloseCode
from chainlit.protocol.server import Heartbeat
from chainlit.runner import ApplicationRunner
from chainlit.security import ChainlitAuth
from chainlit.ws.connection import Connection, make_websocket_handler
from chainlit.ws.registry import SessionEntry, SessionRegistry
from chainlit.ws.session import Session
from tests.persistence.conftest import database_url  # noqa: F401 - fixture re-export
from tests.test_runner import frontend_dir  # noqa: F401 - fixture re-export
from tests.test_runner_persistence import (  # noqa: F401 - fixture re-export
    ALICE,
    auth,
    db_url,
    make_plugin,
    message as user_message,
    seed_user,
    thread_detail,
    wait_for_thread,
)


class _Identity:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier


class _PutUser(ASGIMiddleware):
    """Leave a user in the scope, the way an auth middleware does.

    Its scopes include the websocket one, which is the whole reason the
    real ``JWTCookieAuth`` works on an upgrade: a browser cannot put an
    Authorization header on one, and a cookie it can.
    """

    scopes = (ScopeType.WEBSOCKET,)

    def __init__(self, user: Any) -> None:
        self.user = user

    async def handle(
        self, scope: Scope, receive: Receive, send: Send, next_app: ASGIApp
    ) -> None:
        scope["user"] = self.user
        await next_app(scope, receive, send)


def build(
    registry: Optional[SessionRegistry] = None,
    *,
    user: Optional[_Identity] = None,
    heartbeat_ms: int = 20_000,
    on_arrival: Optional[Callable[[Any], Awaitable[None]]] = None,
    on_disconnect: Optional[Callable[[Session], Awaitable[None]]] = None,
) -> Any:
    """The route and its registry, plus whatever middleware the case needs.

    Authentication is not exercised here on purpose: it runs before
    ``accept()``, so a refusal from it is a failed upgrade rather than a
    close code, and none of the cases below is about that.
    """
    registry = registry if registry is not None else SessionRegistry()
    handler = make_websocket_handler(
        registry=registry,
        make_session=lambda thread_id, hello_frame, u: Session(
            # What the runner's factory does: the handle is minted here
            # because nothing on the wire offers one, and so is the thread
            # when the client named none.
            id=str(uuid.uuid4()),
            thread_id=thread_id or str(uuid.uuid4()),
            chat_profile=hello_frame.chat_profile,
            client_type=hello_frame.client_type,
            user=u,
        ),
        heartbeat_ms=heartbeat_ms,
        on_arrival=on_arrival,
        on_disconnect=on_disconnect,
    )
    middleware = [_PutUser(user)] if user is not None else []
    return handler, middleware, registry


THREAD = "t1"
"""The conversation every case here is about.

The only identity on this wire: a client names the thread in its address
bar, and the session it gets is whatever is in that thread. The handle in
``session.ready`` is minted per session and is never offered back.
"""


def hello(**overrides: Any) -> str:
    """The opening frame. ``threadId=None`` is a first visit: name nothing."""
    frame: Dict[str, Any] = {"t": "hello", "threadId": THREAD}
    frame.update(overrides)
    if frame.get("threadId") is None:
        frame.pop("threadId", None)
    return json.dumps(frame)


def held(registry: SessionRegistry, thread_id: str = THREAD) -> SessionEntry:
    entry = registry.entry_of_thread(thread_id)
    assert entry is not None, f"nobody is in thread {thread_id}"
    return entry


def live(entry: SessionEntry) -> Session:
    """The registry holds a view; a test that sends needs the session."""
    assert isinstance(entry.session, Session)
    return entry.session


def close_code_of(ws: Any, *, limit: int = 200, timeout: float = 5.0) -> int:
    """Read frames until the socket closes, and return the code it closed on."""
    for _ in range(limit):
        try:
            ws.receive(timeout=timeout)
        except WebSocketDisconnect as disconnect:
            return disconnect.code
    raise AssertionError("the connection never closed")


def open_session(ws: Any, *, timeout: float = 5.0) -> List[str]:
    """Say hello and read the whole handshake, returning its tags.

    The replay runs *concurrently* with the reader -- that is what lets an
    answer typed before a reload arrive during it -- so frames keep coming
    after ``session.ready``. It always ends with the spinner, because a
    level-triggered boolean is only honest once everything else has been
    said, and that is what makes this loop terminate.
    """
    ws.send_text(hello())
    tags: List[str] = []
    for _ in range(50):
        tags.append(json.loads(ws.receive_text(timeout=timeout))["t"])
        if tags[-1] == "task.indicator":
            return tags
    raise AssertionError(f"the handshake never finished: {tags}")


# ------------------------------------------------------------- the opening


def test_a_hello_opens_the_session() -> None:
    handler, middleware, registry = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_text(hello())
        ready = json.loads(ws.receive_text(timeout=5))

    assert ready["t"] == "session.ready"
    # Minted here, and this frame is the only place the client learns it:
    # the handle is what an upload or an action button is addressed to.
    assert ready["sessionId"]
    # On every branch, not only the first interaction: a reload into a
    # session that already had one used to come back with no thread id, and
    # the feedback buttons stayed dead for the rest of the conversation.
    assert ready["threadId"] == THREAD
    assert held(registry).id == ready["sessionId"]


def test_a_first_frame_that_is_not_hello_closes_the_connection() -> None:
    """There is no session yet to report an error against."""
    handler, middleware, _ = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_text(json.dumps({"t": "stop"}))
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive(timeout=5)

    assert excinfo.value.code == CloseCode.BAD_HANDSHAKE


def test_a_malformed_first_frame_closes_the_connection() -> None:
    handler, middleware, _ = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_text("{not json")
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive(timeout=5)

    assert excinfo.value.code == CloseCode.BAD_HANDSHAKE


def test_a_conversation_belonging_to_another_user_is_answered_with_a_fresh_one() -> (
    None
):
    """No refusal exists. There is no close code left that could carry one.

    Closing with "that session is not yours" said that it exists, and the
    id in a URL is a bearer token in everything but name. The stranger gets
    a conversation of their own and hears nothing about this one -- the
    same answer a thread that never existed gets.
    """
    registry = SessionRegistry()
    theirs = Session(id="s1", thread_id=THREAD)
    registry.register(theirs, thread_id=THREAD, user_identifier="ada")
    handler, middleware, _ = build(registry, user=_Identity("grace"))

    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_text(hello())
        ready = json.loads(ws.receive_text(timeout=5))

    assert ready["t"] == "session.ready"
    assert ready["threadId"] != THREAD, ready
    # And the owner is undisturbed: still registered, still in her thread.
    assert held(registry).session is theirs


# --------------------------------------------------------- once it is open


def test_an_unknown_tag_is_reported_and_the_socket_stays_open() -> None:
    """A frame this release does not understand is not the user's problem.

    The error is addressed to the client's next version; taking away the
    conversation to deliver it would be a strange way to say so.
    """
    handler, middleware, _ = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        assert open_session(ws)[0] == "session.ready"
        ws.send_text(json.dumps({"t": "no.such.tag"}))
        error = json.loads(ws.receive_text(timeout=5))
        # Still usable afterwards.
        ws.send_text(json.dumps({"t": "hb.ack", "seq": 1}))

    assert error["t"] == "error"
    assert error["code"] == "unknown_tag"


def test_a_malformed_frame_is_reported_and_the_socket_stays_open() -> None:
    handler, middleware, _ = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        assert open_session(ws)[0] == "session.ready"
        ws.send_text("{not json")
        error = json.loads(ws.receive_text(timeout=5))

    assert error["t"] == "error"
    assert error["code"] == "bad_message"


def test_an_oversized_inbound_frame_closes_the_connection() -> None:
    """Reported rather than truncated: half a message is not a message."""
    handler, middleware, _ = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        assert open_session(ws)[0] == "session.ready"
        ws.send_text(json.dumps({"t": "stop", "pad": "x" * (MAX_FRAME_BYTES + 16)}))
        code = close_code_of(ws)

    assert code == CloseCode.FRAME_TOO_LARGE


def test_closing_a_tab_is_not_an_internal_server_error() -> None:
    """The ordinary case.

    A reader exception escaping the task group would be reported to the
    user as a 4500, and anyio wraps even one child exception in a group --
    so the naive ``except WebSocketDisconnect`` never fires and this is the
    test that notices.
    """
    handler, middleware, registry = build()
    with create_test_client(route_handlers=[handler], middleware=middleware) as client:
        with client.websocket_connect("/ws") as ws:
            assert open_session(ws)[0] == "session.ready"

    entry = held(registry)
    assert entry.connected is False, "the session did not outlive its socket"


def test_the_session_outlives_the_socket_with_its_queue_intact() -> None:
    """A dead socket is not a closed queue: the conversation is still there."""
    handler, middleware, registry = build()
    with create_test_client(route_handlers=[handler], middleware=middleware) as client:
        with client.websocket_connect("/ws") as ws:
            open_session(ws)

    session = held(registry).session
    assert isinstance(session, Session)
    assert session.outbound.closed is False


def test_a_socket_that_stops_answering_the_probe_is_closed() -> None:
    """A silent socket is indistinguishable from a healthy one.

    Nothing is written to a session parked on a question, so without a
    probe it can sit against a peer that vanished hours ago -- holding its
    place in the registry, and shielding its own steps from a resume that
    should have reclaimed them.
    """
    handler, middleware, _ = build(heartbeat_ms=40)
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        open_session(ws)
        code = close_code_of(ws)

    assert code == CloseCode.HEARTBEAT_TIMEOUT


def test_answering_the_probe_keeps_the_connection() -> None:
    handler, middleware, _ = build(heartbeat_ms=40)
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        open_session(ws)
        for _ in range(4):
            frame = json.loads(ws.receive_text(timeout=5))
            assert frame["t"] == "hb", frame
            ws.send_text(json.dumps({"t": "hb.ack", "seq": frame.get("seq", 0)}))


def test_a_kept_sessions_backlog_follows_the_ready_frame() -> None:
    """What the last socket never took is delivered -- after ``session.ready``.

    A session kept across a gap may hold frames produced while nobody was
    listening. They are a continuation, not the opening: the client starts
    the conversation on ``session.ready`` and would otherwise see them as
    noise before it, or -- for a level frame like the spinner -- as a stale
    truth ahead of the real one.
    """
    handler, middleware, registry = build()
    with create_test_client(route_handlers=[handler], middleware=middleware) as client:
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
        live(held(registry)).send(Heartbeat(seq=99))  # queued while disconnected
        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(pageLoad=False))
            tags = [json.loads(ws.receive_text(timeout=5))["t"] for _ in range(2)]

    assert tags[0] == "session.ready"
    assert "hb" in tags


def _read(ws: Any, tag: str, *, limit: int = 20, timeout: float = 5.0) -> List[dict]:
    """Frames up to and including the first one tagged ``tag``."""
    frames: List[dict] = []
    for _ in range(limit):
        frame = json.loads(ws.receive_text(timeout=timeout))
        frames.append(frame)
        if frame["t"] == tag:
            return frames
    raise AssertionError(f"never saw {tag!r}: {[f['t'] for f in frames]}")


def test_a_newer_socket_takes_a_kept_session_over() -> None:
    """The client rebuilds its transport without waiting for the old close.

    So the new socket can arrive while the previous handler is still
    reading. It has to take the session over -- writer and all -- and the
    old handler's teardown has to notice it was superseded: detaching there
    took the writer out from under the new socket, and its ``session.ready``
    had already gone to the old one, so the client waited forever.
    """
    handler, middleware, registry = build()
    with create_test_client(route_handlers=[handler], middleware=middleware) as client:
        with client.websocket_connect("/ws") as first:
            assert open_session(first)[0] == "session.ready"
            with client.websocket_connect("/ws") as second:
                second.send_text(hello(pageLoad=False))
                ready = json.loads(second.receive_text(timeout=5))
                assert ready["t"] == "session.ready"
                assert ready["restored"] is True
                # The old socket is closed by the takeover, terminally.
                assert close_code_of(first) == CloseCode.SUPERSEDED

                entry = held(registry)
                assert entry.connected is True
                live(entry).send(Heartbeat(seq=7))
                seqs = [f.get("seq") for f in _read(second, "hb")]
                assert 7 in seqs

    assert held(registry).connected is False


def test_a_session_whose_socket_timed_out_takes_a_new_one() -> None:
    """A probe that expires ends the socket, and only the socket.

    The heartbeat closes a silent peer and the client's recovery is to
    reconnect. It used to do that by aborting the *queue*, which is closed
    for good -- so the session could never take another writer and refused
    exactly that reconnect, and the handshake had to hand it a whole new
    queue to get around it, dropping everything the old socket never took.
    """
    handler, middleware, registry = build(heartbeat_ms=40)
    with create_test_client(route_handlers=[handler], middleware=middleware) as client:
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
            assert close_code_of(ws) == CloseCode.HEARTBEAT_TIMEOUT
        session = held(registry).session
        assert isinstance(session, Session)
        assert session.outbound.closed is False
        # Queued against nobody, and still owed: this is what the queue
        # surviving the socket is worth.
        session.send(Heartbeat(seq=99))

        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(pageLoad=False))
            ready = json.loads(ws.receive_text(timeout=5))
            assert ready["t"] == "session.ready"
            assert ready["restored"] is True
            assert 99 in [frame.get("seq") for frame in _read(ws, "hb")]


def test_a_release_outlives_the_connection_that_asked_for_it(
    test_config: Any,
) -> None:
    """A dying socket must not take the teardown down with it.

    ``session.clear`` is read by ``_read_loop``, which is a child of
    ``_serve``'s task group. Awaited there, the whole release ran inside a
    scope any sibling could cancel -- and the heartbeat is a sibling that
    cancels on a peer that stops answering, which is precisely the peer that
    just said "New chat" and detached. The end-of-chat hook died halfway,
    the metadata patch was never queued, the writer's drain was cut off with
    rows still in it, and ``discard_files`` never ran.

    So the release is a task of the runner's, and the only thing the reader
    still does in the same breath is give the thread up.
    """
    ending: List[str] = []

    async def on_chat_end() -> None:
        # Comfortably longer than the two heartbeat intervals below, so the
        # cancellation lands in the middle of it rather than around it.
        await asyncio.sleep(0.4)
        ending.append("ran to the end")

    test_config.code.on_chat_end = on_chat_end

    registry = SessionRegistry()
    runner = ApplicationRunner(test_config, registry=registry)
    handler = make_websocket_handler(
        registry=registry,
        make_session=runner.make_session,
        on_arrival=runner.on_arrival,
        on_ready=runner.on_ready,
        on_disconnect=runner.on_disconnect,
        heartbeat_ms=40,
    )

    with create_test_client(route_handlers=[handler]) as client:
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
            session = live(held(registry))
            ws.send_text(json.dumps({"t": "session.clear"}))
            # The thread is free in the same breath, before anything awaits.
            deadline = time.monotonic() + 2
            while registry.entry_of_thread(THREAD) is not None:
                assert time.monotonic() < deadline, "the thread was never given up"
                time.sleep(0.01)
            # And nothing here answers a probe, so the heartbeat closes this
            # socket and cancels its group while the release is still going.
            assert close_code_of(ws) == CloseCode.HEARTBEAT_TIMEOUT
            assert ending == [], "the premise: the release is not finished yet"

        deadline = time.monotonic() + 3
        while not ending:
            assert time.monotonic() < deadline, "the release died with the socket"
            time.sleep(0.02)
        # And it got all the way to the end, not merely past the hook.
        while not session.outbound.closed:
            assert time.monotonic() < deadline, "the teardown never finished"
            time.sleep(0.02)


# --------------------------------------------------------------------------
# Live uvicorn: the takeover
# --------------------------------------------------------------------------

WS_IMPLEMENTATIONS = ("websockets", "websockets-sansio")
"""Both, because the takeover behaves differently on each.

``websockets-sansio`` is what ``--ws auto`` resolves to on uvicorn 0.52, so
it is what this server actually runs; its ``websocket.close`` writes the
close frame and queues the disconnect in one go. The older ``websockets``
implementation *awaits* the closing handshake -- up to ``close_timeout``
against a peer that stopped answering -- and the superseded handler unwinds
inside that await. That is the ordering the old teardown got wrong, and it
is only reachable here.
"""


@asynccontextmanager
async def live_server(app: Litestar, *, ws: str) -> AsyncIterator[int]:
    """Serve ``app`` on a loopback port for the body of the block."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", ws=ws)
    server = uvicorn.Server(config)
    serving = asyncio.ensure_future(server.serve())
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            if serving.done():  # pragma: no cover - surfaces a startup failure
                await serving
            if time.monotonic() > deadline:  # pragma: no cover
                raise TimeoutError("uvicorn did not start")
            await asyncio.sleep(0.02)
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 30)


async def read_live(
    sock: ClientConnection, tag: str, *, limit: int = 60, timeout: float = 10.0
) -> List[Dict[str, Any]]:
    """Frames up to and including the first one tagged ``tag``."""
    frames: List[Dict[str, Any]] = []
    for _ in range(limit):
        frames.append(json.loads(await asyncio.wait_for(sock.recv(), timeout)))
        if frames[-1]["t"] == tag:
            return frames
    raise AssertionError(f"never saw {tag!r}: {[f['t'] for f in frames]}")


async def open_live(sock: ClientConnection, **overrides: Any) -> List[Dict[str, Any]]:
    """Say hello and read the whole handshake."""
    await sock.send(hello(**overrides))
    return await read_live(sock, "task.indicator")


async def live_close_code(
    sock: ClientConnection, *, limit: int = 60, timeout: float = 10.0
) -> int:
    """Read until the server closes this socket, and return the code."""
    try:
        for _ in range(limit):
            await asyncio.wait_for(sock.recv(), timeout)
    except ConnectionClosed as closed:
        return closed.rcvd.code if closed.rcvd is not None else 1006
    raise AssertionError("the connection never closed")


async def wait_for_session(
    registry: SessionRegistry, *, thread_id: str = THREAD, timeout: float = 5.0
) -> Session:
    """The session the hand-rolled peer's ``hello`` opened, once it exists."""
    deadline = time.monotonic() + timeout
    while registry.entry_of_thread(thread_id) is None:
        assert time.monotonic() < deadline, "the peer's hello never opened a session"
        await asyncio.sleep(0.01)
    session = held(registry, thread_id).session
    assert isinstance(session, Session)
    return session


async def hand_rolled_peer(port: int, frame: str) -> Tuple[Any, asyncio.StreamWriter]:
    """Upgrade by hand, say one thing, and leave the rest to the caller.

    Not a client library, because every one of them is helpful in exactly
    the ways these two cases forbid: it drains the socket in the
    background, echoes a close frame the moment the server sends one, and
    hangs up straight afterwards. Real browsers on real networks do none of
    those reliably, and the connection lifecycle is judged on what happens
    when they do not.
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    key = base64.b64encode(os.urandom(16)).decode()
    writer.write(
        f"GET /ws HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    await writer.drain()
    status = await asyncio.wait_for(reader.readline(), 20)
    assert status.startswith(b"HTTP/1.1 101"), status
    while True:
        line = await asyncio.wait_for(reader.readline(), 20)
        if line in (b"\r\n", b""):
            break
    writer.write(_masked_text(frame))
    await writer.drain()
    return reader, writer


FROZEN_TAB_LINGER = 2.0
"""How long the frozen tab below holds its TCP connection open after
answering the goodbye. Long enough that a handshake which waits for that
goodbye is unmistakably slower than one that does not, short enough that
the case costs a couple of seconds rather than ``close_timeout``."""


async def frozen_tab(
    port: int, frame: str, *, linger: float = FROZEN_TAB_LINGER
) -> Tuple["asyncio.Future[int]", asyncio.StreamWriter, "asyncio.Task[None]"]:
    """A peer that answers the goodbye and then does not hang up.

    What a tab whose JavaScript has stopped looks like from here: the
    kernel still completes the closing handshake, and nothing closes the
    socket. ``websockets`` waits ``close_timeout`` for the TCP close that
    never comes, so the server's own ``close`` does not return for ten
    seconds -- and anything the takeover does *after* it is ten seconds
    late to the client that is waiting.
    """
    reader, writer = await hand_rolled_peer(port, frame)
    closed: "asyncio.Future[int]" = asyncio.get_running_loop().create_future()

    async def pump() -> None:
        try:
            while True:
                opcode, payload = await _read_frame(reader)
                if opcode != 0x8:
                    continue
                writer.write(_masked_close())
                await writer.drain()
                if not closed.done():
                    code = int.from_bytes(payload[:2], "big") if payload else 1005
                    closed.set_result(code)
                await asyncio.sleep(linger)
                writer.close()
                return
        except asyncio.IncompleteReadError, ConnectionResetError, OSError:
            if not closed.done():
                closed.set_result(1006)

    return closed, writer, asyncio.create_task(pump())


async def _read_frame(reader: Any) -> Tuple[int, bytes]:
    """One unmasked server-to-client frame: its opcode and its payload."""
    head = await reader.readexactly(2)
    length = head[1] & 0x7F
    if length == 126:
        length = int.from_bytes(await reader.readexactly(2), "big")
    elif length == 127:
        length = int.from_bytes(await reader.readexactly(8), "big")
    return head[0] & 0x0F, await reader.readexactly(length)


def _masked_text(payload: str) -> bytes:
    """One client-to-server text frame, masked as RFC 6455 requires."""
    return _masked(0x81, payload.encode())


def _masked_close(code: int = 1000) -> bytes:
    return _masked(0x88, code.to_bytes(2, "big"))


def _masked(opcode: int, body: bytes) -> bytes:
    mask = os.urandom(4)
    header = bytearray([opcode])
    if len(body) < 126:
        header.append(0x80 | len(body))
    else:
        header.append(0x80 | 126)
        header += len(body).to_bytes(2, "big")
    return bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(body))


@pytest.mark.parametrize("ws_impl", WS_IMPLEMENTATIONS)
async def test_live_a_takeover_leaves_the_session_connected(ws_impl: str) -> None:
    """The bug this rebuild is for, and the only transport that shows it.

    The client rebuilds its transport without waiting for the old socket to
    close -- a profile change does exactly that -- so the second socket
    arrives while the first handler is still reading. The first peer here
    is a frozen tab: it completes the closing handshake and then never
    hangs up, so on ``websockets`` the goodbye does not return for
    ``close_timeout``, and the superseded handler wakes up and runs its
    teardown well inside that window.

    Everything that teardown used to do was aimed at the wrong connection.
    It marked the session disconnected and called ``on_disconnect`` -- in
    the real runner: the reaper -- so a session with work in flight, a
    client attached and a question on screen was torn down
    ``session_timeout`` later, and nothing ever set ``connected`` back. The old goodbye also came *before* the arriving client's
    ``session.ready``, which is the same ten seconds of nothing seen from
    the browser. In memory neither is reachable: ``close`` there is a queue
    write that awaits nobody.
    """
    disconnected: List[str] = []

    async def on_disconnect(session: Session) -> None:
        disconnected.append(session.id)

    handler, _middleware, registry = build(on_disconnect=on_disconnect)

    async with live_server(Litestar([handler]), ws=ws_impl) as port:
        goodbye, writer, pump = await frozen_tab(port, hello())
        try:
            session = await wait_for_session(registry)
            # Work in flight: this is the session that must not be reaped.
            session.current_task = asyncio.create_task(asyncio.sleep(30))

            async with connect(f"ws://127.0.0.1:{port}/ws") as second:
                started = time.monotonic()
                ready = (await open_live(second, pageLoad=False))[0]
                handshake = time.monotonic() - started
                assert ready["t"] == "session.ready"
                assert ready["restored"] is True
                # Not held behind the goodbye. The old takeover closed the
                # previous socket before it queued anything for this one, so
                # a peer that answers the close and then stops existing put
                # its own ``close_timeout`` between a reloading client and
                # the first frame it is waiting for.
                assert handshake < FROZEN_TAB_LINGER / 2

                assert await asyncio.wait_for(goodbye, 10) == CloseCode.SUPERSEDED
                # The superseded handler's teardown runs somewhere in here.
                await asyncio.sleep(0.3)

                # ``on_disconnect`` is what schedules the reaper, so this is
                # the assertion that the live session is not being reaped.
                assert disconnected == []
                assert session.connected is True
                assert held(registry).connected is True

                # And the session can still speak, on the socket that holds
                # it: a detach from the wrong handler took the writer out
                # from under this one and the client waited forever.
                session.send(Heartbeat(seq=4242))
                seqs = [f.get("seq") for f in await read_live(second, "hb")]
                assert 4242 in seqs

                # And once the frozen tab's connection finally does go, the
                # handler that was serving it still has nothing to say about
                # a session it no longer holds.
                await asyncio.sleep(FROZEN_TAB_LINGER + 0.3)
                assert disconnected == []
                assert held(registry).connected is True
                session.send(Heartbeat(seq=4243))
                assert 4243 in [f.get("seq") for f in await read_live(second, "hb")]

            session.current_task.cancel()
        finally:
            pump.cancel()
            writer.close()

    assert disconnected == [session.id], "the last socket to go owns the teardown"


@pytest.mark.parametrize("ws_impl", WS_IMPLEMENTATIONS)
async def test_live_a_second_tab_arriving_mid_handshake_still_wins(
    ws_impl: str,
) -> None:
    """Two hellos for one thread, overlapping, and the later one must win.

    ``arrive`` registers the session and returns; ``on_arrival`` then does
    the slow part -- claiming a handover, reading a thread back out of the
    database -- and only then does ``_take_over`` decide who speaks. A
    second tab landing inside that window used to resolve the race
    backwards: it claimed the session the first had just registered, adopted
    it, sent *its* client ``session.ready`` and began replaying, and then the
    first woke up from its own ``on_arrival`` and adopted the session back.
    The tab the user had just opened was closed 4409 "opened in another
    window", and the frames it had queued drained onto the tab it replaced.

    The rule is one arrival at a time per conversation, so the second hello
    is not answered until the first has finished being one. Live, because
    the loser is told by a close code and in-process ``close`` is a queue
    write that awaits nobody.
    """
    gate = asyncio.Event()
    arrivals: List[Any] = []

    async def slow_arrival(arrival: Any) -> None:
        arrivals.append(arrival)
        if len(arrivals) == 1:
            # The first arrival is still deciding what its hello meant.
            await gate.wait()

    handler, _middleware, registry = build(on_arrival=slow_arrival)

    async with live_server(Litestar([handler]), ws=ws_impl) as port:
        url = f"ws://127.0.0.1:{port}/ws"
        try:
            async with connect(url) as first:
                await first.send(hello(pageLoad=True))
                deadline = time.monotonic() + 5
                while not arrivals:
                    assert time.monotonic() < deadline, "the first hello never arrived"
                    await asyncio.sleep(0.01)

                async with connect(url) as second:
                    await second.send(hello(pageLoad=True))
                    # Long enough that an unserialised handshake would be
                    # well past its own ``on_arrival`` and its takeover.
                    await asyncio.sleep(0.3)
                    assert len(arrivals) == 1, (
                        "the second hello was let into the handshake while "
                        "the first was still in it"
                    )

                    gate.set()
                    replay = await open_live(second)
                    assert replay[0]["t"] == "session.ready"
                    assert replay[0]["threadId"] == THREAD
                    # The session the first arrival registered, handed over
                    # -- not a second one built on the same conversation.
                    assert replay[0]["restored"] is True, replay[0]
                    assert arrivals[1].outcome.value == "kept"

                    # The tab the user opened last is the one holding the
                    # conversation, and the one it replaced is told so.
                    assert await live_close_code(first) == CloseCode.SUPERSEDED
                    entry = held(registry)
                    assert entry.connected is True
                    live(entry).send(Heartbeat(seq=77))
                    assert 77 in [f.get("seq") for f in await read_live(second, "hb")]
        finally:
            # Or the handler parked on it outlives the assertion that failed,
            # and the server's shutdown timeout reports itself instead.
            gate.set()

    assert len(arrivals) == 2


@pytest.mark.parametrize("ws_impl", WS_IMPLEMENTATIONS)
async def test_live_a_superseded_probe_cannot_close_the_new_socket(
    ws_impl: str,
) -> None:
    """Two heartbeat loops on one session, and only one of them may act.

    The superseded handler is not always gone when its replacement starts:
    its peer may be a tab that stopped answering, in which case the goodbye
    sent to it is never acknowledged and the handler stays parked in
    ``receive_text``. Its heartbeat loop wakes on its own schedule against
    a socket it no longer owns.

    With the counter on the session there was one ``last_ack`` for two
    loops, so the stale one read the new client's answers as replies to its
    own probes -- and the moment the sequences diverged it declared the peer
    dead and aborted the queue the *live* connection was writing to. The
    fix is ownership, not arithmetic: a connection that is not current
    neither probes nor concludes, and it leaves.
    """
    # Slow enough that the takeover below reliably lands before the first
    # peer's opening probe -- while it is still current, a probe of its own
    # is correct, and the case under test would never be set up.
    interval = 0.3
    handler, _middleware, registry = build(heartbeat_ms=int(interval * 1000))

    async with live_server(Litestar([handler]), ws=ws_impl) as port:
        # Wholly deaf, this one: it answers nothing at all, so the goodbye
        # is never acknowledged and the handler holding it stays parked.
        _reader, writer = await hand_rolled_peer(port, hello())
        try:
            session = await wait_for_session(registry)
            superseded = session.current
            assert isinstance(superseded, Connection)

            async with connect(f"ws://127.0.0.1:{port}/ws") as second:
                ready = (await open_live(second, pageLoad=False))[0]
                assert ready["restored"] is True
                assert superseded.current is False

                # Four intervals of answering honestly. The stale loop wakes
                # twice in that time; on the shared counter that was enough
                # to time this connection out.
                current = session.current
                assert isinstance(current, Connection)
                until = time.monotonic() + 4 * interval
                while time.monotonic() < until:
                    frame = json.loads(await asyncio.wait_for(second.recv(), 10))
                    if frame["t"] == "hb":
                        await second.send(
                            json.dumps({"t": "hb.ack", "seq": frame["seq"]})
                        )

                # The socket the stale loop would have killed is still here,
                # still being served, and still the session's.
                assert current.seq > 0, "the live connection never probed"
                assert superseded.seq == 0, "the superseded connection probed"
                assert session.connected is True
                assert held(registry).connected is True
                await second.send(json.dumps({"t": "no.such.tag"}))
                assert (await read_live(second, "error"))[-1]["code"] == "unknown_tag"
        finally:
            writer.close()


@pytest.mark.parametrize("ws_impl", WS_IMPLEMENTATIONS)
async def test_live_a_reload_gets_its_question_back(ws_impl: str) -> None:
    """F5 with the id the tab stored, over a socket that really closed.

    In memory the first socket's close is a queue write and the session is
    never actually left alone; here the browser's connection goes and the
    handler unwinds for real before the reload's ``hello`` is read -- the
    order the client fix (``state.ts`` restoring the thread with the id) is
    there to produce. What comes back is the whole turn from the session's
    own transcript, the question's buttons, and the question itself with
    what is left of its deadline rather than a fresh one.
    """
    handler, _middleware, registry = build()

    async with live_server(Litestar([handler]), ws=ws_impl) as port:
        async with connect(f"ws://127.0.0.1:{port}/ws") as first:
            await open_live(first, pageLoad=True, threadId="t1")
            session = await wait_for_session(registry)
            _stage_open_question(session)

        # The socket is gone and the handler has unwound: the session is
        # alone with its question, which is the state a reload arrives in.
        await asyncio.sleep(0.2)
        assert session.connected is False

        async with connect(f"ws://127.0.0.1:{port}/ws") as second:
            # The reload's hello: the id the tab stored, the thread it was
            # in, and ``pageLoad`` -- the browser is holding nothing.
            await second.send(hello(pageLoad=True, threadId="t1"))
            replay = await read_live(second, "ask.start")

    assert replay[0]["t"] == "session.ready"
    assert replay[0]["restored"] is True
    outputs = [f["step"].get("output") for f in replay if f["t"] == "step.upsert"]
    assert outputs == ["the free report"], [f["t"] for f in replay]
    assert [f["element"]["name"] for f in replay if f["t"] == "element.upsert"] == [
        "report"
    ]
    assert [f["action"]["name"] for f in replay if f["t"] == "action.add"] == ["yes"]
    ask = replay[-1]
    assert ask["spec"]["stepId"] == "ask-1"
    # The deadline is what is left of the original, never a fresh 600.
    assert 0 < ask["spec"]["timeout"] < 600


def _stage_open_question(session: Session) -> None:
    """Put a turn and an open action question on a bare session.

    There is no application behind this handler -- ``build`` makes sessions
    by hand -- so the state a turn would have left is stated directly. It is
    the same three things ``restore`` reads: what was said, the form's
    furniture, and the ask itself.
    """
    from chainlit.protocol.payloads import (
        Action,
        AskActionSpec,
        PdfElement,
        Step as StepPayload,
    )
    from chainlit.ws.session import PendingAsk, TranscriptEntry

    report = StepPayload(id="m1", type="assistant_message", output="the free report")
    session.transcript.append(
        TranscriptEntry(
            step=report,
            elements=[PdfElement(id="e1", name="report", for_id="m1")],
        )
    )
    session.pending_ask = PendingAsk(
        step_id="ask-1",
        step=StepPayload(id="ask-1", type="assistant_message", output="Buy?"),
        spec=AskActionSpec(step_id="ask-1", timeout=600),
        future=asyncio.get_running_loop().create_future(),
        deadline=time.monotonic() + 600,
        restore_actions=[Action(id="a1", name="yes", for_id="ask-1")],
    )


# --------------------------------------------------------------------------
# Live uvicorn: the reload that keeps its thread
# --------------------------------------------------------------------------


async def _alone(
    plugin: ChainlitPlugin, thread_id: str, *, timeout: float = 5.0
) -> None:
    """Poll until the session in this thread has lost its socket.

    A poll rather than ``wait_until``: the server is running in *this*
    loop, so a ``time.sleep`` here would stop the thing being waited for.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entry = plugin.runner.registry.entry_of_thread(thread_id)
        if entry is not None and not entry.connected:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"the session in thread {thread_id!r} never went quiet")


@pytest.mark.parametrize("ws_impl", WS_IMPLEMENTATIONS)
async def test_live_a_reload_on_a_greeting_keeps_the_session_whole(
    ws_impl: str,
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """F5 on a greeting, over a socket that really closed.

    The in-process twin never closes anything: ``close`` there is a queue
    write, so the first session is still half-alive when the reload is
    read. Here the connection goes, the handler unwinds, and the reload
    arrives at a session that is genuinely alone -- through real cookie
    auth on the upgrade, under both ws implementations.

    And it gets that session back, whole: same thread, same writer, the
    greeting replayed from the transcript rather than said again. The chat
    has not ended, so it is not begun a second time -- the behaviour change
    this release is named for, pinned where the socket is real.

    The whole application is behind this handler, not the bare route the
    cases above use: without a runner there is no ``on_arrival``, and the
    hook that must not run twice would not run at all.
    """
    plugin = make_plugin()
    started: List[Optional[str]] = []

    async def on_chat_start() -> None:
        started.append(cl.context.session.thread_id)
        await cl.Message(content="hello there").send()

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo {msg.content}").send()

    async def on_chat_resume(thread: Dict[str, Any]) -> None:
        return None

    test_config.code.on_chat_start = on_chat_start
    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = on_chat_resume

    await asyncio.to_thread(seed_user, db_url, ALICE)
    # A cookie, because that is the only way a browser authenticates an
    # upgrade: there is no header to put a bearer token on.
    cookie = {"Cookie": f"{auth.key}={auth.create_token(ALICE)}"}

    async with live_server(Litestar(plugins=[plugin]), ws=ws_impl) as port:
        url = f"ws://127.0.0.1:{port}/ws"
        async with connect(url, additional_headers=cookie) as first:
            # A first visit: nothing in the address bar, so the server
            # mints the conversation and names it back.
            opening = await open_live(first, pageLoad=True, threadId=None)
            thread_id = opening[0]["threadId"]
            opening += await read_live(first, "step.upsert")
            assert [
                f["step"].get("output") for f in opening if f["t"] == "step.upsert"
            ] == ["hello there"]

        await _alone(plugin, thread_id)
        assert await asyncio.to_thread(thread_detail, db_url, thread_id) is None, (
            "the premise: greeted, never spoken in, so nowhere in the database"
        )

        async with connect(url, additional_headers=cookie) as second:
            replay = await open_live(second, pageLoad=True, threadId=thread_id)
            ready = replay[0]
            assert ready["t"] == "session.ready"
            assert ready["restored"] is True, "the session was thrown away"
            assert ready["threadId"] == thread_id, ready
            assert [
                f["step"].get("output") for f in replay if f["t"] == "step.upsert"
            ] == ["hello there"], "the greeting did not come back from the transcript"

            await second.send(user_message("hi again"))
            replay += await read_live(second, "thread.first_interaction")

    assert [f for f in replay if f["t"] == "error"] == [], [f["t"] for f in replay]
    announced = [f for f in replay if f["t"] == "thread.first_interaction"]
    assert announced[0]["threadId"] == thread_id, announced
    assert started == [thread_id], "the chat was begun a second time"

    # The session's own writer, never replaced: nothing was torn down here,
    # and the row carrying the echo has to be under the thread the tab has
    # been looking at all along. Read after the server is gone, so a row
    # that is only there because a drain ran on shutdown still counts.
    detail = await asyncio.to_thread(
        wait_for_thread,
        db_url,
        thread_id,
        lambda d: "echo hi again" in [step.output for step in d.steps],
    )
    assert detail.id == thread_id


# --------------------------------------------------------------------------
# Live uvicorn: the second tab, and giving the conversation up
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ws_impl", WS_IMPLEMENTATIONS)
async def test_live_a_second_tab_on_an_idle_thread_takes_it_over(
    ws_impl: str,
) -> None:
    """Two tabs on one URL is a takeover, and the first one loses.

    The decision behind this release: a conversation lives in one tab. A
    duplicated tab, a ``target=_blank``, a link pasted into a second window
    -- all of them name the thread the first tab is in, and all of them get
    the session that is in it. The first is closed 4409, which the client
    treats as terminal and does not reconnect on, so the takeover is not a
    tug of war between two tabs each reconnecting over the other.

    Driven without a reconnect on purpose: a client library that came back
    would hide exactly the loop this behaviour has to not have.
    """
    handler, _middleware, registry = build()

    async with live_server(Litestar([handler]), ws=ws_impl) as port:
        url = f"ws://127.0.0.1:{port}/ws"
        async with connect(url) as first:
            opening = await open_live(first, pageLoad=True)
            assert opening[0]["threadId"] == THREAD
            first_handle = opening[0]["sessionId"]

            async with connect(url) as second:
                replay = await open_live(second, pageLoad=True)
                assert replay[0]["t"] == "session.ready"
                assert replay[0]["restored"] is True
                # The same session, so the same handle: uploads and action
                # buttons rendered in the first tab still address it.
                assert replay[0]["sessionId"] == first_handle
                assert replay[0]["threadId"] == THREAD

                # The first tab is told, terminally, and does not come back.
                assert await live_close_code(first) == CloseCode.SUPERSEDED

                await asyncio.sleep(0.3)
                # And the conversation belongs to the second: the first
                # handler unwinding must not mark it disconnected or take
                # the writer out from under the tab that is using it.
                entry = held(registry)
                assert entry.connected is True
                assert entry.id == first_handle
                live(entry).send(Heartbeat(seq=11))
                assert 11 in [f.get("seq") for f in await read_live(second, "hb")]

    assert held(registry).connected is False


@pytest.mark.parametrize("ws_impl", WS_IMPLEMENTATIONS)
async def test_live_a_second_tab_is_shown_the_question_the_first_was_asked(
    ws_impl: str,
) -> None:
    """The takeover carries the open form with it.

    The server is blocked on an answer, and the only place that answer can
    now come from is the tab that just took the conversation over. A
    takeover that handed back a session without replaying its question
    would leave the user looking at a conversation that had stopped, with
    the coroutine behind it waiting out its deadline against nobody.
    """
    handler, _middleware, registry = build()

    async with live_server(Litestar([handler]), ws=ws_impl) as port:
        url = f"ws://127.0.0.1:{port}/ws"
        async with connect(url) as first:
            await open_live(first, pageLoad=True)
            session = await wait_for_session(registry)
            _stage_open_question(session)

            async with connect(url) as second:
                await second.send(hello(pageLoad=True))
                replay = await read_live(second, "ask.start")

                assert await live_close_code(first) == CloseCode.SUPERSEDED

    assert replay[0]["restored"] is True
    assert [f["action"]["name"] for f in replay if f["t"] == "action.add"] == ["yes"]
    ask = replay[-1]
    assert ask["spec"]["stepId"] == "ask-1"
    # What is left of the deadline, not a fresh one: a form that resets its
    # timer every time a tab is duplicated never times out.
    assert 0 < ask["spec"]["timeout"] < 600
    assert session.pending_ask is not None
    assert session.pending_ask.is_live, "the takeover resolved the question"


@pytest.mark.parametrize("ws_impl", WS_IMPLEMENTATIONS)
async def test_live_new_chat_gives_the_conversation_up(
    ws_impl: str, test_config: Any
) -> None:
    """``session.clear`` ends the session, not just its work.

    Over a real socket, because the abort that answers it really closes
    this one. And over a real ``ApplicationRunner``, because giving a
    conversation up is the application half's to do -- the registry belongs
    to it, and a transport that discarded entries itself would be the layer
    below deciding what a session is.

    What matters is the state it leaves behind: the thread is free within
    the same breath, so a client that opens it again -- from the history,
    or by navigating back -- begins a session rather than being handed the
    empty screen the old one had become.
    """
    registry = SessionRegistry()
    runner = ApplicationRunner(test_config, registry=registry)
    handler = make_websocket_handler(
        registry=registry,
        make_session=runner.make_session,
        on_arrival=runner.on_arrival,
        on_ready=runner.on_ready,
        on_disconnect=runner.on_disconnect,
    )

    async with live_server(Litestar([handler]), ws=ws_impl) as port:
        url = f"ws://127.0.0.1:{port}/ws"
        async with connect(url) as first:
            handle = (await open_live(first, pageLoad=True))[0]["sessionId"]
            session = await wait_for_session(registry)
            await first.send(json.dumps({"t": "session.clear"}))
            # The server closes the socket: the real client has detached by
            # the time it sends this, so nothing is owed to this peer. 1000,
            # not 4500 -- nothing failed, the conversation was given up on
            # purpose, and a client that is somehow still listening should
            # hear the ordinary goodbye and be free to come back to a fresh
            # chat rather than be told the server broke.
            assert await live_close_code(first) == 1000

        deadline = time.monotonic() + 5
        while registry.entry_of_thread(THREAD) is not None:
            assert time.monotonic() < deadline, "the thread was never given up"
            await asyncio.sleep(0.02)
        assert session.outbound.closed is True, "the queue outlived the session"

        async with connect(url) as second:
            ready = (await open_live(second, pageLoad=True))[0]
            # A session of its own on the same address: nothing was handed
            # back, and the handle is new.
            assert not ready.get("restored"), ready
            assert ready["threadId"] == THREAD
            assert ready["sessionId"] != handle
            assert held(registry).id == ready["sessionId"]
