"""Run the table against every driver.

One driver today. The second arrives with the new transport, is added to
``DRIVERS``, and the table does not change -- which is the point.
"""

from typing import Any, Callable

import pytest

from . import legacy
from .cases import SCENARIOS
from .spec import Scenario

DRIVERS = {"socketio": legacy.run}


def _ids(scenario: Scenario) -> str:
    return scenario.name


@pytest.mark.parametrize("driver_name", sorted(DRIVERS))
@pytest.mark.parametrize("scenario", SCENARIOS, ids=_ids)
async def test_scenario(
    scenario: Scenario, driver_name: str, mock_session_factory: Callable[..., Any]
) -> None:
    result = await DRIVERS[driver_name](scenario, mock_session_factory)

    unmatched = result.ledger.find_in_order(scenario.expect)
    assert unmatched is None, (
        f"{scenario.name}\n  why: {scenario.why}\n"
        f"  missing (or out of order): {unmatched}\n"
        f"  sent: {result.ledger.frames}"
    )

    for tag in scenario.forbid:
        assert tag not in result.ledger.tags, (
            f"{scenario.name}\n  why: {scenario.why}\n"
            f"  forbidden frame {tag!r} was sent\n"
            f"  sent: {result.ledger.frames}"
        )

    if scenario.then is not None:
        scenario.then(result)
