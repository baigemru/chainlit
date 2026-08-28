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

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_indexes_and_updated_at"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "chainlit"


JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ISO_TEXT = sa.Text()


def upgrade() -> None:
    op.add_column(
        "threads", sa.Column("updatedAt", ISO_TEXT, nullable=True), schema=SCHEMA
    )

    # Backfill. COALESCE, not a bare MAX: a thread whose steps were never
    # written (or were deleted) would otherwise keep a NULL here, and NULL
    # drops rows out of the keyset comparison.
    threads = sa.table(
        "threads",
        sa.column("id", sa.Uuid()),
        sa.column("createdAt", ISO_TEXT),
        sa.column("updatedAt", ISO_TEXT),
        schema=SCHEMA,
    )
    steps = sa.table(
        "steps",
        sa.column("threadId", sa.Uuid()),
        sa.column("createdAt", ISO_TEXT),
        schema=SCHEMA,
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
        "elements", sa.Column("autoPlay", sa.Boolean(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "elements", sa.Column("playerConfig", JSONB, nullable=True), schema=SCHEMA
    )

    op.create_index("threads_user_id_idx", "threads", ["userId"], schema=SCHEMA)
    # The history page is a keyset scan over (userId, updatedAt, id).
    op.create_index(
        "threads_user_id_updated_at_idx",
        "threads",
        ["userId", "updatedAt", "id"],
        schema=SCHEMA,
    )
    op.create_index("steps_thread_id_idx", "steps", ["threadId"], schema=SCHEMA)
    op.create_index("steps_parent_id_idx", "steps", ["parentId"], schema=SCHEMA)
    op.create_index("elements_thread_id_idx", "elements", ["threadId"], schema=SCHEMA)
    op.create_index("elements_for_id_idx", "elements", ["forId"], schema=SCHEMA)
    op.create_index("feedbacks_for_id_idx", "feedbacks", ["forId"], schema=SCHEMA)
    op.create_index("feedbacks_thread_id_idx", "feedbacks", ["threadId"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("feedbacks_thread_id_idx", "feedbacks", schema=SCHEMA)
    op.drop_index("feedbacks_for_id_idx", "feedbacks", schema=SCHEMA)
    op.drop_index("elements_for_id_idx", "elements", schema=SCHEMA)
    op.drop_index("elements_thread_id_idx", "elements", schema=SCHEMA)
    op.drop_index("steps_parent_id_idx", "steps", schema=SCHEMA)
    op.drop_index("steps_thread_id_idx", "steps", schema=SCHEMA)
    op.drop_index("threads_user_id_updated_at_idx", "threads", schema=SCHEMA)
    op.drop_index("threads_user_id_idx", "threads", schema=SCHEMA)
    op.drop_column("elements", "playerConfig", schema=SCHEMA)
    op.drop_column("elements", "autoPlay", schema=SCHEMA)
    op.drop_column("threads", "updatedAt", schema=SCHEMA)
