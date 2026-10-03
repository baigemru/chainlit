"""The composer's words, as state the session owns.

The placeholder used to be a translation and the line under the field a
property of the chat profile, shown on the empty screen only. An application
running something long inside a thread had no way to say what typing now
does -- and no way to say it again after a reload, because nothing on the
server remembered that it had been said. So the composer joins the element
panel: it lives on the ``Session``, ``cl.Composer`` mutates it, the frame is
read off it, and a reconnect is handed it back.

Transport-side for the reason ``ws/sidebar.py`` is: ``cl.Composer`` reaches
for ``chainlit.context``, and ``ws/session.py`` cannot import its own
importer. What lives here is the model and what the thread row remembers of
it; nothing here reads the current context.

The model *is* the wire frame. The panel needs a model of its own because it
holds things the client is never told (which slots are rows); the composer
holds nothing the client is not told, and a second struct with the same two
fields would be a second place for them to drift.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import msgspec

from chainlit.logger import logger
from chainlit.protocol.server import ComposerState

__all__ = [
    "COMPOSER_META_KEY",
    "ComposerState",
    "composer_meta",
    "state_from_meta",
]

COMPOSER_META_KEY = "__composer"
"""Where the thread row remembers what the composer said.

Dunder-prefixed and one of ``persist.ENGINE_METADATA_KEYS``, like
``__sidebar``: it shares a dict with the application's ``user_session``, and
an application key of the same name must neither shadow it on the way out
nor be handed it on the way back in.
"""


def composer_meta(state: ComposerState) -> Optional[Dict[str, Any]]:
    """The composer as the thread row remembers it, or ``None`` for the default.

    ``None`` rather than ``{}`` because a metadata patch deletes a key mapped
    to ``None``: a composer put back to its default leaves no record behind,
    and a thread that never had one reads back exactly as one that cleared it.
    The tag is dropped -- it names the frame, not the state.
    """
    record = msgspec.to_builtins(state)
    record.pop("t", None)
    return record or None


def state_from_meta(meta: Optional[Mapping[str, Any]]) -> ComposerState:
    """The composer a cold resume rebuilds, out of what was written down.

    A record that does not parse is the default composer and a warning, not
    an error: it would surface inside the handshake, and a thread that cannot
    be resumed over a hand-edited row is worse than one whose placeholder
    came back as the translation.
    """
    if not meta:
        return ComposerState()
    try:
        return msgspec.convert(dict(meta), ComposerState)
    except msgspec.ValidationError, TypeError, ValueError:
        logger.warning("Ignoring a composer record that does not parse: %r", meta)
        return ComposerState()
