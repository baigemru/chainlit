"""index the foreign keys, add threads.updatedAt and the element player columns

Three corrections to the deployed schema:

* every foreign key was unindexed, so deleting a thread had to sequential-scan
  steps, elements and feedbacks;
* the thread history sorts by "last activity", which had to be recomputed as
  MAX(steps."createdAt") with a GROUP BY over every step of every thread —
  ``threads."updatedAt"`` stores it instead, backfilled from that same
  aggregate;
* ``autoPlay``/``playerConfig`` are part of the element wire contract and had
  nowhere to land.

Revision ID: 0002_indexes_and_updated_at
Revises: 0001_baseline
Create Date: 2026-08-27
"""

from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_indexes_and_updated_at"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "chainlit"


def schema() -> Optional[str]:
    """The schema to build in, for the dialect actually connected.

    SQLite has no schemas: env.py folds `chainlit` into the default one with a
    ``schema_translate_map``, but alembic's ALTER helpers format the table
    name as a plain string and never consult that map, so the collapse has to
    be repeated here.
    """
    return None if op.get_bind().dialect.name == "sqlite" else SCHEMA


JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ISO_TEXT = sa.Text()


def upgrade() -> None:
    op.add_column(
        "threads", sa.Column("updatedAt", ISO_TEXT, nullable=True), schema=schema()
    )

    # Backfill. COALESCE, not a bare MAX: a thread whose steps were never
    # written (or were deleted) would otherwise keep a NULL here, and NULL
    # both drops rows out of the keyset comparison and sorts differently on
    # PostgreSQL and SQLite.
    threads = sa.table(
        "threads",
        sa.column("id", sa.Uuid()),
        sa.column("createdAt", ISO_TEXT),
        sa.column("updatedAt", ISO_TEXT),
        schema=schema(),
    )
    steps = sa.table(
        "steps",
        sa.column("threadId", sa.Uuid()),
        sa.column("createdAt", ISO_TEXT),
        schema=schema(),
    )
    newest_step = (
        sa.select(sa.func.max(steps.c.createdAt))
        .where(steps.c.threadId == threads.c.id)
        .scalar_subquery()
    )
    op.execute(
        threads.update().values(
            {"updatedAt": sa.func.coalesce(newest_step, threads.c.createdAt)}
        )
    )

    op.add_column(
        "elements", sa.Column("autoPlay", sa.Boolean(), nullable=True), schema=schema()
    )
    op.add_column(
        "elements", sa.Column("playerConfig", JSONB, nullable=True), schema=schema()
    )

    op.create_index("threads_user_id_idx", "threads", ["userId"], schema=schema())
    # The history page is a keyset scan over (userId, updatedAt, id).
    op.create_index(
        "threads_user_id_updated_at_idx",
        "threads",
        ["userId", "updatedAt", "id"],
        schema=schema(),
    )
    op.create_index("steps_thread_id_idx", "steps", ["threadId"], schema=schema())
    op.create_index("steps_parent_id_idx", "steps", ["parentId"], schema=schema())
    op.create_index("elements_thread_id_idx", "elements", ["threadId"], schema=schema())
    op.create_index("elements_for_id_idx", "elements", ["forId"], schema=schema())
    op.create_index("feedbacks_for_id_idx", "feedbacks", ["forId"], schema=schema())
    op.create_index(
        "feedbacks_thread_id_idx", "feedbacks", ["threadId"], schema=schema()
    )


def downgrade() -> None:
    op.drop_index("feedbacks_thread_id_idx", "feedbacks", schema=schema())
    op.drop_index("feedbacks_for_id_idx", "feedbacks", schema=schema())
    op.drop_index("elements_for_id_idx", "elements", schema=schema())
    op.drop_index("elements_thread_id_idx", "elements", schema=schema())
    op.drop_index("steps_parent_id_idx", "steps", schema=schema())
    op.drop_index("steps_thread_id_idx", "steps", schema=schema())
    op.drop_index("threads_user_id_updated_at_idx", "threads", schema=schema())
    op.drop_index("threads_user_id_idx", "threads", schema=schema())
    op.drop_column("elements", "playerConfig", schema=schema())
    op.drop_column("elements", "autoPlay", schema=schema())
    op.drop_column("threads", "updatedAt", schema=schema())
