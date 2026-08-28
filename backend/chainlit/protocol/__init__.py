"""Typed wire protocol for the Chainlit websocket transport.

Pure data plus a codec. Nothing here imports ``chainlit.socket``,
``chainlit.emitter`` or ``chainlit.session`` — nor any other chainlit
module — so the protocol can be tested, reviewed and versioned on its own,
and so the client rewrite has a single file to read.

See ``README.md`` in this package for the old-event-name → tag map.
"""

from chainlit.protocol import client, codec, payloads, server
from chainlit.protocol.client import CLIENT_TAGS, ClientMsg
from chainlit.protocol.codec import (
    MAX_FRAME_BYTES,
    CloseCode,
    ErrorCode,
    decode_client,
    decode_server,
    encode_client,
    encode_server,
)
from chainlit.protocol.server import SERVER_TAGS, ServerMsg

__all__ = [
    "CLIENT_TAGS",
    "MAX_FRAME_BYTES",
    "SERVER_TAGS",
    "ClientMsg",
    "CloseCode",
    "ErrorCode",
    "ServerMsg",
    "client",
    "codec",
    "decode_client",
    "decode_server",
    "encode_client",
    "encode_server",
    "payloads",
    "server",
]
