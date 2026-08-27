"""Pin the models to the deployed production schema.

The acceptance gate for the persistence models is that `alembic check` against
a restore of production comes back empty. That check needs a live database, so
it cannot run in unit CI -- this file is its offline stand-in: the column
inventory below was read straight out of `chainlit_panda` on 2026-08-27 with

    SELECT table_name, column_name, data_type, is_nullable
    FROM information_schema.columns WHERE table_schema = 'chainlit';

A mismatch here means the next `alembic check` will report a diff, and the
baseline revision will try to reshape a live table.

Two disagreements with upstream Chainlit's documented DDL are deliberate and
recorded here because they are easy to "fix" back into a bug:

- `threads.metadata` is NULLABLE with no server default in production, while
  the upstream DDL says `JSONB NOT NULL DEFAULT '{}'`.
- `steps.disableFeedback` and the `elements.autoPlay`/`playerConfig` pair do
  not exist in production. The first stays absent; the latter two are added by
  revision 0002 and so are expected here.
"""

from typing import Dict, Set, Tuple

import pytest

from chainlit.persistence import models

# (column name, nullable) exactly as deployed. Types are asserted separately,
# because the mapped types are dialect-dependent and the names are not.
PRODUCTION_COLUMNS: Dict[str, Set[Tuple[str, bool]]] = {
    "users": {
        ("id", False),
        ("identifier", False),
        ("metadata", False),
        ("createdAt", True),
    },
    "threads": {
        ("id", False),
        ("createdAt", True),
        ("name", True),
        ("userId", True),
        ("userIdentifier", True),
        ("tags", True),
        ("metadata", True),
        ("parentThreadId", True),
    },
    "steps": {
        ("id", False),
        ("name", False),
        ("type", False),
        ("threadId", False),
        ("parentId", True),
        ("streaming", False),
        ("waitForAnswer", True),
        ("isError", True),
        ("metadata", True),
        ("tags", True),
        ("input", True),
        ("output", True),
        ("createdAt", True),
        ("command", True),
        ("start", True),
        ("end", True),
        ("generation", True),
        ("showInput", True),
        ("language", True),
        ("indent", True),
        ("defaultOpen", True),
        ("modes", True),
        ("autoCollapse", True),
    },
    "elements": {
        ("id", False),
        ("threadId", True),
        ("type", True),
        ("url", True),
        ("chainlitKey", True),
        ("name", False),
        ("display", True),
        ("objectKey", True),
        ("size", True),
        ("page", True),
        ("language", True),
        ("forId", True),
        ("mime", True),
        ("props", True),
    },
    "feedbacks": {
        ("id", False),
        ("forId", False),
        ("threadId", False),
        ("value", False),
        ("comment", True),
    },
}

# Columns revision 0002 adds. Present on the models, absent from production
# until the revision is applied.
ADDED_BY_0002: Dict[str, Set[Tuple[str, bool]]] = {
    "threads": {("updatedAt", True)},
    "elements": {("autoPlay", True), ("playerConfig", True)},
}

MODELS = {
    "users": models.User,
    "threads": models.Thread,
    "steps": models.Step,
    "elements": models.Element,
    "feedbacks": models.Feedback,
}


@pytest.mark.parametrize("table_name", sorted(PRODUCTION_COLUMNS))
def test_model_columns_match_production(table_name: str) -> None:
    expected = PRODUCTION_COLUMNS[table_name] | ADDED_BY_0002.get(table_name, set())
    actual = {
        (column.name, column.nullable is True)
        for column in MODELS[table_name].__table__.columns
    }

    missing = expected - actual
    extra = actual - expected

    assert not missing, f"{table_name}: model is missing {sorted(missing)}"
    assert not extra, (
        f"{table_name}: model declares {sorted(extra)}, which production does not "
        f"have -- autogenerate would propose adding them to a live table"
    )


def test_disable_feedback_stays_absent() -> None:
    """Upstream's DDL carries steps.disableFeedback; production never had it.

    Declaring it would make autogenerate propose a new column; omitting a column
    production *does* have would make it propose a DROP. Both are one-line
    mistakes, so this asserts the one that upstream tempts you into.
    """
    assert "disableFeedback" not in models.Step.__table__.columns


def test_metadata_attribute_is_renamed_but_column_is_not() -> None:
    """`metadata` is reserved on DeclarativeBase; the column name must survive."""
    for model in (models.User, models.Thread, models.Step):
        assert "metadata" in model.__table__.columns
        assert model.metadata_.property.columns[0].name == "metadata"
