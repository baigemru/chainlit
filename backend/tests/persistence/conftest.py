"""Fixtures for the persistence tests.

The schema under test is built by running the real migrations, not by
hand-written DDL: a test schema written by hand only proves the tests agree
with themselves, while this proves the migrations produce the schema the
models expect.

The same tests run on two dialects. By default they run on aiosqlite, which
needs no service and is what unit CI uses. Point ``TEST_DATABASE_URL`` at a
PostgreSQL instance (or pass ``--postgres``, which supplies a default URL)
and the ``engine`` fixture switches over — every test in this package then
exercises the production dialect, including the arms of the statements that
only PostgreSQL ever compiles.

SQLite has no schemas, so the ``chainlit`` schema is folded into the default
one through ``schema_translate_map`` — the statements under test stay
schema-qualified exactly as they are in production. SQLite also ships foreign
keys switched *off*; the pragma below turns them on, otherwise the tests
happily write rows production would reject.
"""

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterator, Optional

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from chainlit.persistence import Persistence, UnitOfWork
from chainlit.persistence.config import upgrade_database
from chainlit.persistence.models import SCHEMA_NAME

TRANSLATE_MAP = {SCHEMA_NAME: None}

# What ``--postgres`` means when no URL is given. Matches the service
# container in .github/workflows/tests.yaml.
DEFAULT_POSTGRES_URL = (
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/chainlit_test"
)

# Truncated between tests, children first so the statement is legal even if a
# future migration drops a cascade.
TABLE_NAMES = ("feedbacks", "elements", "steps", "threads", "users")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--postgres",
        action="store_true",
        default=False,
        help=(
            "Run the persistence tests against PostgreSQL instead of SQLite. "
            f"Uses TEST_DATABASE_URL, or {DEFAULT_POSTGRES_URL}."
        ),
    )


@pytest.fixture(scope="session")
def postgres_url(request: pytest.FixtureRequest) -> Optional[str]:
    """The PostgreSQL URL to run against, or None to stay on SQLite."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    if request.config.getoption("--postgres"):
        return DEFAULT_POSTGRES_URL
    return None


def sqlite_url(directory: Path) -> str:
    return f"sqlite+aiosqlite:///{directory / 'chainlit.db'}"


def _enforce_foreign_keys(engine: AsyncEngine) -> None:
    """Switch SQLite's foreign keys on, per connection.

    They default to off, which silently accepts a step pointing at a thread
    that does not exist — precisely the write PostgreSQL rejects in
    production. The listener goes on the sync engine because that is where
    the DBAPI connection actually surfaces.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_sqlite_engine(directory: Path) -> AsyncEngine:
    """A SQLite engine wired the way production expects: schema folded, FKs on."""
    engine = create_async_engine(
        sqlite_url(directory),
        execution_options={"schema_translate_map": TRANSLATE_MAP},
    )
    _enforce_foreign_keys(engine)
    return engine


async def migrate(engine: AsyncEngine, revision: str = "head") -> None:
    """Run the packaged migrations against an engine."""
    async with engine.connect() as connection:
        await connection.run_sync(upgrade_database, revision)
        await connection.commit()


async def _rebuild_postgres(url: str) -> None:
    """Drop and re-migrate the schema once, for the whole session."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.exec_driver_sql(
                f'DROP SCHEMA IF EXISTS "{SCHEMA_NAME}" CASCADE'
            )
            await connection.commit()
        await migrate(engine)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def postgres_schema(postgres_url: Optional[str]) -> Iterator[Optional[str]]:
    """Build the PostgreSQL schema once, if that is where we are running.

    ``asyncio.run`` rather than an async fixture: pytest-asyncio gives every
    test its own loop, and a session-scoped async fixture would pin a loop
    that is closed before the last test.
    """
    if postgres_url is None:
        yield None
        return
    import asyncio

    asyncio.run(_rebuild_postgres(postgres_url))
    yield postgres_url


@pytest_asyncio.fixture
async def engine(
    tmp_path: Path, postgres_schema: Optional[str]
) -> AsyncIterator[AsyncEngine]:
    """A migrated, empty database, one per test.

    On SQLite that is a fresh file. On PostgreSQL the schema is migrated once
    per session and truncated here — running the migrations per test would
    dominate the runtime, and TRUNCATE leaves the same empty database.
    """
    if postgres_schema is None:
        engine = create_sqlite_engine(tmp_path)
        await migrate(engine)
        yield engine
        await engine.dispose()
        return

    engine = create_async_engine(postgres_schema, poolclass=NullPool)
    qualified = ", ".join(f'"{SCHEMA_NAME}".{name}' for name in TABLE_NAMES)
    async with engine.connect() as connection:
        await connection.execute(text(f"TRUNCATE {qualified} RESTART IDENTITY CASCADE"))
        await connection.commit()
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
    clear_updated_at: bool = False,
) -> str:
    """Create a thread and, optionally, force its updatedAt.

    ``updatedAt`` is set by the service to "now"; a test that needs an
    ordering has to overwrite it afterwards. ``clear_updated_at`` writes a
    NULL there instead — the state migration 0002 leaves behind for a thread
    with no steps and no ``createdAt``, and the state the legacy data layer
    writes while it runs alongside this one.
    """
    from sqlalchemy import update

    from chainlit.persistence.models import THREADS
    from chainlit.persistence.records import ThreadPatch

    thread_id = thread_id or new_id()
    # Built in one go rather than assigned into: the record is frozen, and
    # "not provided" is a distinct value from "provided as empty".
    patch = (
        ThreadPatch(name=name, user_id=user_id)
        if metadata is None
        else ThreadPatch(name=name, user_id=user_id, metadata=metadata)
    )
    await uow.threads.patch(thread_id, patch)
    if clear_updated_at:
        updated_at = None
    elif updated_at is None:
        return thread_id
    await uow.session.execute(
        update(THREADS)
        .where(THREADS.c["id"] == uuid.UUID(thread_id))
        .values({"updatedAt": updated_at})
    )
    return thread_id
