"""The TypeDecorator standing between datetimes and the schema's TEXT columns."""

import uuid
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import Text, cast, literal, select, update
from sqlalchemy.dialects import postgresql

from chainlit.persistence import StepRecord, UnitOfWork
from chainlit.persistence.models import STEPS, ISOTimestamp
from tests.persistence.conftest import at, iso, make_thread, new_id

DIALECT = postgresql.dialect()
COLUMN = ISOTimestamp()


def test_an_aware_datetime_binds_as_iso_with_a_trailing_z() -> None:
    bound = COLUMN.process_bind_param(at(hour=12, microsecond=123456), DIALECT)

    assert bound == "2026-08-27T12:00:00.123456Z"


def test_a_naive_datetime_is_taken_as_utc() -> None:
    """The legacy writer used ``datetime.now().isoformat() + "Z"``, so the
    stored strings are naive already; there is no offset to recover."""
    naive = datetime(2026, 8, 27, 12, 0, 0)

    assert COLUMN.process_bind_param(naive, DIALECT) == "2026-08-27T12:00:00.000000Z"


def test_an_offset_datetime_is_normalised_to_utc() -> None:
    moscow = datetime(2026, 8, 27, 15, 0, 0, tzinfo=timezone(timedelta(hours=3)))

    assert COLUMN.process_bind_param(moscow, DIALECT) == "2026-08-27T12:00:00.000000Z"


def test_a_stored_value_parses_back_to_an_aware_datetime() -> None:
    parsed = COLUMN.process_result_value("2026-08-27T12:00:00.123456Z", DIALECT)

    assert parsed is not None
    assert parsed == datetime(2026, 8, 27, 12, 0, 0, 123456, tzinfo=UTC)
    assert parsed.tzinfo is not None


def test_a_malformed_value_reads_back_as_none() -> None:
    """Rows written before this package existed are not guaranteed to parse.

    One unreadable timestamp must cost that one field, not the whole page of
    results it happened to be on.
    """
    assert COLUMN.process_result_value("0000-00-00 nonsense", DIALECT) is None
    assert COLUMN.process_result_value("", DIALECT) is None


def test_none_survives_in_both_directions() -> None:
    assert COLUMN.process_bind_param(None, DIALECT) is None
    assert COLUMN.process_result_value(None, DIALECT) is None


async def test_round_trip_through_the_database(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(
            id=step_id,
            type="run",
            thread_id=thread_id,
            created_at=iso(at(hour=12, microsecond=42)),
        )
    )

    # Cast, so the value is read as the text the column actually holds
    # rather than through the decorator that would parse it back.
    raw = await uow.session.execute(
        select(cast(STEPS.c["createdAt"], Text())).where(
            STEPS.c["id"] == uuid.UUID(step_id)
        )
    )
    assert raw.scalar_one() == "2026-08-27T12:00:00.000042Z"

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.created_at == "2026-08-27T12:00:00.000042Z"


async def test_a_malformed_stored_value_does_not_break_the_read(
    uow: UnitOfWork,
) -> None:
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(id=step_id, type="run", thread_id=thread_id, output="kept")
    )
    await uow.session.execute(
        update(STEPS)
        .where(STEPS.c["id"] == uuid.UUID(step_id))
        # A literal typed as plain text: this is a value the decorator would
        # never have written, which is the point — it models a legacy row.
        .values({"createdAt": literal("27/08/2026", Text())})
    )

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.created_at is None
    assert stored.output == "kept"
