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

*Serialise blob uploads.* An element's upload starts at submit time and runs
concurrently with everything else; only the element's own row waits for it.
The barrier therefore costs the *slowest* upload, not the sum of them, while
the row still lands in issue order and never before its object exists.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Dict,
    List,
    Optional,
    Self,
    Sequence,
    Set,
    Tuple,
    Union,
)

import msgspec
from msgspec import UNSET

from chainlit.logger import logger
from chainlit.persistence.config import Persistence, UnitOfWork
from chainlit.persistence.records import ElementRecord, StepRecord, ThreadPatch
from chainlit.persistence.statements import PLACEHOLDER_STEP_TYPE

# A batch is capped so one burst cannot hold a transaction open indefinitely.
BATCH_LIMIT = 256

# What a reader is willing to wait for. Matches the barrier this replaces.
DRAIN_TIMEOUT = 10.0

# An upload that never finishes must not pin the writer forever; past this the
# element is dropped, exactly as a raising upload is.
UPLOAD_TIMEOUT = 60.0


@dataclass(slots=True)
class SaveStep:
    """Create-or-update a step, writing only the fields it carries."""

    record: StepRecord


@dataclass(slots=True)
class DeleteStep:
    step_id: str


@dataclass(slots=True)
class SaveElement:
    """Write an element row, optionally gated on its blob upload.

    ``upload`` is a task already in flight. The row is written once it
    succeeds and skipped entirely if it does not — the invariant the legacy
    ``create_element`` got from doing both in one coroutine.
    """

    record: ElementRecord
    upload: Optional["asyncio.Task[Any]"] = None


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


class _Fence:
    """A marker that resolves once everything queued before it has run."""

    __slots__ = ("future",)

    def __init__(self, future: "asyncio.Future[None]") -> None:
        self.future = future

    def resolve(self) -> None:
        if not self.future.done():
            self.future.set_result(None)


def merge_steps(earlier: StepRecord, later: StepRecord) -> StepRecord:
    """Fold two writes of one step into the write they are equivalent to.

    Equivalent, not merely similar: the two columns the upsert does not write
    straight through are folded the same way the database folds them, so
    coalescing can never change what ends up stored.

    * ``start`` keeps the earlier of the two. The column is TEXT holding
      fixed-width ISO, so ``min`` here and ``LEAST`` there compare identically.
    * ``type`` refuses a placeholder over a settled type.
    * everything else is last-write-wins, and a field left ``UNSET`` says
      nothing at all — which is the whole reason the record type can serve
      both creation and update.
    """
    merged = msgspec.structs.replace(earlier)
    for info in msgspec.structs.fields(StepRecord):
        value = getattr(later, info.name)
        if value is UNSET:
            continue
        if info.name == "start":
            prior = earlier.start
            if isinstance(prior, str) and isinstance(value, str):
                value = min(prior, value)
        elif info.name == "type":
            if value == PLACEHOLDER_STEP_TYPE and earlier.type != PLACEHOLDER_STEP_TYPE:
                continue
        setattr(merged, info.name, value)
    return merged


def merge_thread_patches(earlier: ThreadPatch, later: ThreadPatch) -> ThreadPatch:
    """Fold two thread patches. Metadata merges per key, as the database does."""
    merged = msgspec.structs.replace(earlier)
    for info in msgspec.structs.fields(ThreadPatch):
        value = getattr(later, info.name)
        if value is UNSET:
            continue
        if info.name == "metadata" and isinstance(earlier.metadata, dict):
            value = {**earlier.metadata, **value}
        setattr(merged, info.name, value)
    return merged


def coalesce(ops: Sequence[Op]) -> List[Op]:
    """Collapse repeat writes of one row, keeping the first one's position.

    A streaming message issues an update per token; without this each is a
    row in its own right in the batch. The merged write stays where the first
    fragment was, so nothing overtakes anything it was issued behind.

    A delete is a barrier: writes after it describe a row that has to be
    created again, and merging across it would resurrect the deleted state.
    Elements are never merged — an element carries an upload task, and two of
    them are two different objects that both have to land.
    """
    result: List[Op] = []
    steps: Dict[str, int] = {}
    threads: Dict[str, int] = {}

    for op in ops:
        if isinstance(op, SaveStep):
            position = steps.get(op.record.id)
            if position is not None:
                prior = result[position]
                assert isinstance(prior, SaveStep)
                result[position] = SaveStep(merge_steps(prior.record, op.record))
                continue
            steps[op.record.id] = len(result)
        elif isinstance(op, DeleteStep):
            steps.pop(op.step_id, None)
        elif isinstance(op, PatchThread):
            position = threads.get(op.thread_id)
            if position is not None:
                prior = result[position]
                assert isinstance(prior, PatchThread)
                result[position] = PatchThread(
                    op.thread_id, merge_thread_patches(prior.patch, op.patch)
                )
                continue
            threads[op.thread_id] = len(result)
        result.append(op)

    return result


