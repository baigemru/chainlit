"""make feedbacks."forId" unique, after collapsing any duplicates

A step has one piece of feedback: the UI shows one thumb and one comment, and
both readers assume it. ``_steps_with_feedback`` outer-joins on ``forId``, so
a second row for the same step multiplies the step -- ``ThreadService.fetch``
raises ``MultipleResultsFound`` and ``get_detail`` returns the step twice in
the resume snapshot.

Nothing enforced that. The upsert keyed on the feedback's own ``id``, so a
client that had lost the id wrote a second row instead of updating the first,
and 0002 gave ``forId`` only a plain index.

The dedupe runs first and unconditionally: a deployed database may already
hold duplicates, and creating the unique index on one would fail the
migration halfway. Which row survives is arbitrary by construction -- the
table has no timestamp, so there is no "latest" to keep -- and the greatest
id *as text* is chosen because it is deterministic. Text, because PostgreSQL
has no ``MAX(uuid)``: the aggregate exists for text and this migration only
ever ran on SQLite before, where the column is already text.

Revision ID: 0003_one_feedback_per_step
Revises: 0002_indexes_and_updated_at
Create Date: 2026-08-28
"""

from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_one_feedback_per_step"
down_revision: Union[str, None] = "0002_indexes_and_updated_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "chainlit"


def schema() -> Optional[str]:
    """The schema to build in, for the dialect actually connected."""
    return None if op.get_bind().dialect.name == "sqlite" else SCHEMA


def _table() -> sa.TableClause:
    """The two columns this migration touches, in the connected schema."""
    return sa.table("feedbacks", sa.column("id"), sa.column("forId"), schema=schema())


def upgrade() -> None:
    table = _table()
    id_text = sa.cast(table.c["id"], sa.Text)
    survivors = (
        sa.select(sa.func.max(id_text)).select_from(table).group_by(table.c["forId"])
    )
    op.execute(sa.delete(table).where(id_text.not_in(survivors)))

    op.drop_index("feedbacks_for_id_idx", "feedbacks", schema=schema())
    op.create_index(
        "feedbacks_for_id_idx", "feedbacks", ["forId"], unique=True, schema=schema()
    )


def downgrade() -> None:
    op.drop_index("feedbacks_for_id_idx", "feedbacks", schema=schema())
    op.create_index("feedbacks_for_id_idx", "feedbacks", ["forId"], schema=schema())
