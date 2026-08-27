"""The table. One module per behaviour family; ``SCENARIOS`` is their union."""

from typing import Tuple

from ..spec import Scenario
from .ask import ASK_SCENARIOS
from .orphans import ORPHAN_SCENARIOS, PARENT_SCENARIOS
from .resync import RESYNC_SCENARIOS
from .transcript import TRANSCRIPT_SCENARIOS

SCENARIOS: Tuple[Scenario, ...] = (
    ASK_SCENARIOS
    + TRANSCRIPT_SCENARIOS
    + ORPHAN_SCENARIOS
    + PARENT_SCENARIOS
    + RESYNC_SCENARIOS
)

__all__ = ["SCENARIOS"]