class SessionWriter:
    """The ordered writer for one session's thread.

    Ops issued before the first user interaction are *held*, not queued: they
    are released in issue order when the gate opens, behind whatever prelude
    the caller passes. A session that closes without ever interacting drops
    them, which is what happens today when the session's queues die with it.
    """

    def __init__(
        self,
        persistence: Persistence,
        thread_id: str,
        *,
        gate_open: bool = False,
        batch_limit: int = BATCH_LIMIT,
        upload_timeout: float = UPLOAD_TIMEOUT,
    ) -> None:
        self.persistence = persistence
        self.thread_id = thread_id
        self._queue: "asyncio.Queue[Union[Op, _Fence]]" = asyncio.Queue()
        self._held: List[Op] = []
        self._gate_open = gate_open
        self._batch_limit = batch_limit
        self._upload_timeout = upload_timeout
        self._task: Optional["asyncio.Task[None]"] = None
        self._closed = False

    # ------------------------------------------------------------------ state

    @property
    def gate_open(self) -> bool:
        return self._gate_open

    @property
    def held(self) -> Sequence[Op]:
        """The ops waiting for the gate, in issue order. For tests."""
        return tuple(self._held)

    # ---------------------------------------------------------------- lifetime

    def start(self) -> Self:
        if self._task is None:
            self._task = asyncio.create_task(self._consume())
            register(self)
        return self

    async def aclose(self, *, timeout: float = DRAIN_TIMEOUT) -> None:
        """Flush what was issued, then stop.

        A session that never reached its first interaction has nothing to
        flush: its held ops describe a conversation that was abandoned before
        it began, and today they die with the session's queues.
        """
        if self._closed:
            return
        self._closed = True
        unregister(self)

        if self._gate_open:
            await self.drain(timeout=timeout)
        elif self._held:
            logger.debug(
                "Session writer for thread %s closed before the first "
                "interaction; discarding %d held write(s).",
                self.thread_id,
                len(self._held),
            )
        self._held.clear()

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

    def submit_element(
        self, record: ElementRecord, upload: Optional[Awaitable[Any]] = None
    ) -> None:
        """Queue an element, starting its upload immediately.

        The upload runs concurrently from this moment; the row keeps its place
        in the queue and is written only once the object exists.
        """
        task = asyncio.ensure_future(upload) if upload is not None else None
        self.submit(SaveElement(record, task))

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
        for op in self._held:
            self._queue.put_nowait(op)
        self._held.clear()

    # ------------------------------------------------------------------ fence

    async def drain(self, timeout: float = DRAIN_TIMEOUT) -> None:
        """Wait until everything issued so far has been written.

        Bounded and silent on failure: a reader that cannot get a clean
        barrier proceeds with a possibly-incomplete read, which is what it did
        before any barrier existed.

        Returns immediately while the gate is shut. Held writes are not
        pending work — nothing is trying to write them — and treating them as
        such would stall every reader on a fresh session for the full timeout.
        """
        if not self._gate_open or self._task is None or self._task.done():
            return
        fence = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(_Fence(fence))
        try:
            await asyncio.wait_for(asyncio.shield(fence), timeout)
        except TimeoutError:
            logger.warning(
                "Timed out after %ss waiting for the pending writes of thread "
                "%s; reading anyway.",
                timeout,
                self.thread_id,
            )
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
            ops = coalesce([item for item in batch if not isinstance(item, _Fence)])
            ops = await self._settle_uploads(ops)
            if ops:
                await self._write(ops)
        finally:
            for fence in fences:
                fence.resolve()

    async def _write(self, ops: Sequence[Op]) -> None:
        """One transaction for the batch, falling back to one per op.

        Batching is what makes a streaming message cheap, but it also means a
        single rejected write would roll back every innocent write beside it.
        On failure the batch is replayed op by op so the damage is confined to
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

    async def _settle_uploads(self, ops: Sequence[Op]) -> List[Op]:
        """Wait for this batch's uploads, dropping the elements that failed.

        The uploads have been running since submit, so this waits for the
        slowest of them rather than for their sum, and it happens before the
        transaction opens rather than inside it.
        """
        pending = [
            op.upload
            for op in ops
            if isinstance(op, SaveElement) and op.upload is not None
        ]
        if not pending:
            return list(ops)

        await asyncio.wait(pending, timeout=self._upload_timeout)

        kept: List[Op] = []
        for op in ops:
            if isinstance(op, SaveElement) and op.upload is not None:
                if not op.upload.done():
                    op.upload.cancel()
                    logger.warning(
                        "Upload for element %s timed out after %ss; the "
                        "element row is not written.",
                        op.record.id,
                        self._upload_timeout,
                    )
                    continue
                error = None if op.upload.cancelled() else op.upload.exception()
                if op.upload.cancelled() or error is not None:
                    logger.warning(
                        "Upload for element %s failed (%r); the element row "
                        "is not written.",
                        op.record.id,
                        error,
                    )
                    continue
            kept.append(op)
        return kept


# --------------------------------------------------------------------- registry

_writers: Dict[str, Set[SessionWriter]] = {}


def register(writer: SessionWriter) -> None:
    _writers.setdefault(writer.thread_id, set()).add(writer)


def unregister(writer: SessionWriter) -> None:
    bucket = _writers.get(writer.thread_id)
    if bucket is None:
        return
    bucket.discard(writer)
    if not bucket:
        _writers.pop(writer.thread_id, None)


def writers_for(thread_id: str) -> Tuple[SessionWriter, ...]:
    return tuple(_writers.get(thread_id, ()))


async def drain_thread(
    thread_id: Optional[str], timeout: float = DRAIN_TIMEOUT
) -> None:
    """Wait for every writer on this thread.

    Keyed by thread rather than by session because the readers are: an HTTP
    handler asked for a thread has no session to look a writer up by, and two
    tabs on one thread are two writers whose work a single read has to see.
    """
    if not thread_id:
        return
    writers = writers_for(thread_id)
    if not writers:
        return
    await asyncio.gather(*(writer.drain(timeout) for writer in writers))
