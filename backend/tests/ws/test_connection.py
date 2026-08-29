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

from chainlit.protocol.codec import MAX_FRAME_BYTES, CloseCode
from chainlit.protocol.server import Heartbeat
from chainlit.ws.connection import Connection, make_websocket_handler
from chainlit.ws.registry import SessionRegistry
from chainlit.ws.session import Session


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
        make_session=lambda sid, hello_frame, u: Session(
            id=sid,
            thread_id=hello_frame.thread_id,
            chat_profile=hello_frame.chat_profile,
            client_type=hello_frame.client_type,
            user=u,
        ),
        heartbeat_ms=heartbeat_ms,
        on_disconnect=on_disconnect,
    )
    middleware = [_PutUser(user)] if user is not None else []
    return handler, middleware, registry


def hello(**overrides: Any) -> str:
    frame: Dict[str, Any] = {"t": "hello", "sessionId": "s1"}
    frame.update(overrides)
    return json.dumps(frame)


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
        ws.send_text(hello(threadId="t1"))
        ready = json.loads(ws.receive_text(timeout=5))

    assert ready["t"] == "session.ready"
    assert ready["sessionId"] == "s1"
    # On every branch, not only the first interaction: a reload into a
    # session that already had one used to come back with no thread id, and
    # the feedback buttons stayed dead for the rest of the conversation.
    assert ready["threadId"] == "t1"
    assert registry.get("s1") is not None


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


def test_a_session_belonging_to_another_user_is_refused() -> None:
    registry = SessionRegistry()
    registry.register(Session(id="s1"), user_identifier="ada", thread_id="t1")
    handler, middleware, _ = build(registry, user=_Identity("grace"))

    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_text(hello(threadId="t1"))
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive(timeout=5)

    assert excinfo.value.code == CloseCode.SESSION_FORBIDDEN
    # And nothing else: "that session exists but is not yours" says it exists.
    assert registry.get("s1") is not None


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

    entry = registry.get("s1")
    assert entry is not None, "the session did not outlive its socket"
    assert entry.connected is False


def test_the_session_outlives_the_socket_with_its_queue_intact() -> None:
    """A dead socket is not a closed queue: the conversation is still there."""
    handler, middleware, registry = build()
    with create_test_client(route_handlers=[handler], middleware=middleware) as client:
        with client.websocket_connect("/ws") as ws:
            open_session(ws)

    entry = registry.get("s1")
    assert entry is not None
    session = entry.session
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
        entry = registry.get("s1")
        assert entry is not None
        entry.session.send(Heartbeat(seq=99))  # queued while disconnected
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

                entry = registry.get("s1")
                assert entry is not None
                assert entry.connected is True
                entry.session.send(Heartbeat(seq=7))
                seqs = [f.get("seq") for f in _read(second, "hb")]
                assert 7 in seqs

    entry = registry.get("s1")
    assert entry is not None
    assert entry.connected is False


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
        entry = registry.get("s1")
        assert entry is not None
        session = entry.session
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


async def wait_for_session(
    registry: SessionRegistry, *, timeout: float = 5.0
) -> Session:
    """The session the hand-rolled peer's ``hello`` opened, once it exists."""
    deadline = time.monotonic() + timeout
    while registry.get("s1") is None and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    entry = registry.get("s1")
    assert entry is not None, "the peer's hello never opened a session"
    session = entry.session
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
    the real runner: ``on_chat_end``, then the reaper -- so a session with
    work in flight, a client attached and a question on screen was torn
    down ``session_timeout`` later, and nothing ever set ``connected``
    back. The old goodbye also came *before* the arriving client's
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
                entry = registry.get("s1")
                assert entry is not None
                assert entry.connected is True

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
                assert registry.get("s1").connected is True  # type: ignore[union-attr]
                session.send(Heartbeat(seq=4243))
                assert 4243 in [f.get("seq") for f in await read_live(second, "hb")]

            session.current_task.cancel()
        finally:
            pump.cancel()
            writer.close()

    assert disconnected == ["s1"], "the last socket to go owns the teardown"


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
                entry = registry.get("s1")
                assert entry is not None
                assert entry.connected is True
                await second.send(json.dumps({"t": "no.such.tag"}))
                assert (await read_live(second, "error"))[-1]["code"] == "unknown_tag"
        finally:
            writer.close()
