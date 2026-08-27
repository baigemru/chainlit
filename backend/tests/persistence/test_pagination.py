"""Keyset pagination over the thread history."""

from typing import List, Optional

import msgspec
import pytest

from chainlit.persistence import StepRecord, ThreadQuery, UnitOfWork
from chainlit.persistence.statements import decode_cursor
from tests.persistence.conftest import at, iso, make_thread, new_id


async def a_user(uow: UnitOfWork, identifier: str = "alice") -> str:
    user = await uow.users.save(identifier, {})
    return user.id


def points_at(cursor: Optional[str], thread_id: str) -> bool:
    """Whether an opaque cursor marks this thread's position."""
    assert cursor is not None
    decoded = decode_cursor(cursor)
    assert decoded is not None
    return decoded.id == thread_id


# A walk that has not finished by here is not walking: a cursor the query
# stops honouring sends the client back to page one for ever, and an
# unbounded loop turns that into a hung CI job instead of a failed test.
MAX_PAGES = 50


async def walk(uow: UnitOfWork, user_id: str, page_size: int) -> List[str]:
    """Every thread id the paginator hands out, page by page."""
    seen: List[str] = []
    cursor = None
    for _ in range(MAX_PAGES):
        page = await uow.threads.page(
            ThreadQuery(user_id=user_id, first=page_size, cursor=cursor)
        )
        seen.extend(thread.id for thread in page.data)
        if not page.page_info.has_next_page:
            return seen
        cursor = page.page_info.end_cursor
    raise AssertionError(
        f"the history never ended: {len(seen)} threads in {MAX_PAGES} pages"
    )


async def test_page_is_newest_first_and_reports_a_next_page(uow: UnitOfWork) -> None:
    user_id = await a_user(uow)
    oldest = await make_thread(uow, user_id=user_id, updated_at=at(hour=9))
    middle = await make_thread(uow, user_id=user_id, updated_at=at(hour=10))
    newest = await make_thread(uow, user_id=user_id, updated_at=at(hour=11))

    page = await uow.threads.page(ThreadQuery(user_id=user_id, first=2))

    assert [thread.id for thread in page.data] == [newest, middle]
    assert page.page_info.has_next_page is True
    # The cursors are positions, not thread ids -- see
    # test_a_cursor_is_opaque_and_carries_its_own_position.
    assert points_at(page.page_info.start_cursor, newest)
    assert points_at(page.page_info.end_cursor, middle)

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


async def test_a_thread_with_no_activity_yet_sorts_last(uow: UnitOfWork) -> None:
    """A NULL ``updatedAt`` belongs at the end of the history, not the front.

    Migration 0002 backfills ``coalesce(max(step.createdAt), thread.createdAt)``
    and ``threads."createdAt"`` is itself nullable, so a NULL survives the
    backfill; the legacy data layer, running alongside during the migration
    window, writes threads with no ``updatedAt`` at all. PostgreSQL sorts
    NULLs FIRST under DESC, which puts such a thread at the top of page one.
    """
    user_id = await a_user(uow)
    newest = await make_thread(uow, user_id=user_id, updated_at=at(hour=11))
    older = await make_thread(uow, user_id=user_id, updated_at=at(hour=10))
    never = await make_thread(uow, user_id=user_id, clear_updated_at=True)

    page = await uow.threads.page(ThreadQuery(user_id=user_id, first=10))

    assert [thread.id for thread in page.data] == [newest, older, never]


async def test_a_history_containing_a_null_updated_at_is_walked_completely(
    uow: UnitOfWork,
) -> None:
    """The other half of the NULL: it must not swallow the rest of the list.

    The keyset compares ``(updatedAt, id)`` as a row, and a NULL on either
    side makes the whole predicate NULL — so the page after the NULL row comes
    back empty with ``hasNextPage=False`` and the history simply stops.
    """
    user_id = await a_user(uow)
    newest = await make_thread(uow, user_id=user_id, updated_at=at(hour=11))
    older = await make_thread(uow, user_id=user_id, updated_at=at(hour=10))
    never = await make_thread(uow, user_id=user_id, clear_updated_at=True)
    also_never = await make_thread(uow, user_id=user_id, clear_updated_at=True)

    walked = await walk(uow, user_id, page_size=1)

    assert sorted(walked) == sorted([newest, older, never, also_never])
    assert walked[:2] == [newest, older]


