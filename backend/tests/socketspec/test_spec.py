"""Run the table against every driver.

The socket.io driver went with the transport it drove. The driver for the
native websocket is added to ``DRIVERS``, and the table does not change --
which is the point. Until then the table collects as skipped, not as green.
"""

import inspect

import pytest

from .cases import SCENARIOS
from .spec import Result, Scenario

DRIVERS: dict = {}


def _ids(scenario: Scenario) -> str:
    return scenario.name


@pytest.mark.parametrize("driver_name", sorted(DRIVERS))
@pytest.mark.parametrize("scenario", SCENARIOS, ids=_ids)
async def test_scenario(
    scenario: Scenario, driver_name: str, request: pytest.FixtureRequest
) -> None:
    drive = DRIVERS[driver_name](request)
    outcome = drive(scenario)
    result: Result = await outcome if inspect.isawaitable(outcome) else outcome

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
