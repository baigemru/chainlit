"""The alembic environment: it takes the engine the CLI hands it.

``litestar database upgrade`` (advanced_alchemy's command group) builds an
``AlembicCommandConfig`` carrying the app's engine and nothing else -- no
``sqlalchemy.url``, no injected connection. env.py has to migrate on that
engine, or the one path a deployment uses is the one path that does not work.
"""

import asyncio

import pytest
from advanced_alchemy.extensions.litestar import AlembicCommands
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from chainlit.persistence import Persistence
from chainlit.persistence.config import MIGRATIONS_PATH
from chainlit.persistence.models import SCHEMA_NAME
from tests.persistence.conftest import (
    DOCKER_COMMAND,
    PostgresUnavailable,
    require_postgres,
)


def head_revision() -> str:
    """The newest revision the package ships."""
    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_PATH))
    return ScriptDirectory.from_config(config).get_current_head() or ""


async def current_revision(engine: AsyncEngine) -> str:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text(f'SELECT version_num FROM "{SCHEMA_NAME}".alembic_version')
            )
        ).scalar_one()


async def test_the_cli_path_migrates_on_config_engine(
    database_url: str, engine: AsyncEngine
) -> None:
    """Downgrade one step and come back through ``AlembicCommands`` alone.

    A throwaway ``Persistence`` rather than the suite's engine: env.py
    disposes the engine it is handed once the run is over. In a thread,
    because alembic runs the async engine under ``asyncio.run`` and there is
    already a loop running here.
    """
    commands = AlembicCommands(Persistence.from_url(database_url).config)

    await asyncio.to_thread(commands.downgrade, "0002_indexes_and_updated_at")
    assert await current_revision(engine) == "0002_indexes_and_updated_at"

    await asyncio.to_thread(commands.upgrade, "head")
    # Read out of the script directory rather than spelled here: what this
    # pins is that the CLI path arrives at head, and a literal would go red
    # on every revision that has nothing to do with it.
    assert await current_revision(engine) == head_revision()


def test_an_unreachable_database_names_the_docker_command() -> None:
    """Port 1 answers nothing; the message has to say what to start."""
    with pytest.raises(PostgresUnavailable) as caught:
        require_postgres("postgresql+asyncpg://postgres:postgres@127.0.0.1:1/x")

    assert "127.0.0.1:1" in str(caught.value)
    assert DOCKER_COMMAND in str(caught.value)
