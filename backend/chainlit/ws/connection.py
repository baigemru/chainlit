"""The websocket route, and the two loops that share it.

One raw ``@websocket``. Not ``websocket_listener``, which is strictly
turn-taking -- receive, call, reply -- and this protocol is not: the server
talks whenever it has something to say, and the client talks over it. Not
``websocket_stream`` either, which is send-only and quietly throws away
every inbound frame.

So the handler runs a reader and a writer concurrently in one task group,
and everything below is about the ways that arrangement goes wrong.

The thing that keeps it from going wrong is ``Connection``: one object per
accepted socket, and ``session.current`` says which one speaks. A session
outlives its sockets and can be handed from one to the next mid-sentence,
so every loop here asks whether it is still the current connection before
it touches anything the session owns. Without that owner the question was
answered by inference -- comparing socket objects through the send queue,
consulting a flag another coroutine had just cleared -- and each inference
was right only if the two handlers happened to be scheduled in the right
order.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Mapping, Optional

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
from chainlit.ws.outbound import FORCE_CLOSE_GRACE
from chainlit.ws.registry import SessionRegistry
from chainlit.ws.session import Session

logger = logging.getLogger(__name__)

__all__ = [
    "HEARTBEAT_INTERVAL_MS",
    "HELLO_DEADLINE_SECONDS",
    "Connection",
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


class Connection:
    """One accepted socket, and everything true only while it is speaking.

    The session is the conversation; this is the transport under it, and the
    two have different lifetimes. What lives here is what a *second* socket
    on the same session must not share:

    ``generation`` -- the number the session gave this connection when it
    adopted it. Never reused, so a stale loop's own number is enough to say
    it is stale even after the session has moved on twice.

    ``seq`` / ``last_ack`` -- the heartbeat's state. The probe is kept
    although uvicorn pings the peer itself, because a frozen tab answers
    the browser's protocol pings and nothing from its JavaScript, and the
    sweep of abandoned questions has to tell the two apart. The counters
    lived on the session until two loops on one session started timing each
    other out: the old connection's probe was answered against the new
    connection's counter, so the old loop declared the peer dead and closed
    a socket it did not own. Zero means unanswered, which is also where
    every connection starts -- so a probe compares against the sequence it
    sent, never against zero.

    ``current`` -- the only question any loop here has to ask. A connection
    that has lost it does nothing further to the session: not marking it
    disconnected, not stopping its writer, not closing its queue. Those
    belong to whoever holds the session now.
    """

    __slots__ = ("generation", "last_ack", "seq", "session", "socket")

    def __init__(self, session: Session, socket: WebSocket[Any, Any, Any]) -> None:
        self.session = session
        self.socket = socket
        self.generation = 0
        self.seq = 0
        self.last_ack = 0

    @property
    def current(self) -> bool:
        """Whether this connection still speaks for its session."""
        return self.session.current is self

    async def close(self, code: int, reason: str = "") -> None:
        """Say goodbye to the peer, if it is still there to hear it.

        Best-effort and bounded. On the ``websockets`` implementation
        ``close`` awaits the closing handshake -- up to ten seconds against
        a peer that has stopped answering -- and this is called from the
        handler of the socket that replaced this one, which has a client
        waiting on it. A goodbye nobody is left to read is not worth that.
        """
        try:
            await asyncio.wait_for(
                self.socket.close(code=code, reason=reason), FORCE_CLOSE_GRACE
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.debug(
                "connection %d of session %s did not close in %ss",
                self.generation,
                self.session.id,
                FORCE_CLOSE_GRACE,
            )
        except Exception:  # pragma: no cover - a socket already gone
            pass


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

        connection = Connection(session, socket)
        previous = await _take_over(
            connection,
            ready_frame(
                session,
                restored=arrival.outcome.value == "kept",
                heartbeat_ms=heartbeat_ms,
            ),
        )

        try:
            await _serve(
                connection,
                thread_store=thread_store,
                heartbeat_ms=heartbeat_ms,
                on_ready=(lambda: on_ready(arrival)) if on_ready is not None else None,
                fresh_page_load=arrival.fresh_page_load,
                resumed_thread=arrival.resumed_thread,
                goodbye_to=previous,
            )
        finally:
            # Only the current connection tears anything down. A superseded
            # one is a handler whose socket happened to notice its own death
            # after a newer one had already taken the session over, and every
            # line below would be aimed at the wrong connection: marking the
            # session disconnected starts a reaper against a live client,
            # detaching takes the writer out from under it, and
            # ``on_disconnect`` runs ``on_chat_end`` on a chat that has not
            # ended. Otherwise the socket is gone and the session is not: it
            # keeps its queue, its question and its work, and the registry
            # keeps it until something decides otherwise.
            if connection.current:
                session.current = None
                session.connected = False
                registry.mark_disconnected(session.id)
                await session.outbound.detach(socket)
                if on_disconnect is not None:
                    await on_disconnect(session)

    return chainlit_websocket


async def _take_over(connection: Connection, ready: Any) -> Optional[Connection]:
    """Give the session to ``connection``, in the only order that works.

    A kept session may still be wearing its last socket: the client rebuilds
    its transport without waiting for the old one to close (a profile change
    does that), so a new socket arrives while the previous handler is still
    reading. The four steps are ordered by what each one makes safe.

    1. Become the current connection, before anything else can be observed.
       From here the previous handler's loops are stale and know it, so
       nothing they do afterwards can reach the session.
    2. Stop the previous writer, before a frame is queued -- otherwise
       ``session.ready`` drains onto the connection on its way out, and the
       client that is waiting for it waits forever.
    3. Queue ``session.ready`` at the *front* and attach this socket's
       writer. A kept session may still hold frames the previous socket
       never took; they are a continuation the client is entitled to, after
       the frame it starts on and not before. Attached only now, too: a
       writer attached before the handshake would drain onto a connection
       we might still refuse.
    4. Say goodbye to the old socket -- returned rather than done here, and
       run alongside the reader rather than ahead of it. On a peer that
       answers the close frame and then never closes its end of the TCP
       connection, which is what a frozen tab looks like, ``close`` waits
       out its own timeout; a handshake that waited with it would hold the
       arriving client's replay behind a goodbye addressed to nobody. The
       old handler must not need it either: its loops leave on their own the
       moment they see they are not current.
    """
    session = connection.session
    previous = session.adopt(connection)
    if session.outbound.attached:
        # Whatever writer it had, whether or not the connection that
        # attached it is still around to be told.
        await session.outbound.detach()
    session.outbound.send(ready, first=True)
    session.outbound.attach(connection.socket)
    return previous


async def _serve(
    connection: Connection,
    *,
    thread_store: Optional[ThreadStore],
    heartbeat_ms: int,
    on_ready: Optional[Callable[[], Awaitable[None]]] = None,
    fresh_page_load: bool = True,
    resumed_thread: Optional[Mapping[str, Any]] = None,
    goodbye_to: Optional[Connection] = None,
) -> None:
    """Run the reader, the restore and the heartbeat until the socket goes.

    ``except*``, not ``except``: anyio wraps even a single child-task
    exception in an ``ExceptionGroup``, and ``create_task_group()`` no
    longer takes the escape hatch that used to turn that off. A plain
    ``except WebSocketDisconnect`` here is unreachable -- the ordinary case
    of a user closing a tab would escape the group and be reported to them
    as an internal server error.
    """
    session = connection.session
    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(_read_loop, connection, tasks.cancel_scope)
            tasks.start_soon(_heartbeat, connection, heartbeat_ms, tasks.cancel_scope)
            if goodbye_to is not None:
                # The last step of the takeover, run where a task that may
                # outlive its usefulness is already accounted for: if this
                # connection ends first the group cancels the goodbye, which
                # is exactly the right answer to "was it delivered?".
                tasks.start_soon(goodbye_to.close, CloseCode.SUPERSEDED, "superseded")
            # Concurrent with the reader on purpose. `session.ready` has
            # already gone out, so the client is flushing whatever it
            # buffered while disconnected -- and an answer typed before a
            # reload arrives *during* this, which is exactly what the
            # restore has to notice.
            await restore(
                session,
                thread_store=thread_store,
                fresh_page_load=fresh_page_load,
                resumed_thread=resumed_thread,
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


async def _read_loop(connection: Connection, scope: anyio.CancelScope) -> None:
    socket = connection.socket
    session = connection.session
    while True:
        try:
            raw = await socket.receive_text()
        except WebSocketDisconnect:
            scope.cancel()
            return

        if len(raw.encode()) > MAX_FRAME_BYTES:
            # The socket goes, not the queue: a client that overran the
            # frame limit is a client that will reconnect, and what the
            # session has to say is still worth saying to it.
            if connection.current:
                await session.outbound.drop(
                    CloseCode.FRAME_TOO_LARGE, "inbound frame too large"
                )
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

        if isinstance(message, HeartbeatAck):
            # Answered to this connection, never to the session: the ack is
            # about the socket it arrived on, and a session that held the
            # counter had the old connection's probe satisfied by the new
            # connection's client.
            connection.last_ack = message.seq
            continue

        await _dispatch(session, message)


async def _dispatch(session: Session, message: ClientMsg) -> None:
    """Five tags. The sixth, ``hb.ack``, is the connection's and never
    reaches here. Everything else the client can say, it cannot say twice."""
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
    connection: Connection, interval_ms: int, scope: anyio.CancelScope
) -> None:
    """Probe a silent socket, and close one that stops answering.

    Every wake begins by asking whether this connection is still the one.
    Two loops on one session is the ordinary case -- a client rebuilds its
    transport and both handlers are alive for a moment -- and the stale one
    must neither probe (the frame would go to the new socket, against a
    counter that is not its own) nor conclude anything from the silence it
    is bound to hear. It leaves instead, which is also what ends its
    handler when the goodbye sent to its peer never lands.

    Closing is the queue's job, not this loop's: the writer owns the close
    sequence. It is ``drop``, not ``abort`` -- the socket is finished, the
    conversation is not, and the client's answer to both is to reconnect.
    """
    interval = interval_ms / 1000
    while True:
        await asyncio.sleep(interval)
        if not connection.current:
            scope.cancel()
            return
        connection.seq += 1
        connection.session.send(Heartbeat(seq=connection.seq))
        await asyncio.sleep(interval)
        if not connection.current:
            scope.cancel()
            return
        if connection.last_ack != connection.seq:
            await connection.session.outbound.drop(
                CloseCode.HEARTBEAT_TIMEOUT, "no heartbeat ack"
            )
            scope.cancel()
            return


def _identifier(user: Any) -> Optional[str]:
    if user is None:
        return None
    return getattr(user, "identifier", None) or getattr(user, "id", None)
