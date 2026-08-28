"""The wiring in persistence/config.py, which nothing else exercises.

Every assertion here corresponds to a defect found by an audit: the public
constructors advertised keyword arguments that raised, and the config was
handed to advanced_alchemy without the metadata it registers against.
"""

import pytest
from advanced_alchemy.base import metadata_registry
from sqlalchemy.ext.asyncio import create_async_engine

from chainlit.persistence import Persistence
from chainlit.persistence.config import sqlalchemy_config
from chainlit.persistence.models import Base

# Never connected to: these tests build configs, not engines with connections.
URL = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/never"


def test_engine_settings_reach_the_engine_config() -> None:
    """`from_url(url, pool_size=...)` used to raise TypeError.

    The kwargs were spread into the SQLAlchemyAsyncConfig constructor, which
    has no engine fields -- so the documented API died on its first caller.
    """
    config = sqlalchemy_config(URL, pool_size=7, pool_pre_ping=True)

    assert config.engine_config.pool_size == 7
    assert config.engine_config.pool_pre_ping is True


def test_from_url_accepts_engine_settings() -> None:
    persistence = Persistence.from_url(URL, pool_size=3)

    assert persistence.config.engine_config.pool_size == 3


def test_engine_settings_are_refused_alongside_a_prebuilt_engine() -> None:
    """Passing both is a caller error, not a silently ignored argument."""
    engine = create_async_engine(URL)

    with pytest.raises(ValueError, match="belong to the engine"):
        sqlalchemy_config(engine=engine, pool_size=3)


def test_url_and_engine_are_mutually_exclusive() -> None:
    engine = create_async_engine(URL)

    with pytest.raises(ValueError, match="exactly one"):
        sqlalchemy_config(url=URL, engine=engine)

    with pytest.raises(ValueError, match="exactly one"):
        sqlalchemy_config()


def test_metadata_is_registered_with_advanced_alchemy() -> None:
    """Without this, the registry keeps its own empty MetaData.

    Anything driving DDL or autogenerate through the config would then see no
    tables at all -- and would report that as "no changes", not as an error.
    """
    config = sqlalchemy_config(URL)

    assert config.metadata is Base.metadata
    assert set(config.metadata.tables) == {
        "chainlit.users",
        "chainlit.threads",
        "chainlit.steps",
        "chainlit.elements",
        "chainlit.feedbacks",
    }
    assert metadata_registry.get(config.bind_key) is Base.metadata


def test_listeners_that_have_nothing_to_do_are_off() -> None:
    """Neither listener can ever fire here.

    The timestamp one hooks the ORM flush, and every write in this package is
    a Core statement that never flushes; the file-object one looks for
    FileObject columns, and element blobs are plain `objectKey`/`url` text.
    """
    config = sqlalchemy_config(URL)

    assert config.enable_touch_updated_timestamp_listener is False
    assert config.enable_file_object_listener is False


def test_alembic_config_targets_the_chainlit_schema() -> None:
    config = sqlalchemy_config(URL)

    assert config.alembic_config.version_table_name == "alembic_version"
    assert config.alembic_config.version_table_schema == "chainlit"
    assert config.alembic_config.script_location.endswith("migrations")


def test_the_unit_of_work_is_five_services_and_a_session() -> None:
    """No commit/rollback of its own: the session decides, and the two
    callers that own a session (``Persistence.uow`` and the request's
    before-send handler) already do that on it directly."""
    from chainlit.persistence.config import UnitOfWork

    assert {f for f in UnitOfWork.__dataclass_fields__} == {
        "session",
        "users",
        "threads",
        "steps",
        "elements",
        "feedbacks",
    }
    assert not any(
        callable(getattr(UnitOfWork, name, None)) for name in ("commit", "rollback")
    )


async def test_a_cancelled_unit_of_work_returns_its_connection(
    database_url: str,
) -> None:
    """A task torn down mid-query must hand its connection back.

    The user leaving a chat cancels whatever the agent was doing, and what
    it was doing may be a write. The cancellation comes from an anyio
    cancel scope -- the websocket's task group -- which re-delivers it at
    *every* await until the scope exits: a plain ``await close()`` in a
    ``finally`` is cancelled too, the connection is never returned, and
    the garbage collector reports it minutes later. That log line is what
    this test is. Locally the plain close survives too -- the re-delivery
    only bites when asyncpg's own cancel of the running query is what gets
    cancelled -- so this pins the property, not the mechanism.
    """
    import asyncio
    from typing import cast

    import anyio
    from sqlalchemy import text
    from sqlalchemy.pool import QueuePool

    persistence = Persistence.from_url(database_url, pool_size=2, max_overflow=0)
    engine = persistence.config.get_engine()
    pool = cast(QueuePool, engine.pool)
    started = asyncio.Event()

    async def work() -> None:
        async with persistence.uow() as unit:
            started.set()
            await unit.session.execute(text("SELECT pg_sleep(5)"))

    async with anyio.create_task_group() as group:
        group.start_soon(work)
        await started.wait()
        await asyncio.sleep(0.05)
        group.cancel_scope.cancel()

    # The cleanup ran on despite the scope; give it a moment to land.
    for _ in range(50):
        if pool.checkedout() == 0:
            break
        await asyncio.sleep(0.02)
    try:
        assert pool.checkedout() == 0
    finally:
        await engine.dispose()
