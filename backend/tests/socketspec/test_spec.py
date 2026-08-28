"""Run the table against every driver.

The socket.io driver went with the transport it drove. The driver for the
native websocket runs the same table -- which is the point.

Two kinds of row do not run. A ``superseded`` row encodes a decision the
rebuild reversed on purpose and is skipped with the reversal as the reason.
A row in a driver's ``KNOWN_BUGS`` exposes a defect in the implementation it
drives and is ``xfail(strict)``: it flips back to a failure -- and the entry
has to go -- the moment the defect is fixed.
"""

import inspect
from typing import Iterator

import pytest

from .cases import SCENARIOS
from .native import KNOWN_BUGS as NATIVE_KNOWN_BUGS, NativeDriver
from .spec import Result, Scenario

DRIVERS: dict = {"native": NativeDriver}

KNOWN_BUGS: dict = {"native": NATIVE_KNOWN_BUGS}


def _cases() -> Iterator[object]:
    for driver_name in sorted(DRIVERS):
        for scenario in SCENARIOS:
            marks = []
            if scenario.superseded:
                marks.append(
                    pytest.mark.skip(reason=f"superseded: {scenario.superseded}")
                )
            elif reason := KNOWN_BUGS[driver_name].get(scenario.name):
                marks.append(pytest.mark.xfail(strict=True, reason=reason))
            yield pytest.param(
                scenario, driver_name, id=f"{scenario.name}-{driver_name}", marks=marks
            )


@pytest.mark.parametrize(("scenario", "driver_name"), list(_cases()))
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
