"""The service surface the rest of the rebuild talks to."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from chainlit.persistence import (
    ElementRecord,
    FeedbackRecord,
    Persistence,
    StepRecord,
    ThreadPatch,
    UnitOfWork,
)
from chainlit.persistence.models import ELEMENTS, STEPS, THREADS
from chainlit.persistence.services import _column_values
from tests.persistence.conftest import at, iso, make_thread, new_id


async def test_thread_detail_carries_its_steps_and_elements(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow, name="resumable", metadata={"a": 1})
    first = new_id()
    second = new_id()
    await uow.steps.save(
        StepRecord(
            id=second,
            type="assistant_message",
            thread_id=thread_id,
            created_at=iso(at(hour=13)),
            output="second",
        )
    )
    await uow.steps.save(
        StepRecord(
            id=first,
            type="user_message",
            thread_id=thread_id,
            created_at=iso(at(hour=12)),
            output="first",
        )
    )
    await uow.elements.save(
        ElementRecord(
            id=new_id(),
            name="chart.png",
            type="image",
            thread_id=thread_id,
            for_id=second,
            mime="image/png",
            auto_play=False,
            player_config={"controls": True},
            props={"width": 400},
        )
    )

    detail = await uow.threads.get_detail(thread_id)

    assert detail is not None
    assert detail.name == "resumable"
    assert detail.metadata == {"a": 1}
    # Oldest first: the transcript is replayed in order.
    assert [step.id for step in detail.steps] == [first, second]
    assert len(detail.elements) == 1
    assert detail.elements[0].player_config == {"controls": True}
    assert detail.elements[0].props == {"width": 400}
    assert detail.elements[0].auto_play is False


async def test_unknown_thread_has_no_detail(uow: UnitOfWork) -> None:
    assert await uow.threads.get_detail(new_id()) is None


async def test_element_reads_back_by_thread_and_id(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow)
    element_id = new_id()
    await uow.elements.save(
        ElementRecord(id=element_id, name="notes.txt", type="text", thread_id=thread_id)
    )

    stored = await uow.elements.fetch(thread_id, element_id)
    assert stored is not None
    assert stored.name == "notes.txt"

    # Scoped to the thread: another thread's id must not reach it.
    assert await uow.elements.fetch(new_id(), element_id) is None


async def test_an_element_write_leaves_the_columns_it_omits_alone(
    uow: UnitOfWork,
) -> None:
    """Elements are written incrementally: upload the blob, then the url.

    Every field the caller omits is ``UNSET`` and therefore never reaches the
    statement. With ``None`` defaults the second write below nulled
    ``objectKey``, ``props`` and ``mime``, silently losing the upload.
    """
    thread_id = await make_thread(uow)
    element_id = new_id()
    await uow.elements.save(
        ElementRecord(
            id=element_id,
            name="chart.png",
            type="image",
            thread_id=thread_id,
            object_key="threads/abc/chart.png",
            mime="image/png",
            props={"width": 400},
            display="inline",
        )
    )

    # The url arrives later, on its own.
    await uow.elements.save(
        ElementRecord(
            id=element_id,
            name="chart.png",
            type="image",
            url="https://cdn.example/chart.png",
        )
    )

    stored = await uow.elements.fetch(thread_id, element_id)
    assert stored is not None
    assert stored.url == "https://cdn.example/chart.png"
    assert stored.object_key == "threads/abc/chart.png"
    assert stored.mime == "image/png"
    assert stored.props == {"width": 400}
    assert stored.display == "inline"
    assert stored.thread_id == thread_id


async def test_an_element_column_can_still_be_cleared_on_purpose(
    uow: UnitOfWork,
) -> None:
    """UNSET is "no opinion"; an explicit ``None`` is "clear this column"."""
    thread_id = await make_thread(uow)
    element_id = new_id()
    await uow.elements.save(
        ElementRecord(
            id=element_id,
            name="chart.png",
            type="image",
            thread_id=thread_id,
            object_key="threads/abc/chart.png",
            mime="image/png",
        )
    )
    await uow.elements.save(
        ElementRecord(
            id=element_id,
            name="chart.png",
            type="image",
            thread_id=thread_id,
            object_key=None,
        )
    )

    stored = await uow.elements.fetch(thread_id, element_id)
    assert stored is not None
    assert stored.object_key is None
    assert stored.mime == "image/png"


def test_an_omitted_element_field_never_reaches_the_statement() -> None:
    """The record's own contract, without a database in the way."""
    values = _column_values(
        ElementRecord(
            id=new_id(), name="chart.png", type="image", url="https://x/y.png"
        )
    )
    assert set(values) == {"id", "name", "type", "url"}


async def test_patch_records_the_author_and_the_parent(uow: UnitOfWork) -> None:
    user = await uow.users.save("alice", {})
    parent = await make_thread(uow, user_id=user.id)
    child = new_id()

    await uow.threads.patch(
        child,
        ThreadPatch(
            user_id=user.id,
            user_identifier="alice",
            parent_thread_id=parent,
            tags=["profile-switch"],
        ),
    )

    stored = await uow.threads.fetch(child)
    assert stored is not None
    assert stored.user_id == user.id
    assert stored.parent_thread_id == parent
    assert stored.tags == ["profile-switch"]
    assert await uow.threads.get_author(child) == "alice"


async def test_deleting_a_thread_takes_its_children_with_it(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(id=step_id, type="assistant_message", thread_id=thread_id)
    )
    await uow.elements.save(
        ElementRecord(
            id=new_id(), name="x.txt", type="text", thread_id=thread_id, for_id=step_id
        )
    )
    await uow.feedbacks.save(
        FeedbackRecord(for_id=step_id, thread_id=thread_id, value=0)
    )

    await uow.threads.remove(thread_id)

    for table in (THREADS, STEPS, ELEMENTS):
        remaining = await uow.session.execute(select(func.count()).select_from(table))
        assert remaining.scalar_one() == 0


async def test_deleting_a_step_leaves_the_thread(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(id=step_id, type="assistant_message", thread_id=thread_id)
    )

    await uow.steps.remove(step_id)

    assert await uow.steps.fetch(step_id) is None
    assert await uow.threads.fetch(thread_id) is not None


async def test_feedback_is_deleted_by_id(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(id=step_id, type="assistant_message", thread_id=thread_id)
    )
    feedback_id = await uow.feedbacks.save(
        FeedbackRecord(for_id=step_id, thread_id=thread_id, value=1)
    )

    assert await uow.feedbacks.remove(feedback_id) is True
    assert await uow.feedbacks.remove(feedback_id) is False


async def test_a_standalone_unit_of_work_owns_its_session(
    persistence: Persistence, engine: AsyncEngine
) -> None:
    """Without an injected session the context manager commits on exit —
    a socket handler has no framework transaction to ride on."""
    thread_id = new_id()
    async with persistence.uow() as unit:
        await unit.threads.patch(thread_id, ThreadPatch(name="from a background task"))

    async with persistence.uow() as other:
        stored = await other.threads.fetch(thread_id)
    assert stored is not None
    assert stored.name == "from a background task"


async def test_a_standalone_unit_of_work_rolls_back_on_failure(
    persistence: Persistence,
) -> None:
    thread_id = new_id()
    try:
        async with persistence.uow() as unit:
            await unit.threads.patch(thread_id, ThreadPatch(name="doomed"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    async with persistence.uow() as unit:
        assert await unit.threads.fetch(thread_id) is None
