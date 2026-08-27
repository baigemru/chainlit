"""The ordered writer: order, coalescing, the gate, the fence, uploads.

These run against the real database on both dialects, because the properties
under test are properties of what ends up stored — an in-memory double would
only prove the writer agrees with itself. What the double *is* used for is
observing dispatch order, by recording each op as it is applied.
"""

import asyncio
from typing import Any, Dict, List, Optional

import msgspec
import pytest

from chainlit.persistence import Persistence, UnitOfWork
from chainlit.persistence.records import ElementRecord, StepRecord, ThreadPatch
from chainlit.persistence.statements import PLACEHOLDER_STEP_TYPE
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
    coalesce,
    drain_thread,
    merge_steps,
    writers_for,
)

from .conftest import at, iso, new_id


class Recorder(SessionWriter):
    """A writer that remembers, in order, every op it actually applied."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.applied: List[Op] = []

    async def _dispatch(self, uow: UnitOfWork, op: Op) -> None:
        await super()._dispatch(uow, op)
        self.applied.append(op)


@pytest.fixture
def thread_id() -> str:
    return new_id()


@pytest.fixture
async def writer(persistence: Persistence, thread_id: str):
    """An open writer, started, and closed at the end of the test."""
    instance = Recorder(persistence, thread_id).start()
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
    persistence: Persistence, thread_id: str, uow: UnitOfWork
):
    """Held writes come out in issue order, behind the prelude."""
    writer = Recorder(persistence, thread_id, hold_until_interaction=True).start()
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


async def test_gate_shut_drain_does_not_stall(persistence: Persistence, thread_id: str):
    """A reader on a session that has not interacted yet must not wait.

    Held writes are not in flight — nothing is trying to write them — so
    treating them as pending would pin every reader to the full timeout.
    """
    writer = Recorder(persistence, thread_id, hold_until_interaction=True).start()
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
    persistence: Persistence, thread_id: str, uow: UnitOfWork
):
    """A conversation abandoned before it began leaves nothing behind."""
    writer = Recorder(persistence, thread_id, hold_until_interaction=True).start()
    writer.submit(SaveStep(step(thread_id)))
    await writer.aclose(timeout=5.0)

    assert await rows(uow, "STEPS") == []
    assert await rows(uow, "THREADS") == []


async def test_close_after_interaction_flushes(
    persistence: Persistence, thread_id: str, uow: UnitOfWork
):
    writer = Recorder(persistence, thread_id, hold_until_interaction=True).start()
    writer.open_gate(PatchThread(thread_id))
    writer.submit(SaveStep(step(thread_id, name="kept")))
    await writer.aclose(timeout=5.0)

    assert [row["name"] for row in await rows(uow, "STEPS")] == ["kept"]


async def test_closed_writer_drops_further_submissions(
    persistence: Persistence, thread_id: str, uow: UnitOfWork
):
    writer = Recorder(persistence, thread_id).start()
    await writer.aclose(timeout=5.0)
    writer.submit(SaveStep(step(thread_id, name="late")))
    await writer.drain(timeout=1.0)

    assert await rows(uow, "STEPS") == []


# ------------------------------------------------------------------- coalescing


def test_coalesce_folds_repeat_writes_of_one_step():
    thread = new_id()
    identifier = new_id()
    ops = [
        SaveStep(step(thread, identifier, output="a")),
        SaveStep(step(thread, identifier, output="ab")),
        SaveStep(step(thread, identifier, output="abc")),
    ]
    folded = coalesce(ops)

    assert len(folded) == 1
    assert folded[0].record.output == "abc"


def test_coalesce_keeps_the_first_fragments_position():
    """The merged write does not overtake anything it was issued behind."""
    thread = new_id()
    streaming = new_id()
    other = step(thread, name="other")
    ops = [
        SaveStep(step(thread, streaming, output="a")),
        SaveStep(other),
        SaveStep(step(thread, streaming, output="ab")),
    ]
    folded = coalesce(ops)

    assert [op.record.id for op in folded] == [streaming, other.id]


def test_coalesce_does_not_merge_across_a_delete():
    """A write after a delete describes a row that has to be created again."""
    thread = new_id()
    identifier = new_id()
    ops = [
        SaveStep(step(thread, identifier, output="before")),
        DeleteStep(identifier),
        SaveStep(step(thread, identifier, output="after")),
    ]
    folded = coalesce(ops)

    assert [type(op).__name__ for op in folded] == [
        "SaveStep",
        "DeleteStep",
        "SaveStep",
    ]


def test_coalesce_never_merges_elements():
    """Two elements are two objects; both rows have to land."""
    thread = new_id()
    parent = new_id()
    first = element(thread, parent)
    second = element(thread, parent)
    folded = coalesce([SaveElement(first), SaveElement(second)])

    assert [op.record.id for op in folded] == [first.id, second.id]


def test_coalesce_merges_thread_metadata_per_key():
    thread = new_id()
    folded = coalesce(
        [
            PatchThread(thread, ThreadPatch(name="one", metadata={"a": 1, "b": 1})),
            PatchThread(thread, ThreadPatch(metadata={"b": 2, "c": 3})),
        ]
    )

    assert len(folded) == 1
    assert folded[0].patch.name == "one"
    assert folded[0].patch.metadata == {"a": 1, "b": 2, "c": 3}


def test_merge_keeps_the_earliest_start_and_refuses_a_placeholder_type():
    thread = new_id()
    identifier = new_id()
    early = iso(at(hour=10))
    late = iso(at(hour=11))

    merged = merge_steps(
        step(thread, identifier, type="user_message", start=late, output="a"),
        step(thread, identifier, type=PLACEHOLDER_STEP_TYPE, start=early),
    )

    assert merged.start == early
    assert merged.type == "user_message"
    assert merged.output == "a"


@pytest.mark.parametrize(
    "fragments",
    [
        pytest.param(
            [
                {"type": "assistant_message", "start": iso(at(hour=11)), "output": ""},
                {"output": "he"},
                {"output": "hello", "streaming": False},
            ],
            id="streaming",
        ),
        pytest.param(
            [
                {"type": "assistant_message", "start": iso(at(hour=11))},
                {"type": PLACEHOLDER_STEP_TYPE, "start": iso(at(hour=10))},
                {"metadata": {"k": "v"}, "end": iso(at(hour=12))},
            ],
            id="placeholder-and-backdated-start",
        ),
        pytest.param(
            [
                {"type": "assistant_message", "start": iso(at(hour=10))},
                {"start": None, "output": "cleared"},
            ],
            id="start-cleared-by-a-later-write",
        ),
        pytest.param(
            [
                {"type": "assistant_message", "start": "2026-08-27T10:00:00+02:00"},
                {"start": "2026-08-27T09:00:00Z"},
            ],
            id="start-with-an-offset",
        ),
        pytest.param(
            [
                {"type": "tool", "input": "q", "is_error": False},
                {"is_error": True, "output": "boom"},
            ],
            id="error",
        ),
    ],
)
async def test_coalescing_is_equivalent_to_writing_each_fragment(
    persistence: Persistence, uow: UnitOfWork, fragments: List[Dict[str, Any]]
):
    """The folded write stores exactly what the sequence of writes stores.

    This is the property that licenses coalescing at all: the upsert does not
    write ``start`` and ``type`` straight through, so a fold that ignored that
    would quietly change history under a streaming message.
    """
    sequential_thread = new_id()
    folded_thread = new_id()
    sequential_id = new_id()
    folded_id = new_id()

    for fields in fragments:
        await uow.steps.save(step(sequential_thread, sequential_id, **fields))
    await uow.session.commit()

    records = [step(folded_thread, folded_id, **fields) for fields in fragments]
    merged = records[0]
    for record in records[1:]:
        merged = merge_steps(merged, record)
    await uow.steps.save(merged)
    await uow.session.commit()

    left = await uow.steps.fetch(sequential_id)
    right = await uow.steps.fetch(folded_id)
    assert left is not None
    assert right is not None

    ignored = {"id", "thread_id"}
    for info in msgspec.structs.fields(StepRecord):
        if info.name in ignored:
            continue
        assert getattr(left, info.name) == getattr(right, info.name), info.name


async def test_a_streaming_message_becomes_one_write(
    writer: Recorder, thread_id: str, uow: UnitOfWork
):
    """Forty tokens, one row written once — not forty transactions."""
    identifier = new_id()
    writer.submit(SaveStep(step(thread_id, identifier, type="assistant_message")))
    for index in range(40):
        writer.submit(SaveStep(step(thread_id, identifier, output="x" * (index + 1))))
    await writer.drain(timeout=10.0)

    assert len([op for op in writer.applied if isinstance(op, SaveStep)]) == 1
    stored = await uow.steps.fetch(identifier)
    assert stored is not None
    assert stored.output == "x" * 40


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
    persistence: Persistence, thread_id: str, uow: UnitOfWork
):
    """Two tabs are two writers, and one read has to see both."""
    left = Recorder(persistence, thread_id).start()
    right = Recorder(persistence, thread_id).start()
    try:
        assert len(writers_for(thread_id)) == 2
        first, second = new_id(), new_id()
        left.submit(SaveStep(step(thread_id, first, name="left")))
        right.submit(SaveStep(step(thread_id, second, name="right")))

        await drain_thread(thread_id, timeout=10.0)

        assert await uow.steps.fetch(first) is not None
        assert await uow.steps.fetch(second) is not None
    finally:
        await left.aclose(timeout=5.0)
        await right.aclose(timeout=5.0)
        assert writers_for(thread_id) == ()


async def test_drain_thread_covers_a_writer_that_is_still_flushing(
    persistence: Persistence, thread_id: str, uow: UnitOfWork
):
    """A closing writer stays visible until its flush is over.

    The barrier this replaces removed a task from its registry only in the
    task's own done-callback. Leaving the registry first would tell a reader
    "nothing pending" during exactly the window where the writes are being
    committed.
    """
    writer = Recorder(persistence, thread_id).start()
    kept = step(thread_id, name="kept")
    writer.submit(SaveStep(kept))

    closing = asyncio.create_task(writer.aclose(timeout=5.0))
    await asyncio.sleep(0)
    await drain_thread(thread_id, timeout=5.0)
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
    assert stored.url == "https://bucket.example/threads/x/files/y"
    assert stored.object_key == "threads/x/files/y"


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
    persistence: Persistence, thread_id: str, uow: UnitOfWork
):
    """ "Flush what was issued, then stop" -- including behind a stalled upload."""
    writer = Recorder(persistence, thread_id).start()
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
    persistence: Persistence, thread_id: str
):
    """A conversation whose rows are discarded must not leave a blob behind.

    ``queue_until_user_message`` queued the arguments and never built the
    coroutine, so nothing was uploaded either. Holding the callable rather
    than a coroutine already in flight is what preserves that.
    """
    writer = Recorder(persistence, thread_id, hold_until_interaction=True).start()
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
    persistence: Persistence, thread_id: str
):
    """No task is left running with nobody to retrieve its exception."""
    writer = Recorder(persistence, thread_id).start()
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
    persistence: Persistence, thread_id: str, uow: UnitOfWork
):
    writer = Recorder(persistence, thread_id, hold_until_interaction=True).start()
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
    persistence: Persistence, thread_id: str, uow: UnitOfWork
):
    """Closing to submissions before flushing would drop the flush's own work.

    The upload is still in flight when ``aclose`` is called; its row is
    enqueued from inside the drain that ``aclose`` is waiting on.
    """
    writer = Recorder(persistence, thread_id).start()
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
