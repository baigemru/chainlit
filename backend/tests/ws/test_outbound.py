"""The outbound queue: order, the backlog bound, the fence, and the close.

Three kinds of test live here and they are not interchangeable.

*In-process, through Litestar's test client.* Real handler, real
``WebSocket``, real close frames -- so a close code is asserted the way the
browser sees it. What this transport cannot show is backpressure: its
``send`` is a ``Queue.put`` that never blocks and has no frame limit.

*Against a stub socket.* Deadlines and socket loss are properties of the
queue, not of the transport, and a stub is the only way to hold a writer
inside ``send_text`` for as long as an assertion needs.

*Against a live uvicorn.* One real slow consumer and one real hang-up,
because the two behaviours those cases are about -- a closed receive window
and a peer that vanishes mid-frame -- do not exist in memory.

Two framework facts are pinned here as well
(``test_disconnect_arrives_from_a_task_group_as_an_exception_group``,
``test_exception_handlers_are_never_consulted_in_the_websocket_scope``).
They are not tests of this module; they are the reasons its close discipline
is shaped the way it is, and Litestar's own documented websocket example
gets the first one wrong.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import queue as queue_mod
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import anyio
import pytest
import uvicorn
from litestar import Litestar, WebSocket, websocket
from litestar.exceptions import WebSocketDisconnect
from litestar.testing import create_test_client

from chainlit.protocol.codec import MAX_FRAME_BYTES, CloseCode
from chainlit.protocol.server import StepStreamToken, Toast
from chainlit.ws.outbound import DEFAULT_MAX_BACKLOG, Outbound, Overflow

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class StubSocket:
    """The two methods the writer uses, and control over when they finish.

    ``Outbound`` only ever calls ``send_text`` and ``close``, which is why it
    types its socket for the checker and duck-types it at run time: a socket
    that can be held open in the middle of a frame is not something a real
    transport offers.
    """

    def __init__(self, *, block: bool = False, yields: bool = False) -> None:
        self.frames: List[str] = []
        self.closed: Optional[Tuple[int, str]] = None
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.fail_with: Optional[BaseException] = None
        self._block = block
        self._yields = yields

    async def send_text(self, data: Any, encoding: str = "utf-8") -> None:
        self.entered.set()
        if self.fail_with is not None:
            raise self.fail_with
        if self._block:
            await self.release.wait()
        if self._yields:
            # One frame per scheduling round, so a producer can outrun it --
            # a socket with no suspension point at all is not a socket.
            await asyncio.sleep(0)
        self.frames.append(data.decode(encoding) if isinstance(data, bytes) else data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


def token(step_id: str, value: str) -> StepStreamToken:
    return StepStreamToken(id=step_id, token=value)


def close_code_of(ws: Any, *, limit: int = 2000, timeout: float = 10.0) -> int:
    """Read frames until the socket closes and return the code it closed on."""
    for _ in range(limit):
        try:
            ws.receive(timeout=timeout)
        except WebSocketDisconnect as exc:
            return exc.code
    raise AssertionError("the connection never closed")


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------


def test_concurrent_producers_reach_the_wire_in_send_order() -> None:
    """The whole reason there is a queue: one order, not the scheduler's.

    Six coroutines interleave their sends at every ``sleep(0)``. Whichever
    order the loop resumes them in *is* the order ``send`` was called in, and
    that is the order the frames must appear on the socket -- globally, not
    per producer.
    """
    issued: List[Tuple[str, str]] = []

    @websocket("/ws")
    async def handler(socket: WebSocket) -> None:
        await socket.accept()
        outbound = Outbound(name="order")
        outbound.attach(socket)

        async def produce(which: int) -> None:
            for index in range(20):
                message = token(f"p{which}", str(index))
                if outbound.send(message):
                    issued.append((message.id, message.token))
                await asyncio.sleep(0)

        await asyncio.gather(*(produce(which) for which in range(6)))
        assert await outbound.drain(timeout=10)
        await outbound.close()

    with create_test_client([handler]) as client, client.websocket_connect("/ws") as ws:
        received: List[Tuple[str, str]] = []
        for _ in range(120):
            frame = json.loads(ws.receive_text(timeout=10))
            received.append((frame["id"], frame["token"]))
        assert close_code_of(ws) == 1000

    assert len(issued) == 120
    assert received == issued
    # Interleaving really happened, so the assertion above is not trivially
    # satisfied by six consecutive blocks of twenty.
    assert len({producer for producer, _ in received[:12]}) > 1


# --------------------------------------------------------------------------
# The backlog bound
# --------------------------------------------------------------------------


def test_overflow_refuses_the_frame_closes_and_is_observable() -> None:
    """A full backlog is a closed connection, a counter and a callback.

    The frames go in without an ``await`` between them, so the writer never
    gets to run and the bound is reached deterministically -- no TCP needed
    to prove the policy, only to prove it is ever reached.
    """
    seen: List[Overflow] = []
    state: Dict[str, Any] = {}

    @websocket("/ws")
    async def handler(socket: WebSocket) -> None:
        await socket.accept()
        outbound = Outbound(max_backlog=4, on_overflow=seen.append, name="overflow")
        outbound.attach(socket)
        state["accepted"] = [outbound.send(token("s", str(i))) for i in range(6)]
        await asyncio.wait_for(outbound.wait_closed(), 10)
        state["dropped"] = outbound.dropped
        state["overflowed"] = outbound.overflowed
        state["close_code"] = outbound.close_code

    with create_test_client([handler]) as client, client.websocket_connect("/ws") as ws:
        code = close_code_of(ws)

    assert code == CloseCode.INTERNAL
    assert state["accepted"] == [True, True, True, True, False, False]
    assert state["overflowed"] is True
    assert state["dropped"] == 2
    assert state["close_code"] == CloseCode.INTERNAL
    # Observable, and observable once: the second refusal is a closing queue,
    # not a second overflow.
    assert seen == [Overflow(tag="step.stream.token", backlog=4, dropped=1)]


def test_send_never_blocks_a_producer_on_a_stalled_socket() -> None:
    """``send`` is synchronous by construction, so it cannot join the drain.

    This is the property the queue is bought for: with the writer parked
    inside one ``send_text``, a thousand issues still cost the producer
    nothing.
    """

    async def scenario() -> None:
        socket = StubSocket(block=True)
        outbound = Outbound(name="nonblocking")
        outbound.attach(socket)  # type: ignore[arg-type]
        outbound.send(token("s", "first"))
        await asyncio.wait_for(socket.entered.wait(), 10)  # the writer is stuck
        started = time.perf_counter()
        # 1023, not ``DEFAULT_MAX_BACKLOG - 1``: the bound is a number this
        # module defends in prose, so the test states it rather than deriving
        # it from whatever the constant happens to say.
        for index in range(1023):
            assert outbound.send(token("s", str(index))) is True
        assert time.perf_counter() - started < 2.0
        assert DEFAULT_MAX_BACKLOG == 1024
        assert outbound.backlog == 1024
        assert outbound.sent == 0
        socket.release.set()
        await outbound.detach()

    asyncio.run(scenario())


def test_discard_drops_the_backlog_and_keeps_the_control_items() -> None:
    """The seam for a snapshot replay: deltas against a feed being replaced."""

    async def scenario() -> None:
        socket = StubSocket(block=True)
        outbound = Outbound(name="discard")
        outbound.attach(socket)  # type: ignore[arg-type]
        outbound.send(token("s", "held"))
        await asyncio.wait_for(socket.entered.wait(), 10)
        for index in range(9):
            outbound.send(token("s", str(index)))
        assert outbound.backlog == 10
        assert outbound.discard() == 10
        assert outbound.backlog == 0
        assert outbound.dropped == 10
        socket.release.set()
        await outbound.detach()

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# The fence
# --------------------------------------------------------------------------


def test_drain_waits_for_everything_already_queued() -> None:
    """``drain`` returning means the frames are on the socket, not near it."""
    state: Dict[str, Any] = {}

    @websocket("/ws")
    async def handler(socket: WebSocket) -> None:
        await socket.accept()
        outbound = Outbound(name="drain")
        outbound.attach(socket)
        for index in range(50):
            outbound.send(token("s", str(index)))
        state["backlog_before"] = outbound.backlog
        state["drained"] = await outbound.drain(timeout=10)
        state["sent_after"] = outbound.sent
        state["backlog_after"] = outbound.backlog
        await outbound.close()

    with create_test_client([handler]) as client, client.websocket_connect("/ws") as ws:
        for index in range(50):
            assert json.loads(ws.receive_text(timeout=10))["token"] == str(index)
        assert close_code_of(ws) == 1000

    assert state["backlog_before"] == 50
    assert state["drained"] is True
    assert state["sent_after"] == 50
    assert state["backlog_after"] == 0


def test_drain_does_not_wait_for_frames_queued_after_the_call() -> None:
    """A fence, not ``queue.join()``.

    A producer that keeps issuing faster than the socket drains is an
    ordinary streaming session, and "the queue is empty" is a state it never
    reaches. A drain built on quiescence would hang here for its whole
    deadline; this one returns as soon as its own marker goes past.
    """

    async def scenario() -> None:
        socket = StubSocket(yields=True)
        outbound = Outbound(max_backlog=1_000_000, name="fence")
        outbound.attach(socket)  # type: ignore[arg-type]
        outbound.send(token("s", "before"))

        stop = asyncio.Event()

        async def flood() -> None:
            while not stop.is_set():
                for _ in range(4):
                    outbound.send(token("s", "after"))
                await asyncio.sleep(0)

        flooding = asyncio.ensure_future(flood())
        try:
            assert await asyncio.wait_for(outbound.drain(timeout=5), 10) is True
        finally:
            stop.set()
            await flooding
        assert outbound.backlog > 0  # the session is still streaming
        await outbound.detach()

    asyncio.run(scenario())


def test_drain_gives_up_on_its_deadline() -> None:
    """A stalled socket costs the deadline and returns False, not forever."""

    async def scenario() -> None:
        socket = StubSocket(block=True)
        outbound = Outbound(name="deadline")
        outbound.attach(socket)  # type: ignore[arg-type]
        outbound.send(token("s", "held"))
        started = time.perf_counter()
        assert await asyncio.wait_for(outbound.drain(timeout=0.1), 5) is False
        assert time.perf_counter() - started < 2.0
        socket.release.set()
        await outbound.detach()

    asyncio.run(scenario())


def test_a_terminal_close_resolves_a_waiting_fence() -> None:
    """The hang this class must not have.

    A ``drain`` in flight when the connection dies would otherwise sit on its
    own deadline -- ten seconds by default -- waiting for a writer that has
    stopped. Every terminal transition sweeps the fences instead.
    """

    async def scenario() -> None:
        socket = StubSocket(block=True)
        outbound = Outbound(name="sweep")
        outbound.attach(socket)  # type: ignore[arg-type]
        for index in range(3):
            outbound.send(token("s", str(index)))
        pending = asyncio.ensure_future(outbound.drain(timeout=30))
        await asyncio.sleep(0)
        started = time.perf_counter()
        await outbound.close(timeout=0.1)
        assert await asyncio.wait_for(pending, 5) is False
        assert time.perf_counter() - started < 5.0
        assert outbound.closed is True
        assert outbound.close_code == 1000
        socket.release.set()

    asyncio.run(scenario())


def test_detach_resolves_a_waiting_fence() -> None:
    """The same hazard on the blip path, where the queue is *not* closed."""

    async def scenario() -> None:
        socket = StubSocket(block=True)
        outbound = Outbound(name="blip")
        outbound.attach(socket)  # type: ignore[arg-type]
        outbound.send(token("s", "0"))
        pending = asyncio.ensure_future(outbound.drain(timeout=30))
        await asyncio.sleep(0)
        await outbound.detach()
        assert await asyncio.wait_for(pending, 5) is False
        assert outbound.closed is False

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Close discipline
# --------------------------------------------------------------------------


def test_an_oversized_frame_closes_with_frame_too_large() -> None:
    """Over the limit is a close, never a truncation and never a silent drop.

    In-memory transports have no frame limit at all, so a queue that did not
    check would hand the browser a frame the browser rejects: an unexplained
    disconnect in production and a green test here.
    """
    state: Dict[str, Any] = {}

    @websocket("/ws")
    async def handler(socket: WebSocket) -> None:
        await socket.accept()
        outbound = Outbound(name="huge")
        outbound.attach(socket)
        outbound.send(Toast(message="hello"))
        outbound.send(Toast(message="x" * (MAX_FRAME_BYTES + 1)))
        outbound.send(Toast(message="never"))
        await asyncio.wait_for(outbound.wait_closed(), 30)
        state["close_code"] = outbound.close_code
        state["sent"] = outbound.sent
        state["dropped"] = outbound.dropped

    with create_test_client([handler]) as client, client.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text(timeout=30))["message"] == "hello"
        code = close_code_of(ws, timeout=30)

    assert code == CloseCode.FRAME_TOO_LARGE
    assert state["close_code"] == CloseCode.FRAME_TOO_LARGE
    assert state["sent"] == 1
    assert state["dropped"] == 1


def test_close_flushes_the_backlog_before_the_close_frame() -> None:
    """The close travels through the queue, so it cannot overtake a frame."""

    @websocket("/ws")
    async def handler(socket: WebSocket) -> None:
        await socket.accept()
        outbound = Outbound(name="graceful")
        outbound.attach(socket)
        for index in range(30):
            outbound.send(token("s", str(index)))
        await outbound.close(code=CloseCode.SUPERSEDED, reason="taken over")

    with create_test_client([handler]) as client, client.websocket_connect("/ws") as ws:
        for index in range(30):
            assert json.loads(ws.receive_text(timeout=10))["token"] == str(index)
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive(timeout=10)

    assert excinfo.value.code == CloseCode.SUPERSEDED
    assert excinfo.value.detail == "taken over"


def test_abort_drops_the_backlog_and_closes_now() -> None:
    """The other half: a failure that made the queued frames meaningless."""

    async def scenario() -> None:
        socket = StubSocket()
        outbound = Outbound(name="abort")
        outbound.attach(socket)  # type: ignore[arg-type]
        for index in range(20):
            outbound.send(token("s", str(index)))
        outbound.abort(CloseCode.SESSION_FORBIDDEN, "not yours")
        await asyncio.wait_for(outbound.wait_closed(), 5)
        assert outbound.close_code == CloseCode.SESSION_FORBIDDEN
        assert socket.closed == (CloseCode.SESSION_FORBIDDEN, "not yours")
        assert socket.frames == []
        assert outbound.send(token("s", "late")) is False

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# The socket dies; the session does not
# --------------------------------------------------------------------------


def test_a_frame_lost_mid_write_is_held_for_the_next_writer() -> None:
    """A blip must not eat the frame that was in flight.

    The frame is peeked, not popped, so a writer that dies inside
    ``send_text`` leaves it at the head of the queue. A duplicate after a
    reconnect is harmless on this wire -- upserts and patches are idempotent
    -- and a hole is not recoverable at all.
    """

    async def scenario() -> None:
        first = StubSocket()
        first.fail_with = WebSocketDisconnect(detail="gone", code=1006)
        outbound = Outbound(name="held")
        outbound.attach(first)  # type: ignore[arg-type]
        outbound.send(token("s", "a"))
        outbound.send(token("s", "b"))
        await asyncio.wait_for(first.entered.wait(), 10)
        for _ in range(100):
            if not outbound.attached:
                break
            await asyncio.sleep(0.01)

        assert outbound.attached is False
        assert outbound.closed is False  # a dead socket is not a closed queue
        assert outbound.close_code is None
        assert outbound.backlog == 2

        second = StubSocket()
        outbound.attach(second)  # type: ignore[arg-type]
        assert await outbound.drain(timeout=10) is True
        assert [json.loads(frame)["token"] for frame in second.frames] == ["a", "b"]
        await outbound.detach()

    asyncio.run(scenario())


def test_a_client_disconnect_mid_write_does_not_surface_as_4500() -> None:
    """The handler exits cleanly, so Litestar never reaches its catch-all.

    ``except*`` and not ``except``: see
    ``test_disconnect_arrives_from_a_task_group_as_an_exception_group``. The
    queue swallows the send failure itself, so the only exception the handler
    has to shape is its reader's.
    """
    state: Dict[str, Any] = {}

    @websocket("/ws")
    async def handler(socket: WebSocket) -> None:
        await socket.accept()
        outbound = Outbound(name="hangup")
        outbound.attach(socket)
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(socket.receive_text())
                for index in range(500):
                    outbound.send(token("s", str(index)))
                    await asyncio.sleep(0)
        except* WebSocketDisconnect:
            state["disconnected"] = True
        await outbound.detach()
        state["closed"] = outbound.closed
        state["close_code"] = outbound.close_code

    with create_test_client([handler]) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_text(timeout=10)
        # Leaving the block sends the disconnect and re-raises anything the
        # handler let escape; a 4500 close would be visible right here.
        deadline = time.monotonic() + 10
        while "closed" not in state and time.monotonic() < deadline:
            time.sleep(0.01)

    assert state.get("disconnected") is True
    assert state["closed"] is False
    assert state["close_code"] is None


# --------------------------------------------------------------------------
# Framework facts this design is built on
# --------------------------------------------------------------------------


def test_disconnect_arrives_from_a_task_group_as_an_exception_group() -> None:
    """anyio 4.14 wraps even a lone child exception; ``except`` never fires.

    ``create_task_group()`` takes no arguments in 4.x -- the
    ``strict_exception_groups=False`` escape hatch is gone -- so the
    ``except WebSocketDisconnect`` in Litestar's own documented websocket
    example cannot catch anything.
    """
    caught: List[str] = []

    async def scenario() -> None:
        async def boom() -> None:
            raise WebSocketDisconnect(detail="gone", code=1006)

        try:
            async with anyio.create_task_group() as group:
                group.start_soon(boom)
        except WebSocketDisconnect:  # pragma: no cover - the point of the test
            caught.append("bare")
        except BaseExceptionGroup as group_error:
            caught.extend(
                ["group", *(type(e).__name__ for e in group_error.exceptions)]
            )

    asyncio.run(scenario())
    assert caught == ["group", "WebSocketDisconnect"]


def test_exception_handlers_are_never_consulted_in_the_websocket_scope() -> None:
    """Which is why the writer closes the socket itself.

    ``handle_websocket_exception`` is a ``@staticmethod`` that reads
    ``exc.code`` off a ``WebSocketException`` and closes 4500 for everything
    else. It never looks at ``exception_handlers``, so a close code cannot be
    routed through one.
    """
    called: List[Exception] = []

    def never(request: Any, exc: Exception) -> Any:  # pragma: no cover
        called.append(exc)
        raise AssertionError("the websocket scope does not use exception handlers")

    @websocket("/ws")
    async def handler(socket: WebSocket) -> None:
        await socket.accept()
        raise RuntimeError("a plain error, with a handler registered for it")

    with create_test_client(
        [handler], exception_handlers={RuntimeError: never}
    ) as client:
        with client.websocket_connect("/ws") as ws:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive(timeout=10)

    assert excinfo.value.code == 4500
    assert excinfo.value.detail == "Internal Server Error"
    assert called == []


def test_receiving_nothing_more_is_an_empty_queue() -> None:
    """The "and no more frames" assertion, pinned so it stays available."""

    @websocket("/ws")
    async def handler(socket: WebSocket) -> None:
        await socket.accept()
        outbound = Outbound(name="quiet")
        outbound.attach(socket)
        outbound.send(Toast(message="only"))
        assert await outbound.drain(timeout=10)
        await socket.receive_text()
        await outbound.close()

    with create_test_client([handler]) as client, client.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text(timeout=10))["message"] == "only"
        with pytest.raises(queue_mod.Empty):
            ws.receive(block=False)
        ws.send_text("bye")
        assert close_code_of(ws) == 1000


# --------------------------------------------------------------------------
# Live uvicorn
# --------------------------------------------------------------------------


@asynccontextmanager
async def live_server(app: Litestar) -> AsyncIterator[int]:
    """Serve ``app`` on a loopback port for the body of the block."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
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


