"""The send side of one websocket: a single writer over a bounded queue.

Not because concurrent sending is unsafe. It is not -- eight coroutines
writing fifty 200 KB frames each through one Litestar ``WebSocket`` on real
uvicorn deliver four hundred intact frames on every one of the four ws
implementations. The queue is here for three things concurrent ``send`` does
not give:

*One point of ordering.* A step upsert, the tokens that stream into it and
the ``ask.start`` that follows are issued by different coroutines and mean
nothing out of order. Interleaved ``await socket.send_text(...)`` calls are
ordered by whichever coroutine the loop happens to resume, which is not an
order anybody chose.

*A bounded backlog with an overflow policy you can see.* Without a queue,
every producer blocks on the TCP drain of the slowest client -- the app's
own coroutines are the buffer, and the buffer is unbounded. With one, the
backlog is a number with a limit and a name.

*One owner of the close.* A close frame that interleaves with a data frame
is a protocol error, and "everything I queued has reached the socket" is not
a question a caller can answer from outside. Both belong to whoever owns the
send side, so the writer performs the close itself.

What this module refuses to do
------------------------------

*Lose a frame quietly.* Litestar's own ``Subscriber`` is the anti-pattern:
``put_nowait`` returns ``False`` on a full queue (``channels/subscriber.py``
lines 51-57) and the plugin's ``_sub_worker`` throws the return value away
(``channels/plugin.py`` line 312). Every tag on this wire is a delta --
``step.update`` patches a bubble that must already exist, ``step.stream.token``
appends to it, ``element.remove`` addresses one by id -- and there is no ack,
so a client that misses one is wrong from then on and neither end can tell.
Silence is the one policy that cannot be recovered from.

*Wait for quiescence.* ``drain`` pushes a fence and waits for that one
object, so it means "everything issued before the call reached the socket",
not "the queue is empty" -- which a streaming session never is. This is the
same shape, and the same reason, as ``persistence.writer.SessionWriter.drain``.

*Die with the socket.* A Chainlit session outlives its connection. See
``attach``.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Final, Optional, Union

from litestar.status_codes import WS_1000_NORMAL_CLOSURE

from chainlit.logger import logger
from chainlit.protocol.codec import MAX_FRAME_BYTES, CloseCode, encode_server

if TYPE_CHECKING:
    from litestar import WebSocket

    from chainlit.protocol.server import ServerMsg

__all__ = [
    "DEFAULT_DRAIN_TIMEOUT",
    "DEFAULT_MAX_BACKLOG",
    "FORCE_CLOSE_GRACE",
    "Outbound",
    "Overflow",
]


DEFAULT_MAX_BACKLOG: Final[int] = 1024
"""Frames one connection may fall behind by before the policy fires.

Counted in frames, not bytes, because the writer encodes -- a queued
``ServerMsg`` has no size yet -- and because the thing that actually piles up
is ``step.stream.token``. One frame per token means 1024 is about one long
completion: the largest burst a healthy client absorbs in a single breath, so
a smaller bound would fire the policy on ordinary streaming. Much larger and
a wedged connection holds tens of megabytes hostage while the view it is
holding is minutes stale and the resume path would rebuild it in one frame.

