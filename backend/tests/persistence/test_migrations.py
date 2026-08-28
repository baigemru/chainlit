"""The migrations themselves: they run, and 0002/0003 do what they promise.

Each test drops the session's schema and rebuilds it from an earlier revision
so it can plant rows that predate the migration under test. They finish at
``head``, which is the state every other test's ``engine`` fixture assumes.
"""

import uuid
from typing import Any, Dict, Sequence

import sqlalchemy as sa
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from chainlit.persistence.models import SCHEMA_NAME
from tests.persistence.conftest import at, drop_schema, iso, migrate, new_id

# Deliberately not the models: this data is written while the database is at
# revision 0001, where `threads."updatedAt"` does not exist yet.
THREADS_0001 = sa.table(
    "threads",
    sa.column("id", sa.Uuid()),
    sa.column("createdAt", sa.Text()),
    sa.column("name", sa.Text()),
    sa.column("metadata", sa.JSON()),
    schema=SCHEMA_NAME,
)
FEEDBACKS_0002 = sa.table(
    "feedbacks",
    sa.column("id", sa.Uuid()),
    sa.column("forId", sa.Uuid()),
    sa.column("threadId", sa.Uuid()),
    sa.column("value", sa.Integer()),
    sa.column("comment", sa.Text()),
    schema=SCHEMA_NAME,
)
STEPS_0001 = sa.table(
    "steps",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.Text()),
    sa.column("type", sa.Text()),
    sa.column("threadId", sa.Uuid()),
    sa.column("streaming", sa.Boolean()),
    sa.column("createdAt", sa.Text()),
    schema=SCHEMA_NAME,
)


async def test_baseline_then_upgrade_backfills_updated_at(engine: AsyncEngine) -> None:
    await drop_schema(engine)
    await migrate(engine, "0001_baseline")

    busy_thread = uuid.UUID(new_id())
    quiet_thread = uuid.UUID(new_id())
    async with engine.begin() as connection:
        await connection.execute(
            THREADS_0001.insert(),
            [
                {
                    "id": busy_thread,
                    "createdAt": iso(at(hour=9)),
                    "name": "busy",
                    "metadata": {},
                },
                {
                    "id": quiet_thread,
                    "createdAt": iso(at(hour=10)),
                    "name": "quiet",
                    "metadata": {},
                },
            ],
        )
        await connection.execute(
            STEPS_0001.insert(),
            [
                {
                    "id": uuid.UUID(new_id()),
                    "name": "first",
                    "type": "user_message",
                    "threadId": busy_thread,
                    "streaming": False,
                    "createdAt": iso(at(hour=11)),
                },
                {
                    "id": uuid.UUID(new_id()),
                    "name": "last",
                    "type": "assistant_message",
                    "threadId": busy_thread,
                    "streaming": False,
                    "createdAt": iso(at(hour=13)),
                },
            ],
        )

    await migrate(engine, "head")

    threads = sa.table(
        "threads",
        sa.column("id", sa.Uuid()),
        sa.column("updatedAt", sa.Text()),
        schema=SCHEMA_NAME,
    )
    async with engine.connect() as connection:
        rows: Sequence[Row[Any]] = (
            await connection.execute(sa.select(threads.c.id, threads.c.updatedAt))
        ).all()
    stored: Dict[uuid.UUID, str] = {row[0]: row[1] for row in rows}

    # The newest step, not the thread's own creation time.
    assert stored[busy_thread] == iso(at(hour=13))
    # No steps: the COALESCE falls back to createdAt rather than leaving
    # a NULL that would drop the thread out of the keyset page.
    assert stored[quiet_thread] == iso(at(hour=10))


async def test_duplicate_feedback_is_collapsed_before_the_index_is_built(
    engine: AsyncEngine,
) -> None:
    """0003 has to survive a database that already broke the rule it adds.

    The duplicates predate the constraint -- the old upsert keyed on the
    feedback's own id, so a client that lost it wrote a second row. Creating
    the unique index on that database fails halfway, which is why the dedupe
    runs first and unconditionally.
    """
    await drop_schema(engine)
    await migrate(engine, "0002_indexes_and_updated_at")

    thread_id = uuid.UUID(new_id())
    step_id = uuid.UUID(new_id())
    ids = sorted(uuid.UUID(new_id()) for _ in range(3))
    async with engine.begin() as connection:
        await connection.execute(
            THREADS_0001.insert(),
            [{"id": thread_id, "createdAt": iso(at()), "name": "t"}],
        )
        await connection.execute(
            STEPS_0001.insert(),
            [
                {
                    "id": step_id,
                    "name": "answer",
                    "type": "assistant_message",
                    "threadId": thread_id,
                    "streaming": False,
                    "createdAt": iso(at()),
                }
            ],
        )
        await connection.execute(
            FEEDBACKS_0002.insert(),
            [
                {
                    "id": feedback_id,
                    "forId": step_id,
                    "threadId": thread_id,
                    "value": index,
                    "comment": f"c{index}",
                }
                for index, feedback_id in enumerate(ids)
            ],
        )

    await migrate(engine, "head")

    async with engine.connect() as connection:
        rows: Sequence[Row[Any]] = (
            await connection.execute(sa.select(FEEDBACKS_0002.c.id))
        ).all()
    surviving = [row.id for row in rows]
    # MAX(id) *as text*, which is the one deterministic choice on a table with
    # no timestamp -- and text order is what the migration compares in.
    assert surviving == [max(ids, key=str)]
