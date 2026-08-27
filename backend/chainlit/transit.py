"""In-memory store for transit messages handed from one session to the next.

`emitter.set_chat_profile(transit_message=...)` parks a value here under the
id it mints for the session that is about to open, and
`connection_successful` moves it into `user_sessions` before
`on_chat_start` runs. The value never travels through the browser; only the
id does, so the record is never keyed to the session that is going away and
cannot be swallowed by it when its socket flaps.

Besides the message, a record carries the emitting session's thread id
(`parent`) so the thread spawned by the switch can point back at the thread
it came from — a record may hold a parent and no message at all.

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
    # Thread id of the session that parked the record; the switch target
    # stores it as the new thread's parentThreadId.
    parent: Optional[str]


_records: Dict[str, _Record] = {}


def _sweep() -> None:
    deadline = time.monotonic() - TRANSIT_TTL_SECONDS
    for session_id in [
        session_id
        for session_id, record in _records.items()
        if record.created_at < deadline
    ]:
        del _records[session_id]


def store(
    session_id: str,
    value: Any,
    owner: Optional[str],
    parent: Optional[str] = None,
) -> None:
    """Park a record for `session_id`.

    `value` is the transit message (`None` means "no message"), `parent` the
    emitting session's thread id. With nothing to hand over — both `None` —
    a previously parked record is cleared instead.
    """
    _sweep()
    if value is None and parent is None:
        _records.pop(session_id, None)
        return
    if session_id not in _records and len(_records) >= MAX_TRANSIT_RECORDS:
        return
    _records[session_id] = _Record(value, owner, time.monotonic(), parent)


def discard(session_id: str) -> None:
    """Drop the record parked for `session_id`, if any.

    Every park mints a fresh key, so without dropping the previous one a
    session would accumulate records instead of overwriting its single slot
    — breaking both the MAX_TRANSIT_RECORDS backstop and the documented
    "passing None revokes what an earlier call parked" contract.
    """
    _sweep()
    _records.pop(session_id, None)


def pop(session_id: str, owner: Optional[str]) -> Any:
    """Take the record for `session_id`, or NO_TRANSIT when there is none.

    Returns the record itself (`.value`, `.parent`), not just the message.
    Only the owner that parked the record may take it; an expired or foreign
    record answers NO_TRANSIT as well.
    """
    _sweep()
    record = _records.pop(session_id, None)
    if record is None:
        return NO_TRANSIT
    if record.owner != owner:
        return NO_TRANSIT
    return record


def clear() -> None:
    """Drop every record. Test isolation only."""
    _records.clear()
