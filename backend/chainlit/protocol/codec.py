"""Encoders, decoders and wire-level enums.

Two frame kinds:

``TEXT``
    JSON. Everything except the two audio messages.

``BINARY``
    msgpack. Only ``audio.out`` (server) and ``audio.in`` (client). JSON
    would base64 their ``bytes`` payload, inflating every audio chunk by a
    third for no benefit — msgpack carries raw bytes natively.

The decoders are module-level singletons: building a ``msgspec.Decoder``
compiles the type once, and rebuilding it per frame is the single easiest
way to make this protocol slower than the one it replaces.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Final

import msgspec

from chainlit.protocol.client import AudioIn, ClientMsg
from chainlit.protocol.server import AudioOut, ServerMsg

__all__ = [
    "BINARY_CLIENT_TAGS",
    "BINARY_SERVER_TAGS",
    "MAX_TEXT_FRAME_BYTES",
    "CloseCode",
    "ErrorCode",
    "FrameKind",
    "client_frame_kind",
    "decode_client",
    "decode_server",
    "encode_client",
    "encode_server",
    "server_frame_kind",
]


class FrameKind(StrEnum):
    """Which websocket frame type a message travels in."""

    TEXT = "text"
    BINARY = "binary"


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

    RPC_TIMEOUT = "rpc_timeout"
    RATE_LIMITED = "rate_limited"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    INTERNAL = "internal"


MAX_TEXT_FRAME_BYTES: Final[int] = 8 * 1024 * 1024
"""Refuse a text frame above this; answer with ``CloseCode.FRAME_TOO_LARGE``."""


BINARY_SERVER_TAGS: Final[frozenset[str]] = frozenset({"audio.out"})
BINARY_CLIENT_TAGS: Final[frozenset[str]] = frozenset({"audio.in"})


_json_encoder: Final = msgspec.json.Encoder()
_msgpack_encoder: Final = msgspec.msgpack.Encoder()

_server_json_decoder: Final = msgspec.json.Decoder(ServerMsg)
_server_msgpack_decoder: Final = msgspec.msgpack.Decoder(ServerMsg)
_client_json_decoder: Final = msgspec.json.Decoder(ClientMsg)
_client_msgpack_decoder: Final = msgspec.msgpack.Decoder(ClientMsg)


def server_frame_kind(msg: ServerMsg) -> FrameKind:
    """Which frame kind ``msg`` must be sent in."""
    return FrameKind.BINARY if isinstance(msg, AudioOut) else FrameKind.TEXT


def client_frame_kind(msg: ClientMsg) -> FrameKind:
    """Which frame kind ``msg`` must be sent in."""
    return FrameKind.BINARY if isinstance(msg, AudioIn) else FrameKind.TEXT


def encode_server(msg: ServerMsg) -> bytes:
    """Serialize a server message, picking the encoder from its type."""
    if server_frame_kind(msg) is FrameKind.BINARY:
        return _msgpack_encoder.encode(msg)
    return _json_encoder.encode(msg)


def encode_client(msg: ClientMsg) -> bytes:
    """Serialize a client message, picking the encoder from its type."""
    if client_frame_kind(msg) is FrameKind.BINARY:
        return _msgpack_encoder.encode(msg)
    return _json_encoder.encode(msg)


def decode_server(data: bytes, kind: FrameKind = FrameKind.TEXT) -> ServerMsg:
    """Parse a server message out of a received frame.

    ``kind`` comes from the transport, which is the only thing that knows
    whether the frame arrived as text or binary — guessing from the bytes
    is how a protocol grows a content sniffer.

    Raises:
        msgspec.ValidationError: unknown tag, wrong branch, or a field of
            the wrong type.
        msgspec.DecodeError: malformed JSON / msgpack.
    """
    if kind is FrameKind.BINARY:
        return _server_msgpack_decoder.decode(data)
    return _server_json_decoder.decode(data)


def decode_client(data: bytes, kind: FrameKind = FrameKind.TEXT) -> ClientMsg:
    """Parse a client message out of a received frame. See ``decode_server``."""
    if kind is FrameKind.BINARY:
        return _client_msgpack_decoder.decode(data)
    return _client_json_decoder.decode(data)
