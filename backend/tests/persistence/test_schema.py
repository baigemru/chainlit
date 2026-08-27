"""The mapping must match the schema that is deployed, column for column."""

from typing import Dict, Set

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from chainlit.persistence.models import Base

# Taken from the production database (PostgreSQL, schema `chainlit`), plus the
# three columns migration 0002 adds. If a model column is not in this map, it
# does not exist in production and every query using it would fail there.
PRODUCTION_COLUMNS: Dict[str, Set[str]] = {
    "users": {"id", "identifier", "metadata", "createdAt"},
    "threads": {
        "id",
        "createdAt",
        "name",
        "userId",
        "userIdentifier",
        "tags",
        "metadata",
        "parentThreadId",
        # 0002
        "updatedAt",
    },
    "steps": {
        "id",
        "name",
        "type",
        "threadId",
        "parentId",
        "streaming",
        "waitForAnswer",
        "isError",
        "metadata",
        "tags",
        "input",
        "output",
        "createdAt",
        "command",
        "start",
        "end",
        "generation",
        "showInput",
        "language",
        "indent",
        "defaultOpen",
        "modes",
        "autoCollapse",
    },
    "elements": {
        "id",
        "threadId",
        "type",
        "url",
        "chainlitKey",
        "name",
        "display",
        "objectKey",
        "size",
        "page",
        "language",
        "forId",
        "mime",
        "props",
        # 0002
        "autoPlay",
        "playerConfig",
    },
    "feedbacks": {"id", "forId", "threadId", "value", "comment"},
}

# Columns the deployed schema does *not* have. The legacy data layer wrote to
# some of them; every one of those writes was silently doing nothing or
# raising.
ABSENT_COLUMNS = {
    ("steps", "disableFeedback"),
    ("threads", "deletedAt"),
}


def test_models_declare_exactly_the_production_columns() -> None:
    mapped = {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.sorted_tables
    }
    assert mapped == PRODUCTION_COLUMNS


def test_models_do_not_resurrect_dropped_columns() -> None:
    mapped = {
        (table.name, column.name)
        for table in Base.metadata.sorted_tables
        for column in table.columns
    }
    assert mapped.isdisjoint(ABSENT_COLUMNS)


def test_models_live_in_the_chainlit_schema() -> None:
    assert {table.schema for table in Base.metadata.sorted_tables} == {"chainlit"}


async def test_migrated_database_matches_the_models(engine: AsyncEngine) -> None:
    """Drift in either direction — a model column the migrations never
    create, or a migration column no model knows about — fails here."""

    async with engine.connect() as connection:
        migrated = await connection.run_sync(
            lambda sync_connection: {
                name: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns(name)
                }
                for name in inspect(sync_connection).get_table_names()
                if name != "alembic_version"
            }
        )
    assert migrated == PRODUCTION_COLUMNS


async def test_migrated_indexes_match_the_models(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        migrated = await connection.run_sync(
            lambda sync_connection: {
                index["name"]
                for name in inspect(sync_connection).get_table_names()
                for index in inspect(sync_connection).get_indexes(name)
                if index["name"] is not None
                and not str(index["name"]).startswith("sqlite_autoindex")
            }
        )
    declared = {
        index.name
        for table in Base.metadata.sorted_tables
        for index in table.indexes
        if index.name is not None
    }
    assert migrated == declared
