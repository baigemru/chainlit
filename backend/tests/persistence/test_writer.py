"""The ordered writer: order, the gate, the fence, uploads.

These run against the real database on both dialects, because the properties
under test are properties of what ends up stored — an in-memory double would
only prove the writer agrees with itself. What the double *is* used for is
observing dispatch order, by recording each op as it is applied.
"""

import asyncio
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

import msgspec
import pytest
from sqlalchemy import select

from chainlit.persistence import Persistence, UnitOfWork
from chainlit.persistence.models import ELEMENTS
from chainlit.persistence.records import ElementRecord, StepRecord, ThreadPatch
from chainlit.persistence.services import to_uuid
from chainlit.persistence.storage.base import BaseStorageClient
from chainlit.persistence.writer import (
    DeleteElement,
    DeleteStep,
    Op,
    PatchThread,
    SaveElement,
    SaveStep,
    SessionWriter,
    Upload,
    WriterRegistry,
)

from .conftest import new_id


class Recorder(SessionWriter):
    """A writer that remembers, in order, every op it actually applied."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.applied: List[Op] = []

    async def _dispatch(self, uow: UnitOfWork, op: Op) -> Sequence[str]:
        orphaned = await super()._dispatch(uow, op)
        self.applied.append(op)
        # Forwarded, not swallowed: the orphaned blobs of a delete travel
        # back through this return, and a recorder that dropped them would
        # make every blob-delete test pass against a writer that deletes
        # nothing.
        return orphaned


@pytest.fixture
def thread_id() -> str:
    return new_id()


@pytest.fixture
def registry() -> WriterRegistry:
    """One registry per test.

    There is no default one to fall into: a writer joins the registry it is
    handed, and a test sharing a process-wide one with its neighbours would
    be asserting about their writers as much as its own.
    """
    return WriterRegistry()


@pytest.fixture
async def writer(persistence: Persistence, registry: WriterRegistry, thread_id: str):
    """An open writer, started, and closed at the end of the test."""
    instance = Recorder(persistence, thread_id, registry=registry).start()
    yield instance
    await instance.aclose(timeout=5.0)


def step(thread_id: str, step_id: Optional[str] = None, **fields: Any) -> StepRecord:
    fields.setdefault("type", "user_message")
    return StepRecord(id=step_id or new_id(), thread_id=thread_id, **fields)


def element(thread_id: str, for_id: str, element_id: Optional[str] = None):
    return ElementRecord(
        id=element_id or new_id(),
        name="attachment.png",
        type="image",
        thread_id=thread_id,
        for_id=for_id,
    )


async def rows(uow: UnitOfWork, table: str) -> List[Dict[str, Any]]:
    from sqlalchemy import select

    from chainlit.persistence import models

    columns = getattr(models, table)
    result = await uow.session.execute(select(*columns.c))
    return [dict(row._mapping) for row in result]


# --------------------------------------------------------------------- ordering


async def test_ops_are_applied_in_issue_order(writer: Recorder, thread_id: str):
    """Issue order is the write order — across op types, not just within one.

    The mechanism this replaces fans tasks out with no order between them,
    and holds pre-interaction writes in a queue *per method name*, so a step
    issued after an element still overtook it.
    """
    first = step(thread_id, name="first")
    second = step(thread_id, name="second")
    attachment = element(thread_id, for_id=first.id)

    writer.submit(PatchThread(thread_id, ThreadPatch(name="conversation")))
    writer.submit(SaveStep(first))
    writer.submit(SaveElement(attachment))
    writer.submit(SaveStep(second))
    writer.submit(DeleteElement(attachment.id, thread_id))

    await writer.drain(timeout=5.0)

    assert [type(op).__name__ for op in writer.applied] == [
        "PatchThread",
        "SaveStep",
        "SaveElement",
        "SaveStep",
        "DeleteElement",
    ]


async def test_gate_holds_writes_and_releases_them_in_order(
    persistence: Persistence, registry: WriterRegistry, thread_id: str, uow: UnitOfWork
):
    """Held writes come out in issue order, behind the prelude."""
    writer = Recorder(
        persistence, thread_id, hold_until_interaction=True, registry=registry
    ).start()
    try:
        first = step(thread_id, name="first")
        writer.submit(SaveStep(first))
        writer.submit(SaveElement(element(thread_id, for_id=first.id)))
        second = step(thread_id, name="second")
        writer.submit(SaveStep(second))

        assert [type(op).__name__ for op in writer.held] == [
            "SaveStep",
            "SaveElement",
            "SaveStep",
        ]
        await writer.drain(timeout=1.0)
        assert writer.applied == []

        writer.open_gate(PatchThread(thread_id, ThreadPatch(name="hello")))
        await writer.drain(timeout=5.0)

        assert [type(op).__name__ for op in writer.applied] == [
            "PatchThread",
            "SaveStep",
            "SaveElement",
            "SaveStep",
        ]
        thread = await uow.threads.fetch(thread_id)
        assert thread is not None
        assert thread.name == "hello"
    finally:
        await writer.aclose(timeout=5.0)


async def test_gate_shut_drain_does_not_stall(
    persistence: Persistence, registry: WriterRegistry, thread_id: str
):
    """A reader on a session that has not interacted yet must not wait.

    Held writes are not in flight — nothing is trying to write them — so
    treating them as pending would pin every reader to the full timeout.
    """
    writer = Recorder(
        persistence, thread_id, hold_until_interaction=True, registry=registry
    ).start()
    try:
        writer.submit(SaveStep(step(thread_id)))
        loop = asyncio.get_running_loop()
        started = loop.time()
        await writer.drain(timeout=30.0)
        assert loop.time() - started < 1.0
    finally:
        await writer.aclose(timeout=5.0)


# -------------------------------------------------------------------- lifetime


async def test_close_without_interaction_discards_held_writes(
    persistence: Persistence, registry: WriterRegistry, thread_id: str, uow: UnitOfWork
):
    """A conversation abandoned before it began leaves nothing behind."""
    writer = Recorder(
        persistence, thread_id, hold_until_interaction=True, registry=registry
    ).start()
    writer.submit(SaveStep(step(thread_id)))
    await writer.aclose(timeout=5.0)

    assert await rows(uow, "STEPS") == []
    assert await rows(uow, "THREADS") == []


async def test_close_after_interaction_flushes(
    persistence: Persistence, registry: WriterRegistry, thread_id: str, uow: UnitOfWork
):
    writer = Recorder(
        persistence, thread_id, hold_until_interaction=True, registry=registry
    ).start()
    writer.open_gate(PatchThread(thread_id))
    writer.submit(SaveStep(step(thread_id, name="kept")))
    await writer.aclose(timeout=5.0)

    assert [row["name"] for row in await rows(uow, "STEPS")] == ["kept"]


async def test_closed_writer_drops_further_submissions(
    persistence: Persistence, registry: WriterRegistry, thread_id: str, uow: UnitOfWork
):
    writer = Recorder(persistence, thread_id, registry=registry).start()
    await writer.aclose(timeout=5.0)
    writer.submit(SaveStep(step(thread_id, name="late")))
    await writer.drain(timeout=1.0)

    assert await rows(uow, "STEPS") == []


# ------------------------------------------------------------------- the fence


async def test_drain_returns_while_the_session_keeps_writing(
    writer: Recorder, thread_id: str, uow: UnitOfWork
):
    """The fence means "what I issued has landed", not "the queue is empty".

    An actively-streaming session never has an empty queue; waiting for one
    would pin the reader to the full timeout on every read.
    """
    finished = asyncio.Event()
    landmark = new_id()

    async def keep_writing() -> None:
        for _ in range(400):
            writer.submit(SaveStep(step(thread_id)))
            await asyncio.sleep(0.005)
        finished.set()

    submitter = asyncio.create_task(keep_writing())
    try:
        await asyncio.sleep(0.05)
        writer.submit(SaveStep(step(thread_id, landmark, name="landmark")))
        await writer.drain(timeout=10.0)

        assert not finished.is_set(), "drain waited for the session to go quiet"
        assert await uow.steps.fetch(landmark) is not None
    finally:
        submitter.cancel()
        await asyncio.gather(submitter, return_exceptions=True)


async def test_drain_thread_covers_every_writer_on_the_thread(
    persistence: Persistence, registry: WriterRegistry, thread_id: str, uow: UnitOfWork
):
    """Two tabs are two writers, and one read has to see both."""
    left = Recorder(persistence, thread_id, registry=registry).start()
    right = Recorder(persistence, thread_id, registry=registry).start()
    try:
        assert len(registry.writers_for(thread_id)) == 2
        first, second = new_id(), new_id()
        left.submit(SaveStep(step(thread_id, first, name="left")))
        right.submit(SaveStep(step(thread_id, second, name="right")))

        await registry.drain_thread(thread_id, timeout=10.0)

        assert await uow.steps.fetch(first) is not None
        assert await uow.steps.fetch(second) is not None
    finally:
        await left.aclose(timeout=5.0)
        await right.aclose(timeout=5.0)
        assert registry.writers_for(thread_id) == ()


async def test_drain_thread_covers_a_writer_that_is_still_flushing(
    persistence: Persistence, registry: WriterRegistry, thread_id: str, uow: UnitOfWork
):
    """A closing writer stays visible until its flush is over.

    The barrier this replaces removed a task from its registry only in the
    task's own done-callback. Leaving the registry first would tell a reader
    "nothing pending" during exactly the window where the writes are being
    committed.
    """
    writer = Recorder(persistence, thread_id, registry=registry).start()
    kept = step(thread_id, name="kept")
    writer.submit(SaveStep(kept))

    closing = asyncio.create_task(writer.aclose(timeout=5.0))
    await asyncio.sleep(0)
    await registry.drain_thread(thread_id, timeout=5.0)
    assert await uow.steps.fetch(kept.id) is not None
    await closing


# --------------------------------------------------------------------- uploads


async def test_element_row_waits_for_its_upload(
    writer: Recorder, thread_id: str, uow: UnitOfWork
):
    """The row never lands before the object it points at exists."""
    parent = step(thread_id, name="carrier")
    writer.submit(SaveStep(parent))
    uploaded = asyncio.Event()
    attachment = element(thread_id, for_id=parent.id)

    async def upload() -> None:
        await uploaded.wait()
        return None

    writer.submit_element(attachment, upload)
    await asyncio.sleep(0.05)
    assert await uow.elements.fetch(thread_id, attachment.id) is None

    uploaded.set()
    await writer.drain(timeout=5.0)
    assert await uow.elements.fetch(thread_id, attachment.id) is not None


async def test_the_row_is_written_from_what_the_upload_returns(
    writer: Recorder, thread_id: str, uow: UnitOfWork
):
    """The storage backend settles ``url`` and ``objectKey``, not the caller.

    The legacy layer wrote the upload's own answer into the row --
    ``element_dict["url"] = uploaded_file.get("url")``. A record frozen at
    submit time would store neither, and the attachment would be unreachable.
    """
    parent = step(thread_id, name="carrier")
    writer.submit(SaveStep(parent))
    attachment = element(thread_id, for_id=parent.id)

    async def upload() -> ElementRecord:
        return msgspec.structs.replace(
            attachment,
            url="https://bucket.example/threads/x/files/y",
            object_key="threads/x/files/y",
        )

    writer.submit_element(attachment, upload)
    await writer.drain(timeout=5.0)

    stored = await uow.elements.fetch(thread_id, attachment.id)
    assert stored is not None
    assert stored.object_key == "threads/x/files/y"
    # On the column, not through the reader: ``fetch`` substitutes the app's
    # element route for any row with a blob, and this test is about what the
    # upload wrote, not about what the UI is handed.
    row = (
        await uow.session.execute(
            select(ELEMENTS.c["url"]).where(ELEMENTS.c["id"] == to_uuid(attachment.id))
        )
    ).one()
    assert row.url == "https://bucket.example/threads/x/files/y"


async def test_a_failed_upload_drops_the_row_and_nothing_else(
    writer: Recorder, thread_id: str, uow: UnitOfWork
):
    """Upload failure means no row -- the invariant of doing both in one call."""
    parent = step(thread_id, name="carrier")
    writer.submit(SaveStep(parent))
    attachment = element(thread_id, for_id=parent.id)

    async def upload() -> None:
        raise OSError("bucket unreachable")

    writer.submit_element(attachment, upload)
    survivor = step(thread_id, name="survivor")
    writer.submit(SaveStep(survivor))
    await writer.drain(timeout=5.0)

    assert await uow.elements.fetch(thread_id, attachment.id) is None
    assert await uow.steps.fetch(survivor.id) is not None


async def test_only_the_elements_own_row_waits_for_its_upload(
    writer: Recorder, thread_id: str, uow: UnitOfWork
):
    """An unreachable bucket delays one row, not the conversation.

    The ordered queue is for database writes; an upload is not one. Holding a
    batch on it would mean a user attaching a file while S3 is down stops
    every step of the reply from being persisted -- and, if the socket closes
    first, losing them.
    """
    before = step(thread_id, name="before")
    after = step(thread_id, name="after")
    writer.submit(SaveStep(before))

    async def hung() -> None:
        await asyncio.sleep(3600)

    writer.submit_element(element(thread_id, for_id=before.id), hung)
    writer.submit(SaveStep(after))
    await writer.drain(timeout=1.0)

    assert await uow.steps.fetch(before.id) is not None
    assert await uow.steps.fetch(after.id) is not None


async def test_close_flushes_what_was_issued_behind_a_hung_upload(
    persistence: Persistence, registry: WriterRegistry, thread_id: str, uow: UnitOfWork
):
    """ "Flush what was issued, then stop" -- including behind a stalled upload."""
    writer = Recorder(persistence, thread_id, registry=registry).start()
    kept = step(thread_id, name="kept")
    writer.submit(SaveStep(kept))

    async def hung() -> None:
        await asyncio.sleep(3600)

    writer.submit_element(element(thread_id, for_id=kept.id), hung)
    await writer.aclose(timeout=0.5)

    assert await uow.steps.fetch(kept.id) is not None


async def test_uploads_run_concurrently(writer: Recorder, thread_id: str):
    """The barrier costs the slowest upload, not the sum of them."""
    parent = step(thread_id, name="carrier")
    writer.submit(SaveStep(parent))
    release = asyncio.Event()
    started = [asyncio.Event() for _ in range(3)]

    def uploader(index: int) -> Upload:
        async def upload() -> None:
            started[index].set()
            await release.wait()

        return upload

    for index in range(3):
        writer.submit_element(element(thread_id, for_id=parent.id), uploader(index))

    try:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started)), timeout=5.0
        )
    finally:
        release.set()
    await writer.drain(timeout=5.0)


async def test_an_abandoned_session_does_not_upload_its_held_blobs(
    persistence: Persistence, registry: WriterRegistry, thread_id: str
):
    """A conversation whose rows are discarded must not leave a blob behind.

    ``queue_until_user_message`` queued the arguments and never built the
    coroutine, so nothing was uploaded either. Holding the callable rather
    than a coroutine already in flight is what preserves that.
    """
    writer = Recorder(
        persistence, thread_id, hold_until_interaction=True, registry=registry
    ).start()
    uploaded = asyncio.Event()

    async def upload() -> None:
        uploaded.set()

    writer.submit_element(element(thread_id, for_id=new_id()), upload)
    await writer.aclose(timeout=1.0)

    assert not uploaded.is_set()


async def test_a_closed_writer_does_not_start_the_upload_it_drops(
    writer: Recorder, thread_id: str
):
    uploaded = asyncio.Event()

    async def upload() -> None:
        uploaded.set()

    await writer.aclose(timeout=1.0)
    writer.submit_element(element(thread_id, for_id=new_id()), upload)
    await asyncio.sleep(0.05)

    assert not uploaded.is_set()


async def test_close_cancels_an_upload_still_in_flight(
    persistence: Persistence, registry: WriterRegistry, thread_id: str
):
    """No task is left running with nobody to retrieve its exception."""
    writer = Recorder(persistence, thread_id, registry=registry).start()
    parent = step(thread_id, name="carrier")
    writer.submit(SaveStep(parent))

    async def hung() -> None:
        await asyncio.sleep(3600)

    writer.submit_element(element(thread_id, for_id=parent.id), hung)
    await asyncio.sleep(0.05)
    in_flight = tuple(writer._uploads)
    assert len(in_flight) == 1

    await writer.aclose(timeout=0.2)
    await asyncio.sleep(0)
    assert in_flight[0].cancelled()


async def test_gate_open_starts_the_uploads_it_was_holding(
    persistence: Persistence, registry: WriterRegistry, thread_id: str, uow: UnitOfWork
):
    writer = Recorder(
        persistence, thread_id, hold_until_interaction=True, registry=registry
    ).start()
    try:
        parent = step(thread_id, name="carrier")
        writer.submit(SaveStep(parent))
        attachment = element(thread_id, for_id=parent.id)

        async def upload() -> None:
            return None

        writer.submit_element(attachment, upload)
        writer.open_gate(PatchThread(thread_id))
        await writer.drain(timeout=5.0)

        assert await uow.elements.fetch(thread_id, attachment.id) is not None
    finally:
        await writer.aclose(timeout=5.0)


# ------------------------------------------------------------ thread safety


async def test_submit_from_another_thread_reaches_the_queue(
    writer: Recorder, thread_id: str, uow: UnitOfWork
):
    """Integrations call back from their own threads and say so.

    ``asyncio.Queue`` is not thread-safe; a cross-thread ``put_nowait``
    corrupts its waiter state rather than failing loudly.
    """
    import threading

    record = step(thread_id, name="from another thread")
    thread = threading.Thread(target=writer.submit_threadsafe, args=(SaveStep(record),))
    thread.start()
    thread.join()

    await asyncio.sleep(0.05)
    await writer.drain(timeout=5.0)
    assert await uow.steps.fetch(record.id) is not None


# ------------------------------------------------------------------ resilience


async def test_a_rejected_write_does_not_take_its_batch_down(
    writer: Recorder, thread_id: str, uow: UnitOfWork
):
    """Batching must not turn one bad write into a lost conversation.

    Every op in a batch shares a transaction, so a rejected write rolls its
    innocent neighbours back too. The batch is replayed one op at a time so
    only the offender is lost.
    """
    before = step(thread_id, name="before")
    after = step(thread_id, name="after")
    # No such thread: elements."threadId" is a real foreign key, so this is
    # the write the database refuses while its neighbours are perfectly good.
    orphan = element(new_id(), for_id=before.id)

    writer.submit(SaveStep(before))
    writer.submit(SaveElement(orphan))
    writer.submit(SaveStep(after))
    await writer.drain(timeout=5.0)

    assert await uow.steps.fetch(before.id) is not None
    assert await uow.steps.fetch(after.id) is not None
    assert await uow.elements.fetch(orphan.thread_id, orphan.id) is None


async def test_the_writer_survives_a_failed_batch(
    writer: Recorder, thread_id: str, uow: UnitOfWork
):
    """A dead consumer would silently stop persisting for the whole session."""
    writer.submit(SaveElement(element(new_id(), for_id=new_id())))
    await writer.drain(timeout=5.0)

    later = step(thread_id, name="later")
    writer.submit(SaveStep(later))
    await writer.drain(timeout=5.0)

    assert await uow.steps.fetch(later.id) is not None


async def test_an_upload_landing_during_the_final_drain_still_writes_its_row(
    persistence: Persistence, registry: WriterRegistry, thread_id: str, uow: UnitOfWork
):
    """Closing to submissions before flushing would drop the flush's own work.

    The upload is still in flight when ``aclose`` is called; its row is
    enqueued from inside the drain that ``aclose`` is waiting on.
    """
    writer = Recorder(persistence, thread_id, registry=registry).start()
    parent = step(thread_id, name="carrier")
    writer.submit(SaveStep(parent))
    attachment = element(thread_id, for_id=parent.id)

    async def upload() -> None:
        await asyncio.sleep(0.05)

    writer.submit_element(attachment, upload)
    await writer.aclose(timeout=5.0)

    assert await uow.elements.fetch(thread_id, attachment.id) is not None


# ------------------------------------------------------------------- registry


async def test_shutdown_flushes_every_live_writer(
    persistence: Persistence, uow: UnitOfWork
):
    """A process exiting with live writers must not lose what they buffered.

    While the registry was a module global there was no place for this to
    live, so it did not happen and nothing was positioned to notice.
    """
    registry = WriterRegistry()
    first, second = new_id(), new_id()
    left = Recorder(persistence, first, registry=registry).start()
    right = Recorder(persistence, second, registry=registry).start()

    kept_left = step(first, name="left")
    kept_right = step(second, name="right")
    left.submit(SaveStep(kept_left))
    right.submit(SaveStep(kept_right))

    await registry.aclose(timeout=5.0)

    assert await uow.steps.fetch(kept_left.id) is not None
    assert await uow.steps.fetch(kept_right.id) is not None
    assert registry.live == ()


async def test_registries_do_not_see_each_others_writers(
    persistence: Persistence, thread_id: str, uow: UnitOfWork
):
    """Two applications in one process share a thread id but nothing else."""
    mine, theirs = WriterRegistry(), WriterRegistry()
    writer = Recorder(persistence, thread_id, registry=mine).start()
    try:
        assert mine.writers_for(thread_id) == (writer,)
        assert theirs.writers_for(thread_id) == ()

        record = step(thread_id, name="mine")
        writer.submit(SaveStep(record))
        await theirs.drain_thread(thread_id, timeout=5.0)
        assert await uow.steps.fetch(record.id) is None

        await mine.drain_thread(thread_id, timeout=5.0)
        assert await uow.steps.fetch(record.id) is not None
    finally:
        await writer.aclose(timeout=5.0)


async def test_an_upload_that_starts_during_the_close_still_gets_its_row(
    writer: Recorder, thread_id: str, uow: UnitOfWork
):
    """The blob and the row that points at it leave together, or not at all.

    ``aclose`` deliberately keeps accepting submissions while it drains, so
    that an upload finishing mid-drain still has a row to enqueue. But the
    drain used to snapshot the outstanding uploads once, which left a window:
    an upload starting after that snapshot uploaded its blob and was then
    cancelled before enqueueing its row -- silently, since the cancellation
    is re-raised without a log. The blob stays in the bucket with nothing
    pointing at it, which is the one thing this class exists to prevent.
    """
    parent = step(thread_id, name="carrier")
    writer.submit(SaveStep(parent))

    first_release = asyncio.Event()
    late_started = asyncio.Event()
    late_release = asyncio.Event()

    async def slow_upload() -> None:
        await first_release.wait()
        return None

    async def late_upload() -> None:
        late_started.set()
        await late_release.wait()
        return None

    early = element(thread_id, for_id=parent.id)
    writer.submit_element(early, slow_upload)

    closing = asyncio.create_task(writer.aclose())
    await asyncio.sleep(0.05)

    late = element(thread_id, for_id=parent.id)
    writer.submit_element(late, late_upload)
    first_release.set()

    await asyncio.wait_for(late_started.wait(), timeout=5.0)

    # Held until the *first* fence has demonstrably passed -- the early row is
    # committed -- so the late upload is still in flight at exactly the moment
    # a one-shot drain would decide it was finished and let `aclose` cancel it.
    async def committed() -> None:
        while await uow.elements.fetch(thread_id, early.id) is None:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(committed(), timeout=5.0)
    late_release.set()
    await asyncio.wait_for(closing, timeout=5.0)

    assert await uow.elements.fetch(thread_id, early.id) is not None
    assert await uow.elements.fetch(thread_id, late.id) is not None, (
        "the blob was uploaded and the row was not written: an orphan in the "
        "bucket, which is exactly what the ordering is for"
    )


# --------------------------------------------------------------- blob deletes


class Bucket(BaseStorageClient):
    """A store that remembers what it was asked to delete, and can refuse.

    Refusing by raising rather than by returning ``False``: the real clients
    swallow their own errors, so an exception is the failure mode nothing
    *below* this writer has already handled.
    """

    def __init__(self, refuse: Sequence[str] = ()) -> None:
        self.asked: List[str] = []
        self.refuse = set(refuse)

    async def delete_file(self, object_key: str) -> bool:
        self.asked.append(object_key)
        if object_key in self.refuse:
            raise OSError("bucket unreachable")
        return True

    async def upload_file(
        self,
        object_key: str,
        data: Any,
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {"object_key": object_key, "url": f"memory://{object_key}"}

    async def get_read_url(self, object_key: str) -> str:
        return f"memory://{object_key}"

    async def read_file(self, object_key: str) -> Optional[bytes]:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
def bucket() -> Bucket:
    return Bucket()


@pytest.fixture
async def storing_writer(
    persistence: Persistence,
    registry: WriterRegistry,
    thread_id: str,
    bucket: Bucket,
):
    """A writer whose persistence has somewhere to put blobs."""
    instance = Recorder(
        replace(persistence, storage=bucket), thread_id, registry=registry
    ).start()
    yield instance
    await instance.aclose(timeout=5.0)


async def stored_element(
    uow: UnitOfWork, thread_id: str, for_id: str, object_key: Optional[str]
) -> str:
    """An element row as an upload leaves it, blob or no blob."""
    record = msgspec.structs.replace(
        element(thread_id, for_id=for_id), object_key=object_key
    )
    await uow.elements.save(record)
    await uow.session.commit()
    return record.id


async def test_deleting_an_element_deletes_its_blob(
    storing_writer: Recorder, thread_id: str, uow: UnitOfWork, bucket: Bucket
):
    """The row is what points at the object; nothing else ever will.

    Before this, ``delete_file`` existed on both clients and had no caller:
    every attachment a user removed stayed in the bucket for good.
    """
    parent = step(thread_id, name="carrier")
    await uow.steps.save(parent)
    element_id = await stored_element(uow, thread_id, parent.id, "alice/report.xlsx")

    storing_writer.submit(DeleteElement(element_id, thread_id))
    await storing_writer.drain(timeout=5.0)

    assert await uow.elements.fetch(thread_id, element_id) is None
    assert bucket.asked == ["alice/report.xlsx"]


async def test_deleting_a_step_deletes_the_blobs_of_its_elements(
    storing_writer: Recorder, thread_id: str, uow: UnitOfWork, bucket: Bucket
):
    """The element rows go with the step, so their blobs have to as well."""
    parent = step(thread_id, name="carrier")
    await uow.steps.save(parent)
    await stored_element(uow, thread_id, parent.id, "alice/one.png")
    await stored_element(uow, thread_id, parent.id, "alice/two.png")

    storing_writer.submit(DeleteStep(parent.id))
    await storing_writer.drain(timeout=5.0)

    assert await uow.steps.fetch(parent.id) is None
    assert sorted(bucket.asked) == ["alice/one.png", "alice/two.png"]


async def test_an_element_with_no_blob_asks_the_store_for_nothing(
    storing_writer: Recorder, thread_id: str, uow: UnitOfWork, bucket: Bucket
):
    """``cl.Image(url=...)`` was never uploaded: there is nothing to delete.

    Asserted on the log rather than on the response of a store, because a
    delete of ``""`` or of ``None`` is a request a bucket answers -- with a
    miss, one round trip later, and a warning in the application's log.
    """
    parent = step(thread_id, name="carrier")
    await uow.steps.save(parent)
    external = await stored_element(uow, thread_id, parent.id, None)
    empty = await stored_element(uow, thread_id, parent.id, "")

    storing_writer.submit(DeleteElement(external, thread_id))
    storing_writer.submit(DeleteElement(empty, thread_id))
    await storing_writer.drain(timeout=5.0)

    assert await uow.elements.fetch(thread_id, external) is None
    assert bucket.asked == []


async def test_a_bucket_that_refuses_one_blob_still_loses_the_others(
    persistence: Persistence,
    registry: WriterRegistry,
    thread_id: str,
    uow: UnitOfWork,
):
    """The rows are already gone; a store that says no cannot undo that.

    The second key is the assertion: a delete loop that let the failure out
    would leave every blob after the first one in the bucket, and the batch
    would be replayed as if the database had refused it.
    """
    bucket = Bucket(refuse=["alice/one.png"])
    writer = Recorder(
        replace(persistence, storage=bucket), thread_id, registry=registry
    ).start()
    parent = step(thread_id, name="carrier")
    await uow.steps.save(parent)
    await stored_element(uow, thread_id, parent.id, "alice/one.png")
    await stored_element(uow, thread_id, parent.id, "alice/two.png")

    try:
        writer.submit(DeleteStep(parent.id))
        await writer.drain(timeout=5.0)
    finally:
        await writer.aclose(timeout=5.0)

    assert sorted(bucket.asked) == ["alice/one.png", "alice/two.png"]
    assert await uow.steps.fetch(parent.id) is None
    assert [type(op).__name__ for op in writer.applied] == ["DeleteStep"]


async def test_a_delete_replayed_after_a_failed_batch_still_drops_its_blob(
    storing_writer: Recorder, thread_id: str, uow: UnitOfWork, bucket: Bucket
):
    """The batch rolls back; the replay is where the row actually goes.

    Both paths in ``_write`` commit, so both have to discard -- and only this
    one runs when a neighbour in the batch is refused, which is the moment
    the rest of the session is already going wrong.
    """
    parent = step(thread_id, name="carrier")
    await uow.steps.save(parent)
    element_id = await stored_element(uow, thread_id, parent.id, "alice/report.xlsx")
    # No such thread: elements."threadId" is a real foreign key, so this is
    # the write that takes the whole batch down with it.
    orphan = element(new_id(), for_id=parent.id)

    storing_writer.submit(SaveElement(orphan))
    storing_writer.submit(DeleteElement(element_id, thread_id))
    await storing_writer.drain(timeout=5.0)

    assert await uow.elements.fetch(thread_id, element_id) is None
    assert bucket.asked == ["alice/report.xlsx"]


async def test_a_writer_with_no_storage_deletes_the_row_and_says_nothing(
    writer: Recorder,
    thread_id: str,
    uow: UnitOfWork,
    caplog: pytest.LogCaptureFixture,
):
    """An application with nowhere to put blobs has nothing to discard.

    Silently: the row still carries a key -- the application had storage when
    it was written and does not now -- and a warning per delete would be a
    log nobody can act on.
    """
    parent = step(thread_id, name="carrier")
    await uow.steps.save(parent)
    element_id = await stored_element(uow, thread_id, parent.id, "alice/report.xlsx")

    with caplog.at_level("WARNING", logger="chainlit"):
        writer.submit(DeleteElement(element_id, thread_id))
        await writer.drain(timeout=5.0)

    assert await uow.elements.fetch(thread_id, element_id) is None
    assert [r for r in caplog.records if "alice/report.xlsx" in r.getMessage()] == []


async def test_a_delete_issued_during_an_upload_dooms_its_row(
    storing_writer: Recorder, thread_id: str, uow: UnitOfWork, bucket: Bucket
):
    """A delete must not be overtaken by the save it was meant to undo.

    The row of an element with a blob is queued only when the upload ends,
    while a delete is queued at once -- so the delete used to run first and
    the row landed after it, a zombie pointing at a blob no delete would ever
    return. The panel made this reachable from a click: fill a slot, close
    the tab before the bucket answered.
    """
    parent = step(thread_id, name="carrier")
    storing_writer.submit(SaveStep(parent))
    record = element(thread_id, for_id=parent.id)
    release = asyncio.Event()

    async def slow_upload() -> ElementRecord:
        await release.wait()
        return msgspec.structs.replace(
            record, object_key="alice/blob.png", url="memory://alice/blob.png"
        )

    storing_writer.submit_element(record, slow_upload)
    await asyncio.sleep(0.02)
    storing_writer.submit(DeleteElement(record.id, thread_id))
    release.set()

    async def blob_discarded() -> None:
        while "alice/blob.png" not in bucket.asked:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(blob_discarded(), timeout=5.0)
    await storing_writer.drain(timeout=5.0)

    assert await uow.elements.fetch(thread_id, record.id) is None
    assert [op for op in storing_writer.applied if isinstance(op, SaveElement)] == []


async def test_a_delete_forgets_an_upload_still_waiting_for_the_gate(
    persistence: Persistence, registry: WriterRegistry, thread_id: str, bucket: Bucket
):
    """Nothing was uploaded for it, so there is nothing to discard either."""
    writer = SessionWriter(
        replace(persistence, storage=bucket),
        thread_id,
        registry=registry,
        hold_until_interaction=True,
    )
    record = element(thread_id, for_id=new_id())

    async def never() -> None:
        raise AssertionError("an upload the delete forgot was started")

    writer.submit_element(record, never)
    writer.submit(DeleteElement(record.id, thread_id))

    assert [type(entry).__name__ for entry in writer.held] == ["DeleteElement"]
