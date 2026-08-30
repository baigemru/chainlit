"""A translated database error must leave its cause in the server log.

advanced_alchemy turns every driver error into a 409 whose body says only
"There was an issue processing the statement"; without this, an outage's
only trace was that sentence.
"""

import logging

import pytest
from advanced_alchemy.extensions.litestar import exceptions
from sqlalchemy import text

from chainlit.persistence.config import UnitOfWork


async def test_a_failing_statement_logs_its_cause(
    uow: UnitOfWork, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="chainlit"):
        with pytest.raises(exceptions.RepositoryError):
            await uow.threads.execute(text("SELECT 1/0"))

    [record] = [r for r in caplog.records if "Database statement failed" in r.message]
    assert "division by zero" in record.message
    assert record.exc_info is not None


async def test_an_expected_empty_result_stays_quiet(
    uow: UnitOfWork, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="chainlit"):
        with pytest.raises(exceptions.InvalidRequestError):
            await uow.threads.fetch_one(text("SELECT 1 WHERE false"))

    assert not [r for r in caplog.records if "Database statement failed" in r.message]
