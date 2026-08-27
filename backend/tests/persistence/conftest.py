"""Fixtures for the persistence tests.

The schema under test is built by running the real migrations against
aiosqlite, not by hand-written DDL: a test schema written by hand only proves
the tests agree with themselves, while this proves the migrations produce the
schema the models expect.

SQLite has no schemas, so the ``chainlit`` schema is folded into the default
one through ``schema_translate_map`` — the statements under test stay
schema-qualified exactly as they are in production.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from chainlit.persistence import Persistence, UnitOfWork
from chainlit.persistence.config import upgrade_database
from chainlit.persistence.models import SCHEMA_NAME

TRANSLATE_MAP = {SCHEMA_NAME: None}


def sqlite_url(directory: Path) -> str:
    return f"sqlite+aiosqlite:///{directory / 'chainlit.db'}"


async def migrate(engine: AsyncEngine, revision: str = "head") -> None:
    """Run the packaged migrations against an engine."""
    async with engine.connect() as connection:
        await connection.run_sync(upgrade_database, revision)
        await connection.commit()


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """A migrated, empty database, one per test."""
    engine = create_async_engine(
        sqlite_url(tmp_path), execution_options={"schema_translate_map": TRANSLATE_MAP}
    )
    await migrate(engine)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def persistence(engine: AsyncEngine) -> Persistence:
    """A Persistence over the migrated database, reusing its engine."""
    return Persistence.from_engine(engine)


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@pytest_asyncio.fixture
async def uow(
    persistence: Persistence, session: AsyncSession
) -> AsyncIterator[UnitOfWork]:
    """A unit of work over the migrated database."""
    async with persistence.uow(session) as unit:
        yield unit
    await session.commit()


def new_id() -> str:
    return str(uuid.uuid4())


def iso(moment: datetime) -> str:
    """The ISO-with-Z text this schema stores."""
    return (
        moment.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds")
        + "Z"
    )


def at(
    year: int = 2026,
    month: int = 8,
    day: int = 27,
    hour: int = 12,
    minute: int = 0,
    second: int = 0,
    microsecond: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC)


async def make_thread(
    uow: UnitOfWork,
    thread_id: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
    name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    updated_at: Optional[datetime] = None,
) -> str:
    """Create a thread and, optionally, force its updatedAt.

    ``updatedAt`` is set by the service to "now"; a test that needs an
    ordering has to overwrite it afterwards.
    """
    from sqlalchemy import update

    from chainlit.persistence.models import THREADS
    from chainlit.persistence.records import ThreadPatch

    thread_id = thread_id or new_id()
    patch = ThreadPatch(name=name, user_id=user_id)
    if metadata is not None:
        patch.metadata = metadata
    await uow.threads.patch(thread_id, patch)
    if updated_at is not None:
        await uow.session.execute(
            update(THREADS)
            .where(THREADS.c["id"] == uuid.UUID(thread_id))
            .values({"updatedAt": updated_at})
        )
    return thread_id
