"""Client → server messages.

One ``msgspec.Struct`` per message, gathered into the ``ClientMsg`` tagged
union discriminated on ``t`` — the same tag field the server union uses, so
a transport can dispatch both directions with one decoder shape.
"""

from __future__ import annotations

from typing import Literal, Union, get_args

import msgspec

from chainlit.protocol.payloads import AskReplyValue, FileRef, Step

__all__ = [
    "CLIENT_TAGS",
    "AskReply",
    "ClientMsg",
    "HeartbeatAck",
    "Hello",
    "MessageSend",
    "ProfileSwitch",
    "SessionClear",
    "Stop",
]


class _Msg(msgspec.Struct, tag_field="t", rename="camel", omit_defaults=True):
    """Base of every client message. ``t`` carries the tag."""


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


class Hello(_Msg, tag="hello"):
    """The handshake, as the first frame on an open socket.

    Collapses socket.io's ``connect`` auth dict and the separate
    ``connection_successful`` event into one message. Splitting them was
    the source of the ordering hazard the old code works around all over
    ``socket.py``: the client flushed its send buffer *before* emitting
    ``connection_successful``, so buffered events could reach a
    half-initialised session.
    """

    session_id: str
    client_type: Literal["webapp", "copilot", "teams", "slack", "discord"] = "webapp"
    thread_id: str | None = None
    chat_profile: str | None = None
    user_env: dict[str, str] = {}
    # True only on the first connect after a full page load. A reload means
    # a fresh chat unless the old session still has live work to rescue.
    page_load: bool = False
    protocol_version: int = 1


class HeartbeatAck(_Msg, tag="hb.ack"):
    """Answer to a server ``hb``. New in this protocol."""

    seq: int = 0


class SessionClear(_Msg, tag="session.clear"):
    """Drop the session on disconnect instead of keeping it warm."""


class Stop(_Msg, tag="stop"):
    """Cancel the running task, the hooks and any pending ask."""


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------


class MessageSend(_Msg, tag="message.send"):
    message: Step
    file_references: list[FileRef] = []


# --------------------------------------------------------------------------
# Asks
# --------------------------------------------------------------------------


class AskReply(_Msg, tag="ask.reply"):
    """The user's answer to ``ask.start``.

    A plain message, never a request/response ack: it must survive being
    buffered across a reconnect, which a socket.io ack (bound to the socket
    id) does not.
    """

    step_id: str
    value: AskReplyValue


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------


class ProfileSwitch(_Msg, tag="profile.switch"):
    """Ask the server to hot-swap the profile in place.

    The counterpart of the server's ``profile.changed``, which is the only
    writer of the client's profile atom. Distinct from the server's
    ``session.handoff``, which tears the session down instead.
    """

    chat_profile: str


ClientMsg = Union[
    Hello,
    HeartbeatAck,
    SessionClear,
    Stop,
    MessageSend,
    AskReply,
    ProfileSwitch,
]

CLIENT_TAGS: frozenset[str] = frozenset(
    str(branch.__struct_config__.tag) for branch in get_args(ClientMsg)
)
