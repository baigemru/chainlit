"""Every message in both unions survives a full encode/decode cycle."""

from __future__ import annotations

import msgspec
import pytest

from chainlit.protocol.client import CLIENT_TAGS
from chainlit.protocol.codec import (
    decode_client,
    decode_server,
    encode_client,
    encode_server,
)
from chainlit.protocol.server import SERVER_TAGS
from tests.protocol.samples import CLIENT_SAMPLES, SERVER_SAMPLES


def test_every_server_message_has_a_sample() -> None:
    """Exhaustiveness gate: a new tag with no sample fails here, not silently."""
    assert set(SERVER_SAMPLES) == set(SERVER_TAGS)


def test_every_client_message_has_a_sample() -> None:
    assert set(CLIENT_SAMPLES) == set(CLIENT_TAGS)


@pytest.mark.parametrize("tag", sorted(SERVER_SAMPLES))
def test_server_message_roundtrips(tag: str) -> None:
    original = SERVER_SAMPLES[tag]
    # Compare decoded objects, never encoded bytes: omit_defaults makes byte
    # equality depend on which fields happen to hold their default.
    assert decode_server(encode_server(original)) == original


@pytest.mark.parametrize("tag", sorted(CLIENT_SAMPLES))
def test_client_message_roundtrips(tag: str) -> None:
    original = CLIENT_SAMPLES[tag]
    assert decode_client(encode_client(original)) == original


@pytest.mark.parametrize("tag", sorted(SERVER_SAMPLES))
def test_server_message_carries_its_tag(tag: str) -> None:
    assert msgspec.json.decode(encode_server(SERVER_SAMPLES[tag]))["t"] == tag


@pytest.mark.parametrize("tag", sorted(CLIENT_SAMPLES))
def test_client_message_carries_its_tag(tag: str) -> None:
    assert msgspec.json.decode(encode_client(CLIENT_SAMPLES[tag]))["t"] == tag


@pytest.mark.parametrize("tag", sorted(SERVER_SAMPLES))
def test_no_server_message_carries_raw_bytes(tag: str) -> None:
    """The JSON encoder would base64 them; nothing on this wire should have any."""
    encode_server(SERVER_SAMPLES[tag])  # would raise if a field were bytes-typed
    assert not _has_bytes(SERVER_SAMPLES[tag])


@pytest.mark.parametrize("tag", sorted(CLIENT_SAMPLES))
def test_no_client_message_carries_raw_bytes(tag: str) -> None:
    assert not _has_bytes(CLIENT_SAMPLES[tag])


def _has_bytes(msg: msgspec.Struct) -> bool:
    return any(
        isinstance(getattr(msg, field.name, None), (bytes, bytearray))
        for field in msgspec.structs.fields(msg)
    )
