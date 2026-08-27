"""Alembic environment for the chainlit persistence schema.

Hand-written rather than generated from advanced_alchemy's template: the
template never passes ``version_table_schema`` and never sets
``include_schemas``, both of which this schema needs because its tables do not
live in the connection's default schema.

Two entry paths are supported. Callers that already hold a connection (the
test-suite, an app running its own migrations at startup) put it in
``config.attributes["connection"]``; everything else falls back to the URL in
the alembic config.
"""

import asyncio
from typing import Any, Optional

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from chainlit.persistence.models import SCHEMA_NAME, Base

config = context.config
target_metadata = Base.metadata

# The only tables this package owns. Autogenerate is otherwise happy to
# propose dropping every table it finds in the database — LangGraph's
# checkpoint tables share the same database in production.
MANAGED_TABLES = frozenset({"users", "threads", "steps", "elements", "feedbacks"})


def include_object(
    obj: Any,
    name: Optional[str],
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Restrict autogenerate to the five tables this package owns."""
    if type_ == "table":
        return name in MANAGED_TABLES
    parent = getattr(obj, "table", None)
    if parent is not None:
        return parent.name in MANAGED_TABLES
    return True


def _configure(connection: Optional[Connection]) -> None:
    """Apply the settings shared by the online and offline paths."""
    # SQLite has no schemas: the whole schema collapses into the default one,
    # and the version table has to follow it.
    is_sqlite = connection is not None and connection.dialect.name == "sqlite"
    version_table_schema = None if is_sqlite else SCHEMA_NAME

    context.configure(
        connection=connection,
        url=None
        if connection is not None
        else config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table="alembic_version",
        version_table_schema=version_table_schema,
        # The ISO timestamps are TEXT on purpose. With type comparison on,
        # autogenerate would propose retyping them to timestamptz on every
        # run and quietly offer to rewrite live data.
        compare_type=False,
        compare_server_default=True,
        render_as_batch=is_sqlite,
        literal_binds=connection is None,
        dialect_opts={"paramstyle": "named"},
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a database connection."""
    _configure(None)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run the migrations on an already-open synchronous connection."""
    if connection.dialect.name == "sqlite":
        connection = connection.execution_options(
            schema_translate_map={SCHEMA_NAME: None}
        )
    else:
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA_NAME}"')
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async engine from the config and migrate through it."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    await connectable.dispose()


def run_migrations_online() -> None:
    """Migrate against an injected connection, or one built from the URL."""
    injected = config.attributes.get("connection")
    if injected is not None:
        do_run_migrations(injected)
        return

    url = config.get_main_option("sqlalchemy.url") or ""
    if "+aiosqlite" in url or "+asyncpg" in url or "+psycopg_async" in url:
        asyncio.run(run_async_migrations())
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        do_run_migrations(connection)
        connection.commit()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
