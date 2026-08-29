"""The websocket route, and the two loops that share it.

One raw ``@websocket``. Not ``websocket_listener``, which is strictly
turn-taking -- receive, call, reply -- and this protocol is not: the server
talks whenever it has something to say, and the client talks over it. Not
``websocket_stream`` either, which is send-only and quietly throws away
every inbound frame.

So the handler runs a reader and a writer concurrently in one task group,
and everything below is about the ways that arrangement goes wrong.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

import anyio
import msgspec
from litestar import WebSocket, websocket
from litestar.exceptions import WebSocketDisconnect

from chainlit.protocol.client import (
    AskReply,
    ClientMsg,
    HeartbeatAck,
    Hello,
    MessageSend,
    SessionClear,
    Stop,
)
from chainlit.protocol.codec import MAX_FRAME_BYTES, CloseCode, ErrorCode, decode_client
from chainlit.protocol.server import Error, Heartbeat
from chainlit.ws.handshake import Arrival, ThreadStore, arrive, ready_frame, restore
from chainlit.ws.registry import SessionRegistry
from chainlit.ws.session import Session

logger = logging.getLogger(__name__)

__all__ = [
    "HEARTBEAT_INTERVAL_MS",
    "HELLO_DEADLINE_SECONDS",
    "make_websocket_handler",
]

HELLO_DEADLINE_SECONDS = 10.0
"""How long a socket may stay open having said nothing.

A connection that accepts the upgrade and then never speaks costs a task
and a slot for as long as the peer keeps the TCP connection alive, which
can be indefinitely. Ten seconds is generous for a frame the client sends
immediately on open.
"""

HEARTBEAT_INTERVAL_MS = 20_000
"""Probe interval, echoed to the client in ``session.ready``.

