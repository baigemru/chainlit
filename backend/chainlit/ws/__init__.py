"""The websocket transport.

``registry.py``  an in-memory registry of live sessions plus the policy
                 that reads it. It imports nothing from the rest of
                 ``chainlit`` and nothing from the transport, so it can be
                 tested and swapped on its own -- which matters because it
                 is the one thing here that would have to become shared
                 storage if this ever ran on more than one process.
``outbound.py``  the send side of one connection: a bounded queue with a
                 single writer task, which owns frame ordering, the
                 overflow policy and the close sequence.

No process-wide instance is created here on purpose. Who owns the registry
is a lifecycle question -- it belongs to whatever starts and stops the
server -- and a module-level singleton would be the old ``ws_sessions_id``
global again, shared between tests that must not see each other's sessions.
"""

from chainlit.ws.outbound import Outbound, Overflow
from chainlit.ws.registry import (
    Claim,
    ClaimOutcome,
    SessionEntry,
    SessionRegistry,
    SessionView,
    has_live_work,
    is_abandoned_ask_session,
    is_disconnected,
    is_owned_by,
    is_parked_on_live_ask,
)

__all__ = [
    "Claim",
    "ClaimOutcome",
    "Outbound",
    "Overflow",
    "SessionEntry",
    "SessionRegistry",
    "SessionView",
    "has_live_work",
    "is_abandoned_ask_session",
    "is_disconnected",
    "is_owned_by",
    "is_parked_on_live_ask",
]