The worst case is not the typical one: ``MAX_FRAME_BYTES`` is 8 MiB, so 1024
*maximal* frames would be 8 GiB. Nothing queues a thousand thread snapshots
-- the bound is sized for the burst that really happens -- but an embedder
whose app sends large elements in bulk should lower it, which is why it is a
constructor argument and not a constant baked into the loop.
"""

DEFAULT_DRAIN_TIMEOUT: Final[float] = 10.0
"""What a caller waiting for the socket to catch up is willing to spend.
Matches ``persistence.writer.DRAIN_TIMEOUT``: the two are usually awaited by
the same handler, on the same shutdown."""

FORCE_CLOSE_GRACE: Final[float] = 1.0
"""How long a writer gets to answer an abort before it is cancelled outright,
and then how long the close frame gets to reach a socket that is already not
draining. A writer parked in ``send_text`` on a peer that stopped reading
does not come back on its own -- so a close built only on a flag it will read
"next time round the loop" is a close that never happens on precisely the
connections that need one."""


@dataclass(frozen=True, slots=True)
class Overflow:
    """The moment the backlog bound was hit. Handed to ``on_overflow``."""

    tag: str
    """The ``t`` of the frame that did not fit."""

    backlog: int
    """Frames waiting when it was refused."""

    dropped: int
    """Frames this queue has refused in total, including this one."""


# --------------------------------------------------------------------------
# Queue items
# --------------------------------------------------------------------------


class _Control:
    """A marker travelling in the queue rather than a frame on the wire.

    Control items are exempt from the backlog bound. A fence that could be
    refused would make ``drain`` unusable on exactly the connection it exists
    to describe, and a close that could be refused would leave the socket
    open forever at the one moment it must not be.
    """

    __slots__ = ()


class _Fence(_Control):
    """Resolves once everything queued in front of it has reached the socket."""

    __slots__ = ("future",)

    def __init__(self, future: "asyncio.Future[bool]") -> None:
        self.future = future

    def resolve(self, reached: bool) -> None:
        if not self.future.done():
            self.future.set_result(reached)


class _Close(_Control):
    """Close the socket once everything queued in front of it has been sent."""

    __slots__ = ("code", "reason")

    def __init__(self, code: int, reason: str) -> None:
        self.code = code
        self.reason = reason


# Spelled with the two concrete controls rather than their base: the
# writer narrows by `isinstance` against each, and a bare `_Control`
# left in the union survives both checks, so the frame that reaches
# `encode_server` is not a `ServerMsg` as far as the type checker is
# concerned.
_Item = Union["ServerMsg", _Fence, _Close]


# --------------------------------------------------------------------------
# The queue
# --------------------------------------------------------------------------


class Outbound:
    """The outbound half of one session's websocket.

    Lifetime
    --------
    This object belongs to the **session**; the writer task belongs to the
    **socket**. Binding the whole thing to the connection -- the shape a
    ``yield`` dependency's teardown gives you for free -- would throw the
    backlog away on every blip, and a Chainlit session is deliberately
    survivable: it holds a live ask, a running task, an answer parked on the
    handshake gate. So ``attach`` starts a writer, ``detach`` stops it, and
    what has not been sent stays queued for the next one. A frame that was
    mid-``send_text`` when the socket died stays at the head of the queue and
    is sent again by the next writer: a duplicate is harmless here (upserts
    and patches are idempotent, and ``step.stream.token`` losses are not),
    a hole is not.

    That retention is right for a reconnect that resumes where it left off
    and wrong for one that replays a snapshot -- ``thread.resume`` *replaces*
    the client's feed, so deltas queued before it are describing a feed that
    no longer exists. Call ``discard`` before queueing a snapshot.

    Close
    -----
    ``close`` is graceful: it goes through the queue, so everything already
    issued is on the wire before the close frame is. ``abort`` is not: it
    drops the backlog and closes now, which is what a policy failure wants.
    Either way the *writer* performs it, so a close cannot interleave with a
    frame, and both are idempotent.

    With one deliberate exception, and it is the important one: a writer that
    has stopped draining cannot perform anything. An abort it has not
    answered within ``FORCE_CLOSE_GRACE`` cancels it and closes the socket
    from ``abort``'s own task -- still a single owner, because the writer is
    gone before the close is attempted. Both ``close`` and ``abort`` are
    therefore total: they always end with ``closed`` true, even when the
    close frame itself could not be delivered.

    Close codes come from ``protocol.codec.CloseCode``. This object never
    raises ``WebSocketException`` -- that is the handler's idiom, and the one
    that works: Litestar's websocket exception path is a ``@staticmethod``
    that reads ``exc.code`` off a ``WebSocketException`` and closes 4500 for
    anything else, without ever consulting ``exception_handlers``
    (``middleware/_internal/exceptions/middleware.py`` lines 212-231). Since
    the writer is a task and not the handler, an exception raised in it would
    reach no middleware at all; it closes the socket itself instead.
    """

    def __init__(
        self,
        *,
        max_backlog: int = DEFAULT_MAX_BACKLOG,
        on_overflow: Optional[Callable[[Overflow], None]] = None,
        name: str = "",
    ) -> None:
        if max_backlog < 1:
            raise ValueError("max_backlog must be at least 1")
        self._max_backlog = max_backlog
        self._on_overflow = on_overflow
        self._name = name

        self._items: deque[_Item] = deque()
        self._pending = 0
        self._wake = asyncio.Event()

        self._socket: Optional["WebSocket[Any, Any, Any]"] = None
        self._writer: Optional["asyncio.Task[None]"] = None
        self._closer: Optional["asyncio.Task[None]"] = None

        self._closed = False
        self._closed_event = asyncio.Event()
        self._close_code: Optional[int] = None
        self._close_reason = ""
        self._fatal: Optional[tuple[int, str]] = None

        self._sent = 0
        self._dropped = 0
        self._overflowed = False

    def __repr__(self) -> str:
        return (
            f"<Outbound {self._name!r} backlog={self._pending} sent={self._sent} "
            f"dropped={self._dropped} closed={self._closed}>"
        )

    # ------------------------------------------------------------ observation

    @property
    def backlog(self) -> int:
        """Frames waiting to be written, including one being written now."""
        return self._pending

    @property
    def sent(self) -> int:
        """Frames handed to the socket."""
        return self._sent

    @property
    def dropped(self) -> int:
        """Frames ``send`` refused -- the backlog was full, or it was closed.

        The counter is the point. A queue whose overflow is only a ``False``
        return value is a queue whose overflow nobody notices.
        """
        return self._dropped

    @property
    def overflowed(self) -> bool:
        """Whether the backlog bound was ever hit."""
        return self._overflowed

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def close_code(self) -> Optional[int]:
        """The code the socket was closed with, or ``None`` while it is open."""
        return self._close_code

    @property
    def close_reason(self) -> str:
        return self._close_reason

    @property
    def attached(self) -> bool:
        return self._writer is not None

    # -------------------------------------------------------------- producers

    def send(self, msg: "ServerMsg") -> bool:
        """Queue one frame. Never blocks, never awaits, never raises.

        Returns ``False`` if the frame was refused, which happens when the
        queue is already closed or when the backlog is full. A full backlog
        is not a dropped frame -- it *closes the connection*
        (``CloseCode.INTERNAL``), because a client that missed a delta on this
        wire cannot be repaired in place, and reconnect-and-resume rebuilds it
        correctly in one frame. The refusal is counted in ``dropped``, logged,
        and passed to ``on_overflow``.

        ``INTERNAL`` is a policy close wearing the catch-all code: the
        protocol has no member for "the peer stopped reading". See the module
        report; adding one is a change to ``codec.CloseCode``, not to this
        file.
        """
        if self._closed or self._fatal is not None:
            self._dropped += 1
            logger.debug(
                "Outbound %s refused a %s frame: the connection is closing.",
                self._name,
                _tag_of(msg),
            )
            return False

        if self._pending >= self._max_backlog:
            self._dropped += 1
            self._overflowed = True
            logger.error(
                "Outbound %s overflowed at %d queued frames; refusing a %s and "
                "closing the connection. The client is not reading.",
                self._name,
                self._pending,
                _tag_of(msg),
            )
            event = Overflow(
                tag=_tag_of(msg), backlog=self._pending, dropped=self._dropped
            )
            self.abort(CloseCode.INTERNAL, "outbound backlog exceeded")
            self._notify_overflow(event)
            return False

        self._items.append(msg)
        self._pending += 1
        self._wake.set()
        return True

    def _notify_overflow(self, event: Overflow) -> None:
        if self._on_overflow is None:
            return
        try:
            self._on_overflow(event)
        except Exception:
            logger.warning(
                "Outbound %s overflow callback failed", self._name, exc_info=True
            )

    def discard(self) -> int:
        """Drop every frame still waiting; keep the fences and the close.

        For the reconnect that replays a snapshot rather than resuming: after
        ``thread.resume`` the client's feed is replaced, so the deltas queued
        against the old one are garbage. Returns how many were dropped; they
        are counted in ``dropped`` too, because they were.
        """
        if not self._pending:
            return 0
        kept: deque[_Item] = deque(
            item for item in self._items if isinstance(item, _Control)
        )
        count = self._pending
        self._items = kept
        self._pending = 0
        self._dropped += count
        logger.debug("Outbound %s discarded %d queued frame(s).", self._name, count)
        return count

    # ------------------------------------------------------------ writer task

    def attach(self, socket: "WebSocket[Any, Any, Any]") -> None:
        """Start the writer for this socket.

        One writer at a time, by construction: a second one would be the
        concurrent-send arrangement this class exists to replace, and the
        ordering guarantee would go with it.
        """
        if self._closed:
            raise RuntimeError("Outbound is closed")
        if self._writer is not None and not self._writer.done():
            raise RuntimeError("Outbound already has a writer")
        self._socket = socket
        self._writer = asyncio.create_task(
            self._run(socket), name=f"chainlit-outbound-{self._name or id(self)}"
        )
        self._wake.set()

    async def detach(self) -> None:
        """Stop the writer, keeping whatever it had not sent.

        Not a close: the queue stays usable and a later ``attach`` picks the
        backlog up where this one left it.
        """
        writer, self._writer, self._socket = self._writer, None, None
        if writer is None:
            return
        writer.cancel()
        try:
            await writer
        except asyncio.CancelledError:
            if _cancelled_from_outside():
                raise
        self._sweep_fences(reached=False)

    @asynccontextmanager
    async def attached_to(
        self, socket: "WebSocket[Any, Any, Any]"
    ) -> AsyncIterator["Outbound"]:
        """``attach`` for the life of the block, ``detach`` on the way out."""
        self.attach(socket)
        try:
            yield self
        finally:
            await self.detach()

    async def _run(self, socket: "WebSocket[Any, Any, Any]") -> None:
        try:
            await self._pump(socket)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A defect in the writer must close the socket loudly rather than
            # leave a session with a queue nothing is draining.
            logger.exception("Outbound %s writer failed", self._name)
            await self._shutdown(socket, CloseCode.INTERNAL, "outbound writer failed")
        finally:
            # However this writer ends -- close, socket loss, cancellation
            # from an enclosing task group -- nothing is draining the queue
            # any more, so no fence can still be honoured and the handle must
            # not keep an attached-looking queue from being re-attached.
            self._sweep_fences(reached=False)
            if self._writer is asyncio.current_task():
                self._writer = None
                self._socket = None

    async def _pump(self, socket: "WebSocket[Any, Any, Any]") -> None:
        while True:
            if self._fatal is not None:
                code, reason = self._fatal
                self._items = deque(
                    item for item in self._items if isinstance(item, _Fence)
                )
                self._pending = 0
                await self._shutdown(socket, code, reason)
                return

            if not self._items:
                self._wake.clear()
                if not self._items and self._fatal is None:
                    await self._wake.wait()
                continue

            # Peeked, not popped: a frame is only off the queue once the
            # socket has taken it, so a connection that dies mid-write leaves
            # the frame for the next writer instead of eating it.
            item = self._items[0]

            if isinstance(item, _Fence):
                self._items.popleft()
                item.resolve(True)
                continue

            if isinstance(item, _Close):
                self._items.popleft()
                await self._shutdown(socket, item.code, item.reason)
                return

            try:
                frame = encode_server(item)
            except Exception:
                # An unencodable message is a defect where it was built, not
                # a reason to take the client's connection away.
                self._items.popleft()
                self._pending -= 1
                self._dropped += 1
                logger.exception(
                    "Outbound %s could not encode a %s frame; dropping it.",
                    self._name,
                    _tag_of(item),
                )
                continue

            if len(frame) > MAX_FRAME_BYTES:
                self._items.popleft()
                self._pending -= 1
                self._dropped += 1
                logger.error(
                    "Outbound %s built a %s frame of %d bytes, over the %d byte "
                    "limit; closing.",
                    self._name,
                    _tag_of(item),
                    len(frame),
                    MAX_FRAME_BYTES,
                )
                await self._shutdown(
                    socket,
                    CloseCode.FRAME_TOO_LARGE,
                    f"frame of {len(frame)} bytes exceeds {MAX_FRAME_BYTES}",
                )
                return

            try:
                await socket.send_text(frame)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Deliberately broad. In-process this is Litestar's
                # WebSocketDisconnect; on uvicorn it is whatever the ws
                # implementation raises for a peer that went away, and the
                # set differs per implementation. Every one of them means the
                # same thing, and none of them may reach the handler -- an
                # exception escaping there closes 4500 and calls a client
                # hanging up an internal server error.
                logger.debug(
                    "Outbound %s lost its socket mid-frame (%s); %d frame(s) held.",
                    self._name,
                    type(exc).__name__,
                    self._pending,
                )
                self._sweep_fences(reached=False)
                return

            self._items.popleft()
            self._pending -= 1
            self._sent += 1

    # ------------------------------------------------------------------ fence

    async def drain(self, timeout: float = DEFAULT_DRAIN_TIMEOUT) -> bool:
        """Wait until everything queued before this call reached the socket.

        Returns whether it did. ``False`` means the deadline passed, the
        socket died first, or the queue was already closed -- in every case
        the caller has learned that the frames it cared about are not on the
        wire, which is the only thing it can act on.

        Frames queued *after* the call are not waited for. That is the point
        of the fence and the reason this is not ``queue.join()``: on a session
        that is streaming, "the queue is empty" is a state that never arrives,
        and a shutdown path that waited for it would hang.

        Returns immediately when no writer is attached. Nothing is trying to
        send the backlog, so waiting for it would burn the whole deadline to
        learn what is already known.
        """
        if self._closed or self._writer is None or self._writer.done():
            return not self._items and not self._closed
        if not self._items:
            return True

        loop = asyncio.get_running_loop()
        fence = _Fence(loop.create_future())
        self._items.append(fence)
        self._wake.set()
        try:
            return await asyncio.wait_for(asyncio.shield(fence.future), timeout)
        except TimeoutError:
            logger.warning(
                "Outbound %s did not drain within %ss; %d frame(s) still queued.",
                self._name,
                timeout,
                self._pending,
            )
            fence.resolve(False)
            return False

    def _sweep_fences(self, *, reached: bool) -> None:
        """Resolve every waiting fence and take it out of the queue.

        Called on every terminal transition and whenever the writer stops. A
        fence left behind is a ``drain`` blocked for its full deadline on a
        connection that is already gone.
        """
        remaining: deque[_Item] = deque()
        for item in self._items:
            if isinstance(item, _Fence):
                item.resolve(reached)
            else:
                remaining.append(item)
        self._items = remaining

    # ------------------------------------------------------------------ close

    def abort(self, code: int = CloseCode.INTERNAL, reason: str = "") -> None:
        """Close now, dropping whatever is queued. Synchronous, idempotent.

        For a failure that has already made the connection meaningless. The
        writer performs the close when it can; with no writer attached there
        is no socket to close, so the queue simply becomes closed and says
        why.

        The writer often *cannot*, and the overflow case is the proof: a
        backlog only fills because the peer stopped reading, which means the
        writer is parked inside ``send_text`` waiting for a window that is
        not coming. A flag it will read "next time round the loop" would
        never be read. So an abort that is not answered within
        ``FORCE_CLOSE_GRACE`` cancels the writer and sends the close itself
        -- safe precisely because the writer is the only other thing that
        touches the send side, and it is gone by then.
        """
        if self._closed or self._fatal is not None:
            return
        self._fatal = (int(code), reason)
        self._wake.set()
        if self._writer is None or self._writer.done():
            self._mark_closed(int(code), reason)
            return
        self._closer = asyncio.ensure_future(self._force(int(code), reason))

    async def _force(self, code: int, reason: str) -> None:
        """Make an abort terminal even when the writer cannot answer it."""
        if await self._wait_closed(FORCE_CLOSE_GRACE):
            return
        logger.warning(
            "Outbound %s writer did not answer the close in %ss; cancelling it "
            "and closing the socket directly.",
            self._name,
            FORCE_CLOSE_GRACE,
        )
        socket = self._socket
        await self._reap_writer()
        if socket is not None:
            try:
                await asyncio.wait_for(
                    socket.close(code=code, reason=reason), FORCE_CLOSE_GRACE
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A close frame queues behind the frames the peer is not
                # reading, so on a wedged connection it does not go out
                # either. Nothing further can be done from here: the socket
                # dies with the handler.
                logger.debug(
                    "Outbound %s could not deliver its close frame (%s).",
                    self._name,
                    type(exc).__name__,
                )
        self._mark_closed(code, reason)

    async def close(
        self,
        code: int = WS_1000_NORMAL_CLOSURE,
        reason: str = "",
        *,
        timeout: float = DEFAULT_DRAIN_TIMEOUT,
    ) -> None:
        """Flush what is queued, then close. Idempotent, and always terminal.

        The close travels through the queue, so it cannot overtake a frame or
        interleave with one. If the flush does not finish inside ``timeout``
        the close stops being graceful: the backlog is dropped and the writer
        gets ``FORCE_CLOSE_GRACE`` to emit the close frame before it is
        cancelled. A close that could hang would be a session that never ends.
        """
        if self._closed:
            return
        if self._writer is None or self._writer.done():
            # Nothing is draining the queue, so there is nothing to flush and
            # no socket of ours to close politely.
            await self._reap_writer()
            self._mark_closed(int(code), reason)
            return

        self._items.append(_Close(int(code), reason))
        self._wake.set()
        if await self._wait_closed(timeout):
            await self._reap_writer()
            return

        logger.warning(
            "Outbound %s did not flush within %ss; closing on %d queued frame(s).",
            self._name,
            timeout,
            self._pending,
        )
        self.abort(code, reason)
        await self._await_closer()
        await self._reap_writer()
        if not self._closed:  # pragma: no cover - _force always marks it
            self._mark_closed(int(code), reason)

    async def wait_closed(self) -> None:
        """Block until the socket has been closed by the writer."""
        await self._closed_event.wait()

    async def _await_closer(self) -> None:
        """Wait for the task ``abort`` spawned, if it spawned one."""
        closer, self._closer = self._closer, None
        if closer is None:
            return
        try:
            await closer
        except asyncio.CancelledError:
            if _cancelled_from_outside():
                raise
        except Exception:  # pragma: no cover - _force does not raise
            logger.warning("Outbound %s closer failed", self._name, exc_info=True)

    async def _wait_closed(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._closed_event.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def _reap_writer(self) -> None:
        writer, self._writer, self._socket = self._writer, None, None
        if writer is None:
            return
        writer.cancel()
        try:
            await writer
        except asyncio.CancelledError:
            if _cancelled_from_outside():
                raise
        except Exception:
            logger.warning("Outbound %s writer ended badly", self._name, exc_info=True)

    async def _shutdown(
        self, socket: "WebSocket[Any, Any, Any]", code: int, reason: str
    ) -> None:
        """Send the close frame. Only ever called from the writer."""
        self._mark_closed(int(code), reason)
        try:
            await socket.close(code=int(code), reason=reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(
                "Outbound %s could not send its close frame (%s).",
                self._name,
                type(exc).__name__,
            )

    def _mark_closed(self, code: int, reason: str) -> None:
        if not self._closed:
            self._closed = True
            self._close_code = code
            self._close_reason = reason
        self._sweep_fences(reached=False)
        self._pending = 0
        self._items.clear()
        self._closed_event.set()

    # ------------------------------------------------------- context manager

    async def __aenter__(self) -> "Outbound":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.close()


def _cancelled_from_outside() -> bool:
    """Whether *this* task is the one being cancelled, not the writer.

    ``await writer`` re-raises the writer's ``CancelledError`` in the awaiting
    task, which is indistinguishable from that task's own cancellation except
    by asking. Swallowing the wrong one would make ``detach`` uncancellable.
    """
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


def _tag_of(msg: Any) -> str:
    """The ``t`` of a server message, for a log line."""
    config = getattr(type(msg), "__struct_config__", None)
    tag = getattr(config, "tag", None)
    return str(tag) if tag is not None else type(msg).__name__