A silent socket is indistinguishable from a healthy one until something is
written to it, so a session parked on a question can sit against a peer
that vanished hours ago. The probe is what turns that into a close.
"""


async def _first_hello(socket: WebSocket[Any, Any, Any]) -> Hello:
    """Read the opening frame, or refuse the connection.

    Anything other than a well-formed ``hello`` closes: at this point there
    is no session to report an error against, and an ``error`` frame on a
    socket that will never be usable is worse than the close code.
    """
    try:
        raw = await asyncio.wait_for(
            socket.receive_text(), timeout=HELLO_DEADLINE_SECONDS
        )
    except TimeoutError:
        raise _Refused(CloseCode.BAD_HANDSHAKE, "no hello") from None

    if len(raw.encode()) > MAX_FRAME_BYTES:
        raise _Refused(CloseCode.FRAME_TOO_LARGE, "hello too large")

    try:
        message = decode_client(raw.encode())
    except (msgspec.ValidationError, msgspec.DecodeError) as error:
        raise _Refused(CloseCode.BAD_HANDSHAKE, str(error)) from None

    if not isinstance(message, Hello):
        raise _Refused(CloseCode.BAD_HANDSHAKE, "first frame was not hello")
    return message


class _Refused(Exception):
    """The connection cannot be used. Carries the code to close with."""

    def __init__(self, code: int, reason: str = "") -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def make_websocket_handler(
    *,
    registry: SessionRegistry,
    make_session: Callable[[str, Hello, Any], Session],
    thread_store: Optional[ThreadStore] = None,
    on_arrival: Optional[Callable[[Arrival], Awaitable[None]]] = None,
    on_ready: Optional[Callable[[Arrival], Awaitable[None]]] = None,
    on_disconnect: Optional[Callable[[Session], Awaitable[None]]] = None,
    heartbeat_ms: int = HEARTBEAT_INTERVAL_MS,
) -> Any:
    """Build the route handler, closing over what the application supplies.

    A factory rather than a module-level handler because the registry is
    owned by the plugin instance: a module-level one would be the old
    process-wide dict again, and two applications in one interpreter -- two
    tests, most of the time -- would see each other's sessions.

    Two application hooks bracket the handshake, and the split is about
    frame order. ``on_arrival`` runs before ``session.ready`` goes out and
    may only change *state* -- claim a handover, load a transcript -- because
    anything it sent would land ahead of the frame the client resets its
    buffer on. ``on_ready`` runs after the restore has replayed the screen,
    and is where the application's hooks are launched: their first messages
    have to land on top of the rebuilt feed, not under it.
    """

    @websocket("/ws")
    async def chainlit_websocket(socket: WebSocket[Any, Any, Any]) -> None:
        # Guards and the authentication middleware have already run: their
        # scopes include the websocket one, and a refusal from either
        # happens *before* accept, which the browser sees as a failed
        # upgrade -- an HTTP status, not a close code. Nothing here can
        # turn that into a close frame, and the client knows it.
        user = socket.scope.get("user")

        await socket.accept()

        try:
            hello = await _first_hello(socket)
        except _Refused as refusal:
            await socket.close(code=refusal.code, reason=refusal.reason)
            return

        arrival = await arrive(
            registry=registry,
            session_id=hello.session_id,
            user_identifier=_identifier(user),
            page_load=hello.page_load,
            thread_id=hello.thread_id,
            make_session=lambda sid: make_session(sid, hello, user),
        )
        if arrival.refused or arrival.session is None:
            await socket.close(
                code=CloseCode.SESSION_FORBIDDEN,
                reason="session belongs to another user",
            )
            return

        session = arrival.session
        if on_arrival is not None:
            await on_arrival(arrival)

        # A kept session may still be wearing its last socket. The client
        # rebuilds its transport without waiting for the old one to close
        # (a profile change does that), so the new socket can arrive while
        # the previous handler is still reading. This one takes over: the
        # old writer is stopped *before* anything is queued for the new
        # socket, or it would drain ``session.ready`` onto a connection
        # that is on its way out, and the old socket is closed so its
        # handler stops -- two heartbeat loops on one session would time
        # each other out.
        await _take_over(session, socket)

        # ``session.ready`` goes to the front of the queue, and only then is
        # the writer attached: a kept session may still hold frames the
        # previous socket never took, and those are a continuation the
        # client is entitled to -- after the frame it starts on, not before.
        # Attached only now, too, because a writer attached before the
        # handshake would drain frames onto a connection we might refuse.
        session.outbound.send(
            ready_frame(
                session,
                restored=arrival.outcome.value == "kept",
                heartbeat_ms=heartbeat_ms,
            ),
            first=True,
        )
        session.outbound.attach(socket)

        try:
            await _serve(
                socket,
                session,
                thread_store=thread_store,
                heartbeat_ms=heartbeat_ms,
                on_ready=(lambda: on_ready(arrival)) if on_ready is not None else None,
                fresh_page_load=arrival.fresh_page_load,
            )
        finally:
            current = session.outbound.socket
            superseded = current is not None and current is not socket
            # Superseded: a newer socket holds this session, and the
            # bookkeeping is its to do when *it* goes. Marking the session
            # disconnected here would start a reaper against a live
            # connection, and detaching would take the writer out from
            # under it -- which is what left the client waiting on a
            # ``session.ready`` that had already been written to the wrong
            # socket. Otherwise the socket is gone and the session is not:
            # it keeps its queue, its question and its work, and the
            # registry keeps it until something decides otherwise.
            if not superseded:
                session.connected = False
                registry.mark_disconnected(session.id)
                await session.outbound.detach()
                if on_disconnect is not None:
                    await on_disconnect(session)

    return chainlit_websocket


async def _take_over(session: Session, socket: WebSocket[Any, Any, Any]) -> None:
    """Make ``socket`` the one the session writes to, whatever it had before.

    A queue its last writer aborted is closed for good, and the session is
    still worth a socket -- it was kept for a reason -- so it gets a fresh
    one. A writer still running is stopped and its socket closed; the close
    is best-effort, the peer may already be gone.
    """
    session.renew_outbound()
    previous = session.outbound.socket
    if previous is None:
        return
    await session.outbound.detach()
    try:
        await previous.close(code=CloseCode.SUPERSEDED, reason="superseded")
    except Exception:  # pragma: no cover - a socket already gone
        pass


async def _serve(
    socket: WebSocket[Any, Any, Any],
    session: Session,
    *,
    thread_store: Optional[ThreadStore],
    heartbeat_ms: int,
    on_ready: Optional[Callable[[], Awaitable[None]]] = None,
    fresh_page_load: bool = True,
) -> None:
    """Run the reader, the restore and the heartbeat until the socket goes.

    ``except*``, not ``except``: anyio wraps even a single child-task
    exception in an ``ExceptionGroup``, and ``create_task_group()`` no
    longer takes the escape hatch that used to turn that off. A plain
    ``except WebSocketDisconnect`` here is unreachable -- the ordinary case
    of a user closing a tab would escape the group and be reported to them
    as an internal server error.
    """
    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(_read_loop, socket, session, tasks.cancel_scope)
            tasks.start_soon(_heartbeat, session, heartbeat_ms, tasks.cancel_scope)
            # Concurrent with the reader on purpose. `session.ready` has
            # already gone out, so the client is flushing whatever it
            # buffered while disconnected -- and an answer typed before a
            # reload arrives *during* this, which is exactly what the
            # restore has to notice.
            await restore(
                session, thread_store=thread_store, fresh_page_load=fresh_page_load
            )
            if on_ready is not None:
                # After the replay, inside the group: a hook that fails is
                # this connection's failure, and one that launches work
                # launches it onto the session, which outlives the group.
                await on_ready()
    except* WebSocketDisconnect:
        pass
    except* Exception as errors:
        logger.exception("websocket session %s failed", session.id, exc_info=errors)


async def _read_loop(
    socket: WebSocket[Any, Any, Any], session: Session, scope: anyio.CancelScope
) -> None:
    while True:
        try:
            raw = await socket.receive_text()
        except WebSocketDisconnect:
            scope.cancel()
            return

        if len(raw.encode()) > MAX_FRAME_BYTES:
            session.outbound.abort(CloseCode.FRAME_TOO_LARGE, "inbound frame too large")
            scope.cancel()
            return

        try:
            message = decode_client(raw.encode())
        except msgspec.ValidationError as error:
            # The socket stays open. A frame this connection does not
            # understand is not a reason to take away one it does: the
            # error is addressed to the client's next release, not to the
            # user's conversation.
            session.send(Error(code=ErrorCode.UNKNOWN_TAG.value, message=str(error)))
            continue
        except msgspec.DecodeError as error:
            session.send(Error(code=ErrorCode.BAD_MESSAGE.value, message=str(error)))
            continue

        await _dispatch(session, message)


async def _dispatch(session: Session, message: ClientMsg) -> None:
    """Seven tags. Everything else the client can say, it cannot say twice."""
    if isinstance(message, HeartbeatAck):
        session.last_ack = message.seq
        return

    if isinstance(message, Hello):
        # A second hello on an established socket. The handshake is not
        # re-runnable -- it has side effects -- so this is ignored rather
        # than replayed.
        session.send(
            Error(
                code=ErrorCode.BAD_MESSAGE.value,
                message="the session is already open",
            )
        )
        return

    runner = session.runner
    if isinstance(message, MessageSend):
        if runner is not None:
            await runner.on_message(session, message.message, message.file_references)
        return

    if isinstance(message, Stop):
        if runner is not None:
            await runner.on_stop(session)
        return

    if isinstance(message, AskReply):
        _deliver_reply(session, message)
        return

    if isinstance(message, SessionClear):
        session.cancel_work()
        session.transcript.clear()
        return


def _deliver_reply(session: Session, message: AskReply) -> None:
    """Hand an answer to the question it belongs to, or hold it.

    Addressed by ``stepId``, which is the whole reason the reply is a
    message rather than a socket.io ack: an ack was bound to the socket and
    could not survive the reconnect the client buffers across. An answer
    for a question we are not showing is parked, not dropped -- it is the
    only copy of something the user typed, and the handshake may still be
    restoring the question it answers.
    """
    ask = session.pending_ask
    if ask is None or not ask.is_live or ask.step_id != message.step_id:
        session.parked_replies.append(
            {"stepId": message.step_id, "value": message.value}
        )
        return
    if not ask.future.done():
        ask.future.set_result(message.value)


async def _heartbeat(
    session: Session, interval_ms: int, scope: anyio.CancelScope
) -> None:
    """Probe a silent socket, and close one that stops answering.

    Closing is the queue's job, not this loop's: the writer owns the close
    sequence, so a probe that expires aborts the queue rather than touching
    the socket, and one owner still ends the connection.
    """
    interval = interval_ms / 1000
    seq = 0
    while True:
        await asyncio.sleep(interval)
        seq += 1
        session.send(Heartbeat(seq=seq))
        await asyncio.sleep(interval)
        if session.last_ack != seq:
            session.outbound.abort(CloseCode.HEARTBEAT_TIMEOUT, "no heartbeat ack")
            scope.cancel()
            return


def _identifier(user: Any) -> Optional[str]:
    if user is None:
        return None
    return getattr(user, "identifier", None) or getattr(user, "id", None)
