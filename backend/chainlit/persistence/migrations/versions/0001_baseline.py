"""baseline: the schema the legacy SQLAlchemy data layer deployed

Reproduces the tables as they exist in production today, so a fresh database
and a stamped production database converge on the same starting point. It
deliberately keeps the warts: ISO timestamps in TEXT columns, no index on any
foreign key. Migration 0002 is where the corrections live.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "chainlit"


def qualified(table: str) -> str:
    """A foreign-key target, schema-qualified."""
    return f"{SCHEMA}.{table}"


# Types are spelled out here instead of imported from the models: a migration
# has to keep describing the schema as it was on the day it ran, and the
# models will not.
JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
TEXT_ARRAY = sa.ARRAY(sa.Text())
# createdAt/start/end hold ISO strings with a literal trailing "Z".
ISO_TEXT = sa.Text()


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("metadata", JSONB, nullable=False),
        sa.Column("createdAt", ISO_TEXT, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("identifier", name="uq_users_identifier"),
        schema=SCHEMA,
    )

    op.create_table(
        "threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("createdAt", ISO_TEXT, nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("userId", sa.Uuid(), nullable=True),
        sa.Column("userIdentifier", sa.Text(), nullable=True),
        sa.Column("tags", TEXT_ARRAY, nullable=True),
        # Nullable, no server default: that is what the deployed schema has.
        # Upstream's documented DDL claims NOT NULL DEFAULT '{}'; prod disagrees,
        # and the baseline has to reproduce prod or `alembic check` never goes empty.
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("parentThreadId", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_threads"),
        sa.ForeignKeyConstraint(
            ["userId"],
            [qualified("users") + ".id"],
            name="fk_threads_userId_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parentThreadId"],
            [qualified("threads") + ".id"],
            name="fk_threads_parentThreadId_threads",
            ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "threads_parent_thread_id_idx",
        "threads",
        ["parentThreadId"],
        schema=SCHEMA,
    )

    op.create_table(
        "steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("threadId", sa.Uuid(), nullable=False),
        sa.Column("parentId", sa.Uuid(), nullable=True),
        sa.Column("streaming", sa.Boolean(), nullable=False),
        sa.Column("waitForAnswer", sa.Boolean(), nullable=True),
        sa.Column("isError", sa.Boolean(), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("tags", TEXT_ARRAY, nullable=True),
        sa.Column("input", sa.Text(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("createdAt", ISO_TEXT, nullable=True),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("start", ISO_TEXT, nullable=True),
        sa.Column("end", ISO_TEXT, nullable=True),
        sa.Column("generation", JSONB, nullable=True),
        sa.Column("showInput", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("indent", sa.Integer(), nullable=True),
        sa.Column("defaultOpen", sa.Boolean(), nullable=True),
        sa.Column("modes", JSONB, nullable=True),
        sa.Column("autoCollapse", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_steps"),
        sa.ForeignKeyConstraint(
            ["threadId"],
            [qualified("threads") + ".id"],
            name="fk_steps_threadId_threads",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "elements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("threadId", sa.Uuid(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("chainlitKey", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("display", sa.Text(), nullable=True),
        sa.Column("objectKey", sa.Text(), nullable=True),
        sa.Column("size", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("forId", sa.Uuid(), nullable=True),
        sa.Column("mime", sa.Text(), nullable=True),
        sa.Column("props", JSONB, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_elements"),
        sa.ForeignKeyConstraint(
            ["threadId"],
            [qualified("threads") + ".id"],
            name="fk_elements_threadId_threads",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "feedbacks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("forId", sa.Uuid(), nullable=False),
        sa.Column("threadId", sa.Uuid(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_feedbacks"),
        sa.ForeignKeyConstraint(
            ["forId"],
            [qualified("steps") + ".id"],
            name="fk_feedbacks_forId_steps",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["threadId"],
            [qualified("threads") + ".id"],
            name="fk_feedbacks_threadId_threads",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("feedbacks", schema=SCHEMA)
    op.drop_table("elements", schema=SCHEMA)
    op.drop_table("steps", schema=SCHEMA)
    op.drop_index("threads_parent_thread_id_idx", "threads", schema=SCHEMA)
    op.drop_table("threads", schema=SCHEMA)
    op.drop_table("users", schema=SCHEMA)
