"""The live sessions, as the HTTP routes are allowed to see them.

One declaration, shared by every controller, satisfied by the real
``chainlit.ws.registry.SessionRegistry`` without an adapter. The routes name
only what they touch, so a test can hand them a stub with five attributes
instead of building a websocket; the transport keeps everything else to
itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import AbstractSet, Any, Mapping, Optional, Protocol, runtime_checkable

__all__ = ["LiveSession", "SessionRegistry"]


@runtime_checkable
class LiveSession(Protocol):
    """The part of a session a route may read.

    Properties rather than attributes: a Protocol attribute is invariant,
    and the real session keeps ``dict``s where a route needs a ``Mapping``.
    """

    @property
    def user(self) -> Optional[Any]:
        """The user the socket belongs to; ``None`` without authentication.

        Only ``.identifier`` is read.
        """
        ...

    @property
    def files_dir(self) -> Path:
        """The per-session spool directory, created on first use."""
        ...

    @property
    def files(self) -> Mapping[str, Mapping[str, Any]]:
        """Uploaded files by id, each with a ``path`` and a ``type``."""
        ...

    @property
    def files_spec(self) -> Mapping[str, Any]:
        """The upload constraints of each pending file ask, keyed by the
        id of the message that asked."""
        ...

    async def persist_file(
        self, name: str, mime: str, *, content: bytes
    ) -> Mapping[str, Any]:
        """Spool the bytes and return the reference the client uploads to."""
        ...

    async def call_action(self, action: Mapping[str, Any]) -> Any:
        """Run the app's callback for this action; ``LookupError`` if none."""
        ...

    async def release(self) -> None:
        """Give the conversation up: out of the registry, then torn down.

        What a route calls when the thread stops existing underneath a live
        session.
        """
        ...


@runtime_checkable
class SessionRegistry(Protocol):
    """Bound by the application under the dependency key ``sessions``.

    Both lookups exist because both identities are real: the routes that act
    on something a session rendered -- an action button, a custom element --
    are handed the session handle, and the routes that act on a conversation
    are handed the thread. A thread holds at most one session, which is what
    makes the last two questions single lookups.
    """

    def find(self, session_id: str) -> Optional[LiveSession]:
        """The live session with this handle, or ``None``."""
        ...

    def find_thread(self, thread_id: Optional[str]) -> Optional[LiveSession]:
        """The live session in this conversation, or ``None``."""
        ...

    def has_live_task(self, thread_id: Optional[str]) -> bool:
        """Whether the session on this thread is running something."""
        ...

    def protected_step_ids(self, thread_id: Optional[str]) -> AbstractSet[str]:
        """Steps a live question on this thread is holding on screen."""
        ...
