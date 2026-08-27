"""Wiring: the engine config, the alembic hookup and the unit of work."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Type

from advanced_alchemy.config import AlembicAsyncConfig, AsyncSessionConfig

# EngineConfig must come from the Litestar extension, not from
# advanced_alchemy.config: they are two distinct classes with the same name,
# and SQLAlchemyAsyncConfig here is the extension's one.
from advanced_alchemy.extensions.litestar import EngineConfig, SQLAlchemyAsyncConfig
from alembic.config import Config as AlembicConfig
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from chainlit.persistence.models import SCHEMA_NAME, Base
from chainlit.persistence.services import (
    ElementService,
    FeedbackService,
    StepService,
    ThreadService,
    UserService,
)

MIGRATIONS_PATH = Path(__file__).parent / "migrations"
VERSION_TABLE = "alembic_version"


def alembic_config(url: Optional[str] = None) -> AlembicConfig:
    """Build the alembic config for this package's migrations.

    There is no alembic.ini: the migrations ship inside the wheel, so the
    script location has to be resolved from ``__file__`` rather than from a
    file the deployment is expected to have copied somewhere.
    """
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_PATH))
    if url is not None:
        # Escaped: a password with a '%' in it would otherwise be read as
        # ConfigParser interpolation.
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade_database(connection: Connection, revision: str = "head") -> None:
    """Migrate through an already-open synchronous connection.

    Async callers reach this through ``connection.run_sync(upgrade_database)``;
    alembic's own machinery is synchronous all the way down.
    """
    from alembic import command

    cfg = alembic_config()
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, revision)


def sqlalchemy_config(
    url: Optional[str] = None,
    engine: Optional[AsyncEngine] = None,
    **engine_kwargs: Any,
) -> SQLAlchemyAsyncConfig:
    """The advanced_alchemy config this package's services run on.

    Either a URL to build an engine from, or an engine the caller already
    owns — an app with its own pool settings should hand one in rather than
    have a second one opened behind its back.
    """
    if (url is None) == (engine is None):
        raise ValueError("Pass exactly one of url or engine")
    if engine is not None and engine_kwargs:
        raise ValueError("Engine settings belong to the engine you passed in")

    return SQLAlchemyAsyncConfig(
        connection_string=url,
        engine_instance=engine,
        # Engine settings go through EngineConfig. Spreading them into the
        # SQLAlchemyAsyncConfig constructor instead raises TypeError on the
        # first caller who passes one, which is what `pool_size` did.
        engine_config=EngineConfig(**engine_kwargs)
        if engine_kwargs
        else EngineConfig(),
        session_config=AsyncSessionConfig(expire_on_commit=False),
        # advanced_alchemy registers this against its bind key; without it the
        # registry keeps its own empty MetaData and anything driving DDL or
        # autogenerate through the config sees no tables at all.
        metadata=Base.metadata,
        # The ORM never writes an `updated_at` column of its own here — the
        # thread service sets "updatedAt" explicitly, and the listener would
        # only go looking for a column that does not exist.
        enable_touch_updated_timestamp_listener=False,
        # No model uses FileObject/StoredObject: element blobs live in
        # `objectKey`/`url` as plain text, so the listener has nothing to do.
        enable_file_object_listener=False,
        alembic_config=AlembicAsyncConfig(
            script_location=str(MIGRATIONS_PATH),
            version_table_name=VERSION_TABLE,
            version_table_schema=SCHEMA_NAME,
        ),
    )


@dataclass
class UnitOfWork:
    """One session, and the five services bound to it.

    Services are per-session in advanced_alchemy, so they cannot be built once
    at startup; this is the object handlers actually work against.
    """

    session: AsyncSession
    users: UserService
    threads: ThreadService
    steps: StepService
    elements: ElementService
    feedbacks: FeedbackService

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()


@dataclass
class Persistence:
    """Everything the app needs to reach the database.

    The service *classes* live here rather than instances: each request opens
    its own session and instantiates them against it.
    """

    config: SQLAlchemyAsyncConfig
    user_service: Type[UserService] = UserService
    thread_service: Type[ThreadService] = ThreadService
    step_service: Type[StepService] = StepService
    element_service: Type[ElementService] = ElementService
    feedback_service: Type[FeedbackService] = FeedbackService

    @classmethod
    def from_url(cls, url: str, **engine_kwargs: Any) -> "Persistence":
        return cls(config=sqlalchemy_config(url=url, **engine_kwargs))

    @classmethod
    def from_engine(cls, engine: AsyncEngine, **engine_kwargs: Any) -> "Persistence":
        return cls(config=sqlalchemy_config(engine=engine, **engine_kwargs))

    def bind(self, session: AsyncSession) -> UnitOfWork:
        """Bind the services to a session someone else owns."""
        return UnitOfWork(
            session=session,
            users=self.user_service(session=session),
            threads=self.thread_service(session=session),
            steps=self.step_service(session=session),
            elements=self.element_service(session=session),
            feedbacks=self.feedback_service(session=session),
        )

    @asynccontextmanager
    async def uow(
        self, session: Optional[AsyncSession] = None
    ) -> AsyncIterator[UnitOfWork]:
        """Yield a unit of work, committing on a clean exit.

        With a session passed in (a Litestar handler's injected one) the
        caller keeps ownership: no commit, no close, so the framework's own
        commit-on-response still decides. Standalone — a socket handler, a
        background task — this opens and owns the session itself.
        """
        if session is not None:
            yield self.bind(session)
            return

        async with self.config.get_session() as owned:
            uow = self.bind(owned)
            try:
                yield uow
            except Exception:
                await owned.rollback()
                raise
            await owned.commit()

    async def close(self) -> None:
        engine = self.config.get_engine()
        await engine.dispose()
