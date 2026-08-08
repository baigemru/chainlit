"""In-memory store for transit messages handed from one session to the next.

`emitter.set_chat_profile(transit_message=...)` parks a value here under the
emitting session's id; the frontend claims it for the session it is about to
open (`claim_transit_message`), and `connection_successful` moves it into
`user_sessions` before `on_chat_start` runs. The value never travels through
the browser.

This module must stay free of chainlit imports: both `emitter` and `socket`
import it, and anything heavier would create an import cycle.
"""

import time
from typing import Any, Dict, NamedTuple, Optional

# A record that was never claimed (dead socket, outdated frontend bundle)
# must not survive long enough to leak into an unrelated future session.
TRANSIT_TTL_SECONDS = 120

# Backstop against unbounded growth. New records are rejected, not evicted:
# a session spamming profile switches must not push out other sessions'
# not-yet-claimed messages.
MAX_TRANSIT_RECORDS = 1000

# `None` is the "no record" answer of `pop`, while `""`, `0` and `False` are
# valid transit values — so presence needs its own marker.
NO_TRANSIT = object()


class _Record(NamedTuple):
    value: Any
    owner: Optional[str]
    created_at: float


_records: Dict[str, _Record] = {}


def _sweep() -> None:
    deadline = time.monotonic() - TRANSIT_TTL_SECONDS
    for session_id in [
        session_id
        for session_id, record in _records.items()
        if record.created_at < deadline
    ]:
        del _records[session_id]


def store(session_id: str, value: Any, owner: Optional[str]) -> None:
    """Park `value` for `session_id`; `None` clears a previously parked one."""
    _sweep()
    if value is None:
        _records.pop(session_id, None)
        return
    if session_id not in _records and len(_records) >= MAX_TRANSIT_RECORDS:
        return
    _records[session_id] = _Record(value, owner, time.monotonic())


def reassign(old_id: str, new_id: str) -> None:
    """Move a parked record to the session id that will actually connect."""
    _sweep()
    record = _records.pop(old_id, None)
    if record is not None:
        _records[new_id] = record


def pop(session_id: str, owner: Optional[str]) -> Any:
    """Take the record for `session_id`, or NO_TRANSIT when there is none.

    Only the owner that parked the record may take it; an expired or foreign
    record answers NO_TRANSIT as well.
    """
    _sweep()
    record = _records.pop(session_id, None)
    if record is None:
        return NO_TRANSIT
    if record.owner != owner:
        return NO_TRANSIT
    return record.value


def clear() -> None:
    """Drop every record. Test isolation only."""
    _records.clear()