async def test_deleting_the_cursor_thread_does_not_truncate_the_history(
    uow: UnitOfWork,
) -> None:
    """The cursor has to survive the row it names.

    Reading the timestamp back out of the cursor row makes the whole
    comparison NULL once that row is gone — and a thread being deleted from
    another tab while this one scrolls is ordinary. Everything below the
    deleted thread would become unreachable until the page is reloaded.
    """
    user_id = await a_user(uow)
    await make_thread(uow, user_id=user_id, updated_at=at(hour=12))
    boundary = await make_thread(uow, user_id=user_id, updated_at=at(hour=11))
    oldest = await make_thread(uow, user_id=user_id, updated_at=at(hour=10))

    page = await uow.threads.page(ThreadQuery(user_id=user_id, first=2))
    assert page.page_info.has_next_page is True
    cursor = page.page_info.end_cursor

    await uow.threads.remove(boundary)

    second = await uow.threads.page(
        ThreadQuery(user_id=user_id, first=2, cursor=cursor)
    )
    assert [thread.id for thread in second.data] == [oldest]


async def test_a_full_last_page_reports_no_next_page(uow: UnitOfWork) -> None:
    """Exactly ``first`` threads is the boundary hasNextPage gets wrong.

    With ``>=`` instead of ``>`` the client is told to fetch a page that does
    not exist; the walk-based tests still terminate, so only the exact
    boundary catches it.
    """
    user_id = await a_user(uow)
    await make_thread(uow, user_id=user_id, updated_at=at(hour=11))
    await make_thread(uow, user_id=user_id, updated_at=at(hour=10))

    page = await uow.threads.page(ThreadQuery(user_id=user_id, first=2))

    assert len(page.data) == 2
    assert page.page_info.has_next_page is False


async def test_a_page_size_below_one_still_serves_a_page(uow: UnitOfWork) -> None:
    """``first=0`` used to answer "empty page, and there is more".

    ``LIMIT 0 + 1`` returns a row, so hasNextPage is True; the row is then
    trimmed off, so endCursor is None — and a client looping until
    hasNextPage goes False re-requests page one forever.
    """
    user_id = await a_user(uow)
    await make_thread(uow, user_id=user_id, updated_at=at(hour=11))
    await make_thread(uow, user_id=user_id, updated_at=at(hour=10))

    page = await uow.threads.page(ThreadQuery(user_id=user_id, first=0))

    assert len(page.data) == 1
    assert page.page_info.has_next_page is True
    assert page.page_info.end_cursor is not None
    assert await walk(uow, user_id, page_size=0) != []


@pytest.mark.parametrize("first", [0, -1, 1000])
def test_the_wire_refuses_an_out_of_range_page_size(first: int) -> None:
    """The bound is on the record, so the generated schema carries it too."""
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(msgspec.json.encode({"first": first}), type=ThreadQuery)


async def test_a_malformed_user_id_lists_nothing(uow: UnitOfWork) -> None:
    """It arrives off the wire, and it must fail closed.

    ``UUID("not-a-uuid")`` raises ValueError; dropping the filter instead
    would hand the caller every user's history.
    """
    user_id = await a_user(uow)
    await make_thread(uow, user_id=user_id, updated_at=at(hour=11))

    page = await uow.threads.page(ThreadQuery(user_id="not-a-uuid", first=10))

    assert page.data == []
    assert page.page_info.has_next_page is False


async def test_a_malformed_cursor_serves_the_first_page(uow: UnitOfWork) -> None:
    """A cursor the client did not get from us is not an error, just noise."""
    user_id = await a_user(uow)
    newest = await make_thread(uow, user_id=user_id, updated_at=at(hour=11))
    older = await make_thread(uow, user_id=user_id, updated_at=at(hour=10))

    page = await uow.threads.page(
        ThreadQuery(user_id=user_id, first=10, cursor="not-a-cursor")
    )

    assert [thread.id for thread in page.data] == [newest, older]


async def test_a_search_wildcard_is_matched_literally(uow: UnitOfWork) -> None:
    """``%`` and ``_`` are what the user typed, not what they meant.

    Unescaped, a search for "%" matches every thread, and "a_c" matches "abc".
    """
    user_id = await a_user(uow)
    literal_match = await make_thread(
        uow, user_id=user_id, name="100% done", updated_at=at(hour=11)
    )
    await make_thread(uow, user_id=user_id, name="Unrelated", updated_at=at(hour=10))

    percent = await uow.threads.page(ThreadQuery(user_id=user_id, search="%", first=10))
    assert [thread.id for thread in percent.data] == [literal_match]

    underscore = await uow.threads.page(
        ThreadQuery(user_id=user_id, search="Unrelate_", first=10)
    )
    assert underscore.data == []


async def test_a_cursor_is_opaque_and_carries_its_own_position(
    uow: UnitOfWork,
) -> None:
    """Not a thread id: the position has to outlive the row it came from."""
    user_id = await a_user(uow)
    newest = await make_thread(uow, user_id=user_id, updated_at=at(hour=11))
    await make_thread(uow, user_id=user_id, updated_at=at(hour=10))

    page = await uow.threads.page(ThreadQuery(user_id=user_id, first=1))
    cursor: Optional[str] = page.page_info.start_cursor

    assert cursor is not None
    assert cursor != newest