async def raw_upgrade(
    port: int, path: str
) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a websocket and then never read from it again.

    A client library would keep draining the socket into a buffer of its own,
    which is the opposite of the case under test. Nothing here reads a byte
    after the handshake, so the receive window closes and the server's writer
    lands in exactly the drain this queue exists to bound.
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    key = base64.b64encode(os.urandom(16)).decode()
    writer.write(
        f"GET {path} HTTP/1.1\r\n"
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
    return reader, writer


class SpySocket:
    """Delegates to the real socket, remembering what its failures were."""

    def __init__(self, socket: WebSocket, state: Dict[str, Any]) -> None:
        self._socket = socket
        self._state = state

    async def send_text(self, data: Any, encoding: str = "utf-8") -> None:
        try:
            await self._socket.send_text(data, encoding)
        except BaseException as exc:
            self._state["send_error"] = type(exc).__name__
            raise

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self._socket.close(code=code, reason=reason)


async def test_live_slow_client_overflows_rather_than_blocking_producers() -> None:
    """The real backpressure path, which no in-memory transport reproduces.

    The client completes the handshake and then reads nothing, so the receive
    window shuts and the writer parks inside one ``send_text``. The producer
    keeps issuing -- that is what the queue buys -- until the bound is
    reached, at which point the policy fires and the connection ends with a
    code the browser can see.
    """
    state: Dict[str, Any] = {}
    payload = "y" * 200_000

    def record(event: Overflow) -> None:
        state["overflow"] = event

    @websocket("/slow")
    async def slow(socket: WebSocket) -> None:
        await socket.accept()
        outbound = Outbound(max_backlog=8, on_overflow=record, name="slow")
        outbound.attach(socket)
        started = time.perf_counter()
        for _ in range(400):
            if not outbound.send(Toast(message=payload)):
                break
            await asyncio.sleep(0)
        state["push_seconds"] = time.perf_counter() - started
        await asyncio.wait_for(outbound.wait_closed(), 60)
        state["overflowed"] = outbound.overflowed
        state["dropped"] = outbound.dropped
        state["close_code"] = outbound.close_code
        state["done"] = True

    async with live_server(Litestar([slow])) as port:
        _reader, writer = await raw_upgrade(port, "/slow")
        try:
            deadline = time.monotonic() + 60
            while not state.get("done") and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
        finally:
            writer.close()

    assert state.get("done") is True, state
    assert state["overflowed"] is True
    assert state["dropped"] >= 1
    assert state["close_code"] == CloseCode.INTERNAL
    # The bound was reached because the socket stalled, and the producer paid
    # nothing for it: 400 issues against a wedged connection in well under a
    # second, where an unqueued producer would still be inside its first send.
    assert state["push_seconds"] < 10.0
    assert isinstance(state["overflow"], Overflow)
    assert state["overflow"].backlog == 8


async def test_live_hangup_mid_frame_holds_the_frame_and_closes_nothing() -> None:
    """A peer that vanishes is not a server error and not a queue close.

    In memory the failure is Litestar's ``WebSocketDisconnect``; on uvicorn it
    is whatever the ws implementation raises for a dead peer, which is why the
    writer's guard is a bare ``except Exception``. Either way the frame stays
    queued, the queue stays open for the next socket, and nothing propagates
    to the handler -- an exception escaping there is a 4500, reported to the
    user as an internal server error for the crime of closing a tab.
    """
    state: Dict[str, Any] = {}

    @websocket("/hangup")
    async def hangup(socket: WebSocket) -> None:
        await socket.accept()
        outbound = Outbound(max_backlog=100_000, name="hangup")
        outbound.attach(SpySocket(socket, state))  # type: ignore[arg-type]
        deadline = time.monotonic() + 45
        for _ in range(4_000):
            outbound.send(Toast(message="z" * 200_000))
            await asyncio.sleep(0)
            if not outbound.attached or time.monotonic() > deadline:
                break
        state["attached"] = outbound.attached
        state["closed"] = outbound.closed
        state["close_code"] = outbound.close_code
        state["backlog"] = outbound.backlog
        state["done"] = True

    async with live_server(Litestar([hangup])) as port:
        _reader, writer = await raw_upgrade(port, "/hangup")
        writer.close()
        deadline = time.monotonic() + 60
        while not state.get("done") and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    assert state.get("done") is True, state
    assert state.get("send_error"), "the writer never saw the peer go away"
    assert state["attached"] is False
    assert state["closed"] is False
    assert state["close_code"] is None
    assert state["backlog"] > 0
