"""Codec plumbing: frame selection and the wire-level enums."""

from __future__ import annotations

import msgspec
import pytest

from chainlit.protocol import client as c, server as s
from chainlit.protocol.codec import (
    MAX_FRAME_BYTES,
    CloseCode,
    ErrorCode,
    decode_client,
    decode_server,
    encode_server,
)


def test_close_codes_match_the_specified_values() -> None:
    assert CloseCode.BAD_HANDSHAKE == 4400
    assert CloseCode.UNAUTHENTICATED == 4401
    assert CloseCode.SESSION_FORBIDDEN == 4403
    assert CloseCode.THREAD_FORBIDDEN == 4404
    assert CloseCode.HEARTBEAT_TIMEOUT == 4408
    assert CloseCode.SUPERSEDED == 4409
    assert CloseCode.FRAME_TOO_LARGE == 4413
    assert CloseCode.BACKLOG_EXCEEDED == 4429
    assert CloseCode.INTERNAL == 4500


def test_close_codes_stay_in_the_private_range() -> None:
    assert all(4000 <= int(code) <= 4999 for code in CloseCode)


def test_error_codes_are_unique_strings() -> None:
    values = [code.value for code in ErrorCode]
    assert len(values) == len(set(values))
    assert all(v == v.lower() and " " not in v for v in values)


def test_an_error_message_accepts_an_error_code() -> None:
    msg = s.Error(code=ErrorCode.THREAD_NOT_FOUND.value, message="gone", fatal=True)
    assert decode_server(encode_server(msg)) == msg


def test_max_frame_is_declared() -> None:
    assert MAX_FRAME_BYTES > 0


def test_the_wire_is_json_only() -> None:
    """No binary branch survives: every frame must parse as JSON.

    The two audio messages were the only ones that ever needed msgpack, and
    they are gone. A frame that only msgpack can read means someone
    reintroduced a second encoder without saying so.
    """
    payload = encode_server(s.TaskIndicator(running=True))
    assert msgspec.json.decode(payload) == {"t": "task.indicator", "running": True}


def test_malformed_json_raises_decode_error() -> None:
    with pytest.raises(msgspec.DecodeError):
        decode_client(b"{not json")


def test_a_field_of_the_wrong_type_is_rejected() -> None:
    with pytest.raises(msgspec.ValidationError):
        decode_server(b'{"t":"task.indicator","running":"yes"}')


def test_a_missing_required_field_is_rejected() -> None:
    with pytest.raises(msgspec.ValidationError):
        decode_client(b'{"t":"hello"}')


def test_defaults_are_omitted_from_the_wire() -> None:
    """omit_defaults keeps frames small; absent means default."""
    raw = msgspec.json.decode(encode_server(s.AskEnd(step_id="s")))
    assert raw == {"t": "ask.end", "stepId": "s"}


def test_an_absent_optional_field_decodes_to_its_default() -> None:
    decoded = decode_client(b'{"t":"hello","sessionId":"s"}')
    assert isinstance(decoded, c.Hello)
    assert decoded.client_type == "webapp"
    assert decoded.page_load is False
    assert decoded.user_env == {}
    # A client that knows nothing about screen classes is a client with no
    # funnel, not a malformed handshake.
    assert decoded.device is None
