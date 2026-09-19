"""add users.account, where @cl.account stores what a user saved

The account page keeps its values in a column of their own, not under a key in
``users.metadata``: ``upsert_user`` replaces ``metadata`` wholesale on every
sign-in (``set_={"metadata": excluded.metadata}``), so anything the engine
wrote there would be gone at the user's next login, and a reserved-key filter
on top of that would be a patch around the wrong storage.

``NOT NULL DEFAULT '{}'``, so every existing row is valid the moment the column
appears and no backfill has to run over a live table.

Revision ID: 0004_users_account
Revises: 0003_one_feedback_per_step
Create Date: 2026-09-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_users_account"
down_revision: Union[str, None] = "0003_one_feedback_per_step"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "chainlit"

JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "account",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("users", "account", schema=SCHEMA)
