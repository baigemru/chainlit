"""drop steps.modes, the column of a picker that was never drawn

The modes system reached from this column up to a TypeScript type and out
through ``cl.Mode`` in the public API, and nothing in between ever wrote to
it: the composer has no mode picker, ``set_modes`` was retired with the
socket.io vocabulary, and the only construction of a ``Mode`` lived in its
own test. A nullable column nobody writes costs nothing to read and
everything to believe in, so it goes with the rest of the feature.

Baseline stays as it is. A column added by ``0001`` and dropped by ``0005``
is the honest history; rewriting the baseline would make a database stamped
at ``0001`` disagree with the file that claims to describe it.

The downgrade puts the column back, empty. There is nothing to restore into
it -- that is the point.

Revision ID: 0005_drop_steps_modes
Revises: 0004_users_account
Create Date: 2026-09-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_drop_steps_modes"
down_revision: Union[str, None] = "0004_users_account"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "chainlit"

JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.drop_column("steps", "modes", schema=SCHEMA)


def downgrade() -> None:
    op.add_column("steps", sa.Column("modes", JSONB, nullable=True), schema=SCHEMA)
