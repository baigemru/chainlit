"""The registry, as the HTTP routes are allowed to see it.

Two routes act on a live session -- calling an action, and spooling an
upload -- and both are ordinary HTTP requests that have to find one. They
declare what they need as their own ``Protocol`` rather than importing the
registry, which is what keeps the HTTP half of this application from
depending on the transport half.

That leaves a seam, and this is it. The names differ on purpose either
side of it: the registry says ``thread_has_live_task`` because the
question is about a thread rather than about the session you happen to
hold, while a controller asking it already has a thread in hand and reads
better without the repetition. Renaming here costs one adapter and keeps
both modules honest; renaming either of them to match would make one of
them lie.
"""

from __future__ import annotations

from typing import Any, Optional, Set

from chainlit.ws.registry import SessionRegistry

__all__ = ["SessionLookup"]


class SessionLookup:
    """What ``ProjectController`` and ``FilesController`` are bound to."""

    def __init__(self, registry: SessionRegistry) -> None:
        self._registry = registry

    def get(self, session_id: str) -> Optional[Any]:
        """The live session with this id, or ``None``.

        The session itself, not the registry's entry: a controller has no
        business with the bookkeeping around it, and handing over the entry
        would let one write to it.
        """
        entry = self._registry.get(session_id)
        return None if entry is None else entry.session

    def has_live_task(self, thread_id: str) -> bool:
        """Whether any session on this thread is running something."""
        return self._registry.thread_has_live_task(thread_id)

    def protected_step_ids(self, thread_id: str) -> Set[str]:
        """Steps a live question on this thread is holding on screen."""
        return set(self._registry.protected_step_ids(thread_id))
