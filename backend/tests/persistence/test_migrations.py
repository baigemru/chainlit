"""The migrations themselves: they run, and 0002 backfills what it promises."""

from pathlib import Path
from typing import Any, Dict, Sequence

import sqlalchemy as sa
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import create_async_engine

from tests.persistence.conftest import (
    TRANSLATE_MAP,
    at,
    iso,
    migrate,
    new_id,
    sqlite_url,
)

# Deliberately not the models: this data is written while the database is at
# revision 0001, where `threads."updatedAt"` does not exist yet.
THREADS_0001 = sa.table(
    "threads",
    sa.column("id", sa.Uuid()),
    sa.column("createdAt", sa.Text()),
    sa.column("name", sa.Text()),
    sa.column("metadata", sa.JSON()),
    # No schema: on SQLite the chainlit schema is folded into the default one.
)
STEPS_0001 = sa.table(
    "steps",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.Text()),
    sa.column("type", sa.Text()),
    sa.column("threadId", sa.Uuid()),
    sa.column("streaming", sa.Boolean()),
    sa.column("createdAt", sa.Text()),
    # No schema: on SQLite the chainlit schema is folded into the default one.
)


async def test_baseline_then_upgrade_backfills_updated_at(tmp_path: Path) -> None:
    import uuid

    engine = create_async_engine(
        sqlite_url(tmp_path), execution_options={"schema_translate_map": TRANSLATE_MAP}
    )
    try:
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

        async with engine.connect() as connection:
            rows: Sequence[Row[Any]] = (
                await connection.execute(
                    sa.select(sa.column("id"), sa.column("updatedAt")).select_from(
                        sa.table("threads")
                    )
                )
            ).all()
        stored: Dict[str, str] = {row[0]: row[1] for row in rows}

        # The newest step, not the thread's own creation time.
        assert stored[str(busy_thread).replace("-", "")] == iso(at(hour=13))
        # No steps: the COALESCE falls back to createdAt rather than leaving
        # a NULL that would drop the thread out of the keyset page.
        assert stored[str(quiet_thread).replace("-", "")] == iso(at(hour=10))
    finally:
        await engine.dispose()
