"""Every message in both unions survives a full encode/decode cycle."""

from __future__ import annotations

import msgspec
import pytest

from chainlit.protocol.client import CLIENT_TAGS
from chainlit.protocol.codec import (
    FrameKind,
    client_frame_kind,
    decode_client,
    decode_server,
    encode_client,
    encode_server,
    server_frame_kind,
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
    kind = server_frame_kind(original)
    # Compare decoded objects, never encoded bytes: omit_defaults makes byte
    # equality depend on which fields happen to hold their default.
    assert decode_server(encode_server(original), kind) == original


@pytest.mark.parametrize("tag", sorted(CLIENT_SAMPLES))
def test_client_message_roundtrips(tag: str) -> None:
    original = CLIENT_SAMPLES[tag]
    kind = client_frame_kind(original)
    assert decode_client(encode_client(original), kind) == original


@pytest.mark.parametrize("tag", sorted(SERVER_SAMPLES))
def test_server_message_carries_its_tag(tag: str) -> None:
    original = SERVER_SAMPLES[tag]
    if server_frame_kind(original) is FrameKind.BINARY:
        raw = msgspec.msgpack.decode(encode_server(original))
    else:
        raw = msgspec.json.decode(encode_server(original))
    assert raw["t"] == tag


@pytest.mark.parametrize("tag", sorted(CLIENT_SAMPLES))
def test_client_message_carries_its_tag(tag: str) -> None:
    original = CLIENT_SAMPLES[tag]
    if client_frame_kind(original) is FrameKind.BINARY:
        raw = msgspec.msgpack.decode(encode_client(original))
    else:
        raw = msgspec.json.decode(encode_client(original))
    assert raw["t"] == tag


def test_only_the_audio_messages_are_binary() -> None:
    binary_server = {
        tag
        for tag, msg in SERVER_SAMPLES.items()
        if server_frame_kind(msg) is FrameKind.BINARY
    }
    binary_client = {
        tag
        for tag, msg in CLIENT_SAMPLES.items()
        if client_frame_kind(msg) is FrameKind.BINARY
    }
    assert binary_server == {"audio.out"}
    assert binary_client == {"audio.in"}
