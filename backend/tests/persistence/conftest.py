"""Fixtures for the persistence tests.

The schema under test is built by running the real migrations, not by
hand-written DDL: a test schema written by hand only proves the tests agree
with themselves, while this proves the migrations produce the schema the
models expect.

PostgreSQL only. The statements under test are written for the production
dialect -- ``jsonb`` operators, ``LEAST``, ``ON CONFLICT``, ``NULLS LAST`` --
and a SQLite run could only ever exercise a translation of them, which is
what this suite used to do and what it stopped proving anything with. The
suite connects to ``TEST_DATABASE_URL`` if set, else to ``DEFAULT_POSTGRES_URL``
(the CI service container); if nothing is listening there it stops at once
with the ``docker run`` that would start one.
"""

import asyncio
import os
import socket
import uuid
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Dict, Optional

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Connection, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from chainlit.persistence import Persistence, UnitOfWork
from chainlit.persistence.config import MIGRATIONS_PATH
from chainlit.persistence.models import SCHEMA_NAME

# Where the suite runs when TEST_DATABASE_URL is unset. Matches the service
# container in .github/workflows/tests.yaml and the docker command below.
DEFAULT_POSTGRES_URL = (
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/chainlit_pytest"
)

DOCKER_COMMAND = (
    "docker run -d --name chainlit-test-pg -e POSTGRES_USER=postgres "
    "-e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=chainlit_pytest "
    "-p 5432:5432 postgres:16"
)

# Truncated between tests, children first so the statement is legal even if a
# future migration drops a cascade.
TABLE_NAMES = ("feedbacks", "elements", "steps", "threads", "users")


class PostgresUnavailable(RuntimeError):
    """Nothing is listening where the suite expects PostgreSQL."""


def postgres_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or DEFAULT_POSTGRES_URL


def require_postgres(url: str, timeout: float = 1.0) -> None:
    """Fail fast, and say how to fix it, if the database is not reachable.

    A plain TCP connect rather than a database round-trip: the question is
    whether anything is listening at all, and asyncpg answering that with a
    stack trace through the engine is the failure this exists to replace.
    """
    parsed = make_url(url)
    host, port = parsed.host or "127.0.0.1", parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return
    except OSError as error:
        raise PostgresUnavailable(
            f"The persistence tests need PostgreSQL at {host}:{port} and nothing "
            f"is listening ({error}). Start one with:\n\n    {DOCKER_COMMAND}\n\n"
            "or point TEST_DATABASE_URL at an existing server."
        ) from error


def upgrade(connection: Connection, revision: str = "head") -> None:
    """Run the packaged migrations on an open synchronous connection.

    The suite's own alembic hookup: env.py takes the connection from
    ``config.attributes`` and runs on its transaction, so the schema is built
    by exactly the migrations a deployment runs, with no second engine.
    """
    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_PATH))
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


async def migrate(engine: AsyncEngine, revision: str = "head") -> None:
    """Run the packaged migrations against an engine."""
    async with engine.connect() as connection:
        await connection.run_sync(upgrade, revision)
        await connection.commit()


async def drop_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.exec_driver_sql(
            f'DROP SCHEMA IF EXISTS "{SCHEMA_NAME}" CASCADE'
        )
        await connection.commit()


async def _rebuild(url: str) -> None:
    """Drop and re-migrate the schema once, for the whole session."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        await drop_schema(engine)
        await migrate(engine)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    """The migrated database, built once for the session.

    ``asyncio.run`` rather than an async fixture: pytest-asyncio gives every
    test its own loop, and a session-scoped async fixture would pin a loop
    that is closed before the last test.
    """
    url = postgres_url()
    try:
        require_postgres(url)
    except PostgresUnavailable as error:
        pytest.exit(str(error), returncode=1)
    asyncio.run(_rebuild(url))
    return url


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """A migrated, empty database, one per test.

    The schema is migrated once per session and truncated here — running the
    migrations per test would dominate the runtime, and TRUNCATE leaves the
    same empty database.
    """
    engine = create_async_engine(database_url, poolclass=NullPool)
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
