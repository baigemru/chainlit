"""One ordered writer per session.

Three mechanisms do this job today and each has a defect.
``create_persist_task`` spawns an unbounded fan of tasks with *no order
between them* — which is why the step upsert has to be order-insensitive
through ``LEAST`` and a placeholder-refusing ``CASE``. ``queue_until_user_message``
holds pre-interaction writes in a queue *per method name*, so on flush every
deferred ``create_step`` replays before any ``create_element`` regardless of
the order they were issued in. ``wait_for_persist`` cannot know when the fan
is finished, so it re-snapshots for a bounded number of rounds and hopes.

This module collapses the three into one consumer per session: FIFO, one
transaction per batch, and a fence instead of a re-snapshot.

What it deliberately does not do
--------------------------------

*Wait for quiescence.* A batch is whatever is already in the queue when the
consumer wakes up; the writer never delays a write hoping more will arrive.

*Block a reader behind a still-streaming session.* ``drain`` enqueues a fence
and waits for that one object, so it means "everything issued before I was
called has landed" — not "the queue is empty", which an actively-streaming
session never is.

*Let a blob upload hold the queue.* An upload runs as its own task and
enqueues the element's row when it succeeds, so an unreachable bucket can
delay that one row and nothing else. The ordered queue is for database
writes; an upload is not one.

*Coalesce repeat writes of one row.* The consumer sends whole messages, never
a token at a time, so a batch holds at most one write per step and there is
nothing to fold. The fold that used to run here was built for a stream of
per-token updates and was the most intricate code in the module -- it had to
reproduce ``LEAST``/``CASE`` in Python to stay equivalent to the upsert --
and code that exists for a load profile nobody generates is a liability, not
a feature. The database-side ``LEAST``/``CASE`` stay: they are about two
writers racing, not about one writer's batch.

Restoring token streaming
-------------------------

If the emitter ever sends ``step.stream.token`` frames, every token becomes a
``SaveStep`` carrying a longer ``output``, and a 300-token message becomes 300
upserts of one row inside one transaction. Coalescing is what made that one
upsert. The checklist:

1. Restore the trio ``coalesce`` / ``merge_steps`` / ``merge_thread_patches``
   and the ``_stored_form`` helper, all from the commit that removed
   coalescing (``git log -S coalesce -- backend/chainlit/persistence/writer.py``).
   They fold repeat ``SaveStep``/``PatchThread`` ops of one row into the
   first one's position, treat ``DeleteStep`` as a barrier and never merge
   elements; ``merge_steps`` keeps the earliest ``start`` (compared in stored
   form) and refuses a placeholder ``type``, exactly as the upsert does.
2. Put the call back in ``SessionWriter._apply``, between splitting the
   fences out of the batch and calling ``_write``::

       ops = coalesce([item for item in batch if not isinstance(item, _Fence)])

3. Restore the tests from the same commit: the ``coalescing`` section of
   ``tests/persistence/test_writer.py``, in particular
   ``test_coalescing_is_equivalent_to_writing_each_fragment``, which is the
   property that licenses the fold at all.
4. The wire side already exists: ``StepStreamStart`` (``step.stream.start``)
   and ``StepStreamToken`` (``step.stream.token``) are defined in
   ``chainlit/protocol/server.py``. What is missing is a ``stream_token``
   method on the emitter facade that produces a ``StepStreamToken`` and
   submits the partial ``SaveStep`` -- there is no such method today because
   nothing streams.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Self,
    Sequence,
    Set,
    Tuple,
    Union,
)

from chainlit.logger import logger
from chainlit.persistence.config import Persistence, UnitOfWork
from chainlit.persistence.records import ElementRecord, StepRecord, ThreadPatch

# A batch is capped so one burst cannot hold a transaction open indefinitely.
BATCH_LIMIT = 256

# What a reader is willing to wait for. Matches the barrier this replaces.
DRAIN_TIMEOUT = 10.0

# The upload half of an element, run as its own task. It returns the record to
# write — the storage backend is what decides the row's ``url`` and
# ``objectKey``, so the record handed to ``submit_element`` is a starting
# point, not the final one. Returning None writes the submitted record as-is.
Upload = Callable[[], Awaitable[Optional[ElementRecord]]]


@dataclass(slots=True)
class SaveStep:
    """Create-or-update a step, writing only the fields it carries."""

    record: StepRecord


@dataclass(slots=True)
class DeleteStep:
    step_id: str


@dataclass(slots=True)
class SaveElement:
    record: ElementRecord


@dataclass(slots=True)
class DeleteElement:
    element_id: str
    thread_id: Optional[str] = None


@dataclass(slots=True)
class PatchThread:
    """Create-or-update the thread row; an empty patch is a touch."""

    thread_id: str
    patch: ThreadPatch = field(default_factory=ThreadPatch)


Op = Union[SaveStep, DeleteStep, SaveElement, DeleteElement, PatchThread]


@dataclass(slots=True)
class _HeldUpload:
    """An element whose upload has not been started because the gate is shut.

    The *callable* is held rather than a coroutine, so a session abandoned
    before its first interaction uploads nothing at all — matching
    ``queue_until_user_message``, which queued the arguments and never built
    the coroutine either. A started upload would leave an orphan object in the
    bucket that no row will ever point at.
    """

    record: ElementRecord
    upload: Upload


class _Fence:
    """A marker that resolves once everything queued before it has run."""

    __slots__ = ("future",)

    def __init__(self, future: "asyncio.Future[None]") -> None:
        self.future = future

    def resolve(self) -> None:
        if not self.future.done():
            self.future.set_result(None)


class SessionWriter:
    """The ordered writer for one session's thread.

    ``hold_until_interaction`` is the gate: while it is shut, ops accumulate
    in one ordered list and are released, in issue order, behind whatever
    prelude ``open_gate`` is given. A session that closes without ever
    interacting *discards* them, which is what happens today when the
    session's queues die with it — so the discarding mode is opt-in, and a
    plain ``SessionWriter(...)`` writes what it is given.
    """

    def __init__(
        self,
        persistence: Persistence,
        thread_id: str,
        *,
        registry: "WriterRegistry",
        hold_until_interaction: bool = False,
        batch_limit: int = BATCH_LIMIT,
    ) -> None:
        self.persistence = persistence
        self.thread_id = thread_id
        self.registry = registry
        self._queue: "asyncio.Queue[Union[Op, _Fence]]" = asyncio.Queue()
        self._held: List[Union[Op, _HeldUpload]] = []
        self._gate_open = not hold_until_interaction
        self._batch_limit = batch_limit
        self._uploads: Set["asyncio.Task[None]"] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional["asyncio.Task[None]"] = None
        self._closing = False
        self._closed = False

    # ------------------------------------------------------------------ state

    @property
    def gate_open(self) -> bool:
        return self._gate_open

    @property
    def held(self) -> Tuple[Union[Op, _HeldUpload], ...]:
        """What is waiting for the gate, in issue order. For tests."""
        return tuple(self._held)

    # ---------------------------------------------------------------- lifetime

    def start(self) -> Self:
        if self._task is None:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._consume())
            self.registry.register(self)
        return self

    async def aclose(self, *, timeout: float = DRAIN_TIMEOUT) -> None:
        """Flush what was issued, then stop.

        A session that never reached its first interaction has nothing to
        flush: its held ops describe a conversation that was abandoned before
        it began, and today they die with the session's queues. Nothing was
        uploaded for them either — the gate holds the upload, not a coroutine
        already in flight.

        The writer leaves the registry only once the flush is over. Dropping
        out of it first would make ``drain_thread`` report "nothing pending"
        during the exact window where the writes are still being committed.

        It stops accepting writes only then, too. An upload finishing *during*
        the final drain still has a row to enqueue, and closing to submissions
        first would drop exactly the write this method is here to flush.
        """
        if self._closing:
            return
        self._closing = True

        try:
            if self._gate_open:
                await self.drain(timeout=timeout)
            elif self._held:
                logger.debug(
                    "Session writer for thread %s closed before the first "
                    "interaction; discarding %d held write(s).",
                    self.thread_id,
                    len(self._held),
                )
        finally:
            self._closed = True
            self.registry.unregister(self)
            self._held.clear()
            for upload in tuple(self._uploads):
                upload.cancel()
            self._uploads.clear()
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None

    async def __aenter__(self) -> Self:
        return self.start()

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------- submission

    def submit(self, op: Op) -> None:
        """Queue one write. Never blocks, never raises, never awaits."""
        if self._closed:
            logger.warning(
                "Dropping a %s issued after the session writer for thread %s closed.",
                type(op).__name__,
                self.thread_id,
            )
            return
        if self._gate_open:
            self._queue.put_nowait(op)
        else:
            self._held.append(op)

    def submit_threadsafe(self, op: Op) -> None:
        """Queue one write from a thread that is not running the event loop.

        Integrations call back from their own threads — llama_index does, and
        says so where it works around the current helper. The queue is not
        thread-safe, so those callers have to hop the loop rather than touch
        it directly.
        """
        loop = self._loop
        if loop is None:
            logger.warning(
                "Dropping a %s submitted to an unstarted session writer for thread %s.",
                type(op).__name__,
                self.thread_id,
            )
            return
        loop.call_soon_threadsafe(self.submit, op)

    def submit_element(
        self, record: ElementRecord, upload: Optional[Upload] = None
    ) -> None:
        """Queue an element, uploading its blob first if there is one.

        The upload is a *callable*: nothing starts until the writer decides
        the element is going to be written at all. It runs as its own task,
        so an unreachable bucket delays this row and no other, and the row is
        written from whatever record the upload returns — the storage backend
        is what settles ``url`` and ``objectKey``.
        """
        if upload is None:
            self.submit(SaveElement(record))
            return
        if self._closed:
            logger.warning(
                "Dropping an element issued after the session writer for "
                "thread %s closed; its blob is not uploaded.",
                self.thread_id,
            )
            return
        if self._gate_open:
            self._start_upload(record, upload)
        else:
            self._held.append(_HeldUpload(record, upload))

    def _start_upload(self, record: ElementRecord, upload: Upload) -> None:
        task = asyncio.ensure_future(self._upload_then_write(record, upload))
        self._uploads.add(task)
        task.add_done_callback(self._uploads.discard)

    async def _upload_then_write(self, record: ElementRecord, upload: Upload) -> None:
        try:
            written = await upload()
        except asyncio.CancelledError:
            raise
        except Exception:
            # No row: an element whose blob is not in the bucket points at
            # nothing. This is the invariant the legacy create_element got
            # from doing the upload and the insert in one coroutine.
            logger.warning(
                "Upload for element %s failed; the element row is not written.",
                record.id,
                exc_info=True,
            )
            return
        self.submit(SaveElement(written if written is not None else record))

    def open_gate(self, prelude: Optional[Op] = None) -> None:
        """Release the held writes, behind ``prelude`` if one is given.

        The prelude is how the thread row gets named and attributed before
        anything hangs off it; passing it here rather than submitting it
        separately is what guarantees it goes first.
        """
        if self._gate_open:
            if prelude is not None:
                self._queue.put_nowait(prelude)
            return
        self._gate_open = True
        if prelude is not None:
            self._queue.put_nowait(prelude)
        held, self._held = self._held, []
        for entry in held:
            if isinstance(entry, _HeldUpload):
                self._start_upload(entry.record, entry.upload)
            else:
                self._queue.put_nowait(entry)

    # ------------------------------------------------------------------ fence

    async def drain(self, timeout: float = DRAIN_TIMEOUT) -> None:
        """Wait until everything issued so far has been written.

        Two steps, because an upload enqueues its row when it finishes: first
        the uploads outstanding right now, then a fence behind the rows they
        just queued. Both are covered by one deadline.

        Repeated until no upload is outstanding, not done once: submissions
        keep being accepted while this runs -- ``aclose`` says so deliberately
        -- so an upload starting after the first snapshot would upload its
        blob and then be cancelled before enqueueing the row that points at
        it, leaving the blob orphaned in the bucket. That is the one invariant
        this class exists to hold.

        Bounded and silent on failure: a reader that cannot get a clean
        barrier proceeds with a possibly-incomplete read, which is what it did
        before any barrier existed.

        Returns immediately while the gate is shut. Held writes are not
        pending work — nothing is trying to write them — and treating them as
        such would stall every reader on a fresh session for the full timeout.
        """
        if not self._gate_open or self._task is None or self._task.done():
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            while True:
                uploads = tuple(self._uploads)
                if uploads:
                    _done, pending = await asyncio.wait(
                        uploads, timeout=max(0.0, deadline - loop.time())
                    )
                    if pending:
                        logger.warning(
                            "Timed out waiting for %d upload(s) of thread %s; "
                            "reading anyway.",
                            len(pending),
                            self.thread_id,
                        )
                fence = loop.create_future()
                self._queue.put_nowait(_Fence(fence))
                await asyncio.wait_for(
                    asyncio.shield(fence), max(0.0, deadline - loop.time())
                )
                if not self._uploads:
                    return
                if loop.time() >= deadline:
                    raise TimeoutError
        except TimeoutError:
            logger.warning(
                "Timed out after %ss waiting for the pending writes of thread "
                "%s; reading anyway.",
                timeout,
                self.thread_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Failed waiting for the pending writes of thread %s",
                self.thread_id,
                exc_info=True,
            )

    # --------------------------------------------------------------- consumer

    async def _consume(self) -> None:
        while True:
            first = await self._queue.get()
            batch: List[Union[Op, _Fence]] = [first]
            while len(batch) < self._batch_limit:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            try:
                await self._apply(batch)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The consumer outlives any single failure: dying here would
                # silently stop persisting for the rest of the session.
                logger.warning(
                    "Session writer for thread %s failed a batch",
                    self.thread_id,
                    exc_info=True,
                )
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def _apply(self, batch: Sequence[Union[Op, _Fence]]) -> None:
        fences = [item for item in batch if isinstance(item, _Fence)]
        try:
            ops = [item for item in batch if not isinstance(item, _Fence)]
            if ops:
                await self._write(ops)
        finally:
            for fence in fences:
                fence.resolve()

    async def _write(self, ops: Sequence[Op]) -> None:
        """One transaction for the batch, falling back to one per op.

        One transaction, so a burst of writes costs one round of commit
        overhead rather than one per op -- but it also means a single
        rejected write would roll back every innocent write beside it. On
        failure the batch is replayed op by op so the damage is confined to
        the op that actually caused it.
        """
        try:
            async with self.persistence.uow() as uow:
                for op in ops:
                    await self._dispatch(uow, op)
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Batch of %d write(s) for thread %s failed; replaying individually.",
                len(ops),
                self.thread_id,
                exc_info=True,
            )

        for op in ops:
            try:
                async with self.persistence.uow() as uow:
                    await self._dispatch(uow, op)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Dropping a %s for thread %s that could not be written",
                    type(op).__name__,
                    self.thread_id,
                    exc_info=True,
                )

    async def _dispatch(self, uow: UnitOfWork, op: Op) -> None:
        if isinstance(op, SaveStep):
            await uow.steps.save(op.record)
        elif isinstance(op, DeleteStep):
            await uow.steps.remove(op.step_id)
        elif isinstance(op, SaveElement):
            await uow.elements.save(op.record)
        elif isinstance(op, DeleteElement):
            await uow.elements.remove(op.element_id, op.thread_id)
        elif isinstance(op, PatchThread):
            await uow.threads.patch(op.thread_id, op.patch)
        else:  # pragma: no cover - the union is closed
            raise TypeError(f"Unknown write op: {op!r}")


# --------------------------------------------------------------------- registry


class WriterRegistry:
    """The live writers, keyed by thread.

    An object rather than a module global so an application can own one. In
    Litestar that means an instance on ``app.state``, seeded by a lifespan
    context manager registered from a plugin's ``on_app_init`` -- the same way
    advanced_alchemy owns its engine.

    One ordering trap to carry into that wiring: the shutdown drain must NOT
    go in ``on_shutdown``. Litestar pushes those hooks onto its AsyncExitStack
    first, so they unwind *last* -- after every lifespan manager, including the
    SQLAlchemy plugin's, whose exit disposes the engine. A drain there would
    run against a disposed engine. Register this registry's lifespan after the
    database plugin's and let LIFO put the drain before the disposal.
    """

    def __init__(self) -> None:
        self._by_thread: Dict[str, Set["SessionWriter"]] = {}

    def register(self, writer: "SessionWriter") -> None:
        self._by_thread.setdefault(writer.thread_id, set()).add(writer)

    def unregister(self, writer: "SessionWriter") -> None:
        bucket = self._by_thread.get(writer.thread_id)
        if bucket is None:
            return
        bucket.discard(writer)
        if not bucket:
            self._by_thread.pop(writer.thread_id, None)

    def writers_for(self, thread_id: str) -> Tuple["SessionWriter", ...]:
        return tuple(self._by_thread.get(thread_id, ()))

    @property
    def live(self) -> Tuple["SessionWriter", ...]:
        return tuple(writer for bucket in self._by_thread.values() for writer in bucket)

    async def drain_thread(
        self, thread_id: Optional[str], timeout: float = DRAIN_TIMEOUT
    ) -> None:
        """Wait for every writer on this thread.

        Keyed by thread rather than by session because the readers are: an
        HTTP handler asked for a thread has no session to look a writer up by,
        and two tabs on one thread are two writers whose work a single read
        has to see.
        """
        if not thread_id:
            return
        writers = self.writers_for(thread_id)
        if not writers:
            return
        await asyncio.gather(*(writer.drain(timeout) for writer in writers))

    async def aclose(self, *, timeout: float = DRAIN_TIMEOUT) -> None:
        """Flush and stop every writer still live.

        The shutdown path, which did not exist while the registry was a
        module global: a process exiting with live writers lost whatever they
        had buffered, and nothing was in a position to notice.
        """
        writers = self.live
        if not writers:
            return
        await asyncio.gather(
            *(writer.aclose(timeout=timeout) for writer in writers),
            return_exceptions=True,
        )
