"""Alembic environment for the chainlit persistence schema.

Hand-written rather than generated from advanced_alchemy's template: the
template never passes ``version_table_schema`` and never sets
``include_schemas``, both of which this schema needs because its tables do not
live in the connection's default schema.

Two entry paths, and no ``sqlalchemy.url``:

* ``litestar database upgrade`` (advanced_alchemy's CLI, registered by the
  ``SQLAlchemyPlugin`` that ``Persistence.plugin`` builds) hands the app's own
  engine over as ``config.engine`` -- ``AlembicCommandConfig`` in
  ``advanced_alchemy/alembic/commands.py`` sets it, and this is the path a
  deployment migrates through;
* a caller that already holds an open synchronous connection -- the
  test-suite building its schema, an app migrating inside its own lifespan --
  puts it in ``config.attributes["connection"]`` and the migrations run on
  that connection's transaction.

There is no alembic.ini and no URL fallback. The migrations ship inside the
wheel and every caller has an engine already; a second way to spell the
database would only let the CLI and the app disagree about which one.
"""

import asyncio
from typing import Any, Optional

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

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


def _configure(connection: Optional[Connection], url: Optional[str] = None) -> None:
    """Apply the settings shared by the online and offline paths."""
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table="alembic_version",
        version_table_schema=SCHEMA_NAME,
        # The ISO timestamps are TEXT on purpose. With type comparison on,
        # autogenerate would propose retyping them to timestamptz on every
        # run and quietly offer to rewrite live data.
        compare_type=False,
        compare_server_default=True,
        literal_binds=connection is None,
        dialect_opts={"paramstyle": "named"},
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a database connection.

    ``db_url`` is what advanced_alchemy's command config carries; it is the
    only place a URL comes from, since nothing writes ``sqlalchemy.url``.
    """
    _configure(None, url=getattr(config, "db_url", None))
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run the migrations on an already-open synchronous connection."""
    connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA_NAME}"')
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_on_engine(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    # Disposed, not handed back: the pool's connections are bound to the loop
    # ``asyncio.run`` is about to close, so nothing could use them afterwards.
    await engine.dispose()


def run_migrations_online() -> None:
    """Migrate on the injected connection, or on the engine the CLI hands in."""
    injected = config.attributes.get("connection")
    if injected is not None:
        do_run_migrations(injected)
        return

    engine = getattr(config, "engine", None)
    if engine is None:
        raise RuntimeError(
            "No database to migrate: run `litestar database upgrade` against an "
            "app with a Persistence registered, or put an open connection in "
            'config.attributes["connection"].'
        )
    asyncio.run(_run_on_engine(engine))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
