"""Encoders, decoders and wire-level enums.

Every frame is JSON in a text frame. There is no binary branch: the only
messages that ever needed one were the two audio chunks, and audio is gone
-- file upload and download were already HTTP, so nothing else on this
wire carries ``bytes``.

The decoders are module-level singletons: building a ``msgspec.Decoder``
compiles the type once, and rebuilding it per frame is the single easiest
way to make this protocol slower than the one it replaces.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Final

import msgspec

from chainlit.protocol.client import ClientMsg
from chainlit.protocol.server import ServerMsg

__all__ = [
    "MAX_FRAME_BYTES",
    "CloseCode",
    "ErrorCode",
    "decode_client",
    "decode_server",
    "encode_client",
    "encode_server",
]


class CloseCode(IntEnum):
    """Websocket close codes in the private-use 4000-4999 range."""

    BAD_HANDSHAKE = 4400
    """The first frame was not a well-formed ``hello``."""

    UNAUTHENTICATED = 4401
    """No valid credentials on a server that requires login."""

    SESSION_FORBIDDEN = 4403
    """The session id belongs to another user."""

    THREAD_FORBIDDEN = 4404
    """The thread id is not readable by this user."""

    HEARTBEAT_TIMEOUT = 4408
    """No ``hb.ack`` within the deadline."""

    SUPERSEDED = 4409
    """Another connection took this session over."""

    FRAME_TOO_LARGE = 4413
    """A frame exceeded the transport limit."""

    BACKLOG_EXCEEDED = 4429
    """The client stopped reading and the outbound backlog filled.

    A policy close, not a failure, and deliberately not ``INTERNAL``: the
    server is fine and the session is intact. Every tag on this wire is a
    delta -- an update patches a bubble that must already exist, a token
    appends to one -- so a client that misses a frame is wrong from then
    on, and with no acks neither end can tell. Closing hands it the
    recovery path it already has: reconnect, ``hello``, resume, and one
    frame later the view is correct again. The client must therefore
    retry this code.
    """

    INTERNAL = 4500
    """Unexpected server-side failure."""


class ErrorCode(StrEnum):
    """Payload of a ``ServerMsg`` ``error``.

    Distinct from ``CloseCode``: an error leaves the socket open. A failure
    that must also close it sends both.
    """

    BAD_MESSAGE = "bad_message"
    """The frame decoded, but the message is not valid here."""

    UNKNOWN_TAG = "unknown_tag"
    """The ``t`` tag is not part of this protocol version."""

    UNAUTHENTICATED = "unauthenticated"
    UNAUTHORIZED = "unauthorized"

    SESSION_NOT_FOUND = "session_not_found"
    THREAD_NOT_FOUND = "thread_not_found"

    ASK_SLOT_BUSY = "ask_slot_busy"
    """A second concurrent ask was refused; see ``features.strict_ask_slot``."""

    ASK_UNKNOWN = "ask_unknown"
    """An ``ask.reply`` addressed a step with no live ask."""

    PROFILE_FORBIDDEN = "profile_forbidden"
    """The requested chat profile does not exist for this user."""

    RATE_LIMITED = "rate_limited"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    INTERNAL = "internal"


MAX_FRAME_BYTES: Final[int] = 8 * 1024 * 1024
"""Refuse a frame above this; answer with ``CloseCode.FRAME_TOO_LARGE``."""


_encoder: Final = msgspec.json.Encoder()
_server_decoder: Final = msgspec.json.Decoder(ServerMsg)
_client_decoder: Final = msgspec.json.Decoder(ClientMsg)


def encode_server(msg: ServerMsg) -> bytes:
    """Serialize a server message."""
    return _encoder.encode(msg)


def encode_client(msg: ClientMsg) -> bytes:
    """Serialize a client message."""
    return _encoder.encode(msg)


def decode_server(data: bytes) -> ServerMsg:
    """Parse a server message out of a received frame.

    Raises:
        msgspec.ValidationError: unknown tag, wrong branch, or a field of
            the wrong type.
        msgspec.DecodeError: malformed JSON.
    """
    return _server_decoder.decode(data)


def decode_client(data: bytes) -> ClientMsg:
    """Parse a client message out of a received frame. See ``decode_server``."""
    return _client_decoder.decode(data)
