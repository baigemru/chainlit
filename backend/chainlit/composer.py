"""``cl.Composer`` -- what the input says, per conversation.

Every call here replaces ``session.composer`` whole, writes it down, and
sends it as one ``composer.state`` frame. The model lives in
``chainlit.ws.composer``, where the transport can reach it for the replay.

It refuses to be a partial update. ``set(hint=...)`` is the whole state, so a
placeholder set earlier is gone after it: a frame is a projection of the
model, and a client that reconnected in the middle of two calls must be told
what the composer *is*, not what changed while it was away. An application
keeping one field across calls passes it again.

It does not decide what the words are, and it does not gate the composer:
whether the user may send is the turn's business (``task.indicator``), not
the placeholder's.
"""

from __future__ import annotations

from typing import Optional

from chainlit import persist
from chainlit.context import context
from chainlit.ws.composer import ComposerState

__all__ = ["Composer", "ComposerState"]


class Composer:
    """Say, for this conversation, what the composer is for."""

    @staticmethod
    async def set(
        placeholder: Optional[str] = None, hint: Optional[str] = None
    ) -> None:
        """Replace the composer's words in this conversation.

        ``placeholder`` replaces the translated placeholder of the input;
        ``hint`` is markdown under the composer, in the register of
        ``ChatProfile.composer_hint``, and replaces that hint on the empty
        screen as well. ``None`` is "as configured" -- the translation, the
        profile's hint -- and ``""`` is "nothing here", which is how a hint
        the profile set is taken away.

        The state survives a reload and a cold resume: it is written into the
        thread's metadata under ``__composer`` and read back by the engine,
        so ``on_chat_resume`` does not have to say it again.
        """
        _put(ComposerState(placeholder=placeholder, hint=hint))

    @staticmethod
    async def clear() -> None:
        """Put the composer back to what the configuration says."""
        _put(ComposerState())

    @staticmethod
    def state() -> ComposerState:
        """The model, live. Synchronous: it is read off the session."""
        return context.session.composer


def _put(state: ComposerState) -> None:
    session = context.session
    session.composer = state
    persist.patch_composer(session)
    session.send(state)
