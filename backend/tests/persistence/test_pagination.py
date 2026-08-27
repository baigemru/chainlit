"""Keyset pagination over the thread history."""

from typing import List

from chainlit.persistence import StepRecord, ThreadQuery, UnitOfWork
from tests.persistence.conftest import at, iso, make_thread, new_id


async def a_user(uow: UnitOfWork, identifier: str = "alice") -> str:
    user = await uow.users.save(identifier, {})
    return user.id


async def walk(uow: UnitOfWork, user_id: str, page_size: int) -> List[str]:
    """Every thread id the paginator hands out, page by page."""
    seen: List[str] = []
    cursor = None
    while True:
        page = await uow.threads.page(
            ThreadQuery(user_id=user_id, first=page_size, cursor=cursor)
        )
        seen.extend(thread.id for thread in page.data)
        if not page.page_info.has_next_page:
            return seen
        cursor = page.page_info.end_cursor


async def test_page_is_newest_first_and_reports_a_next_page(uow: UnitOfWork) -> None:
    user_id = await a_user(uow)
    oldest = await make_thread(uow, user_id=user_id, updated_at=at(hour=9))
    middle = await make_thread(uow, user_id=user_id, updated_at=at(hour=10))
    newest = await make_thread(uow, user_id=user_id, updated_at=at(hour=11))

    page = await uow.threads.page(ThreadQuery(user_id=user_id, first=2))

    assert [thread.id for thread in page.data] == [newest, middle]
    assert page.page_info.has_next_page is True
    assert page.page_info.start_cursor == newest
    assert page.page_info.end_cursor == middle

    second = await uow.threads.page(
        ThreadQuery(user_id=user_id, first=2, cursor=page.page_info.end_cursor)
    )
    assert [thread.id for thread in second.data] == [oldest]
    assert second.page_info.has_next_page is False


async def test_threads_sharing_a_timestamp_are_walked_exactly_once(
    uow: UnitOfWork,
) -> None:
    """The reason the cursor compares ``(updatedAt, id)`` as a row.

    A profile switch writes the parent thread and its successor in the same
    instant. With the timestamp alone as the cursor, one of them is returned
    on both pages or on neither.
    """
    user_id = await a_user(uow)
    same_moment = at(hour=10)
    threads = {
        await make_thread(uow, user_id=user_id, updated_at=same_moment)
        for _ in range(4)
    }

    seen = await walk(uow, user_id, page_size=1)

    assert len(seen) == len(threads)
    assert set(seen) == threads


async def test_a_thread_with_no_steps_is_still_listed(uow: UnitOfWork) -> None:
    """updatedAt is written by the service, so it is never NULL — a NULL
    would drop the thread out of the row comparison entirely."""
    user_id = await a_user(uow)
    empty = await make_thread(uow, user_id=user_id)

    page = await uow.threads.page(ThreadQuery(user_id=user_id, first=10))

    assert [thread.id for thread in page.data] == [empty]
    assert page.data[0].updated_at is not None


async def test_other_users_threads_are_not_listed(uow: UnitOfWork) -> None:
    alice = await a_user(uow, "alice")
    bob = await a_user(uow, "bob")
    hers = await make_thread(uow, user_id=alice, updated_at=at(hour=10))
    await make_thread(uow, user_id=bob, updated_at=at(hour=11))

    page = await uow.threads.page(ThreadQuery(user_id=alice, first=10))

    assert [thread.id for thread in page.data] == [hers]


async def test_search_matches_a_name_or_a_step_output(uow: UnitOfWork) -> None:
    user_id = await a_user(uow)
    by_name = await make_thread(
        uow, user_id=user_id, name="Deployment notes", updated_at=at(hour=11)
    )
    by_output = await make_thread(uow, user_id=user_id, updated_at=at(hour=10))
    await uow.steps.save(
        StepRecord(
            id=new_id(),
            type="assistant_message",
            thread_id=by_output,
            output="the DEPLOYMENT failed",
            created_at=iso(at(hour=10)),
        )
    )
    await make_thread(uow, user_id=user_id, name="Unrelated", updated_at=at(hour=9))

    page = await uow.threads.page(
        ThreadQuery(user_id=user_id, search="deployment", first=10)
    )

    assert [thread.id for thread in page.data] == [by_name, by_output]


async def test_feedback_filter_keeps_only_rated_threads(uow: UnitOfWork) -> None:
    from chainlit.persistence import FeedbackRecord

    user_id = await a_user(uow)
    rated = await make_thread(uow, user_id=user_id, updated_at=at(hour=11))
    await make_thread(uow, user_id=user_id, updated_at=at(hour=10))
    step_id = new_id()
    await uow.steps.save(
        StepRecord(id=step_id, type="assistant_message", thread_id=rated)
    )
    await uow.feedbacks.save(FeedbackRecord(for_id=step_id, thread_id=rated, value=1))

    page = await uow.threads.page(ThreadQuery(user_id=user_id, feedback=1, first=10))

    assert [thread.id for thread in page.data] == [rated]
