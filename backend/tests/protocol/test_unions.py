"""The tagged unions must behave as contracts, not as suggestions."""

from __future__ import annotations

import json

import msgspec
import pytest

from chainlit.protocol import client as c, server as s
from chainlit.protocol.codec import decode_client, decode_server
from chainlit.protocol.payloads import (
    AskActionReply,
    AskActionSpec,
    AskElementSpec,
    AskFileSpec,
    AskReplyValue,
    AskTextReply,
    AskTextSpec,
    AudioElement,
    CustomElement,
    Element,
    ImageElement,
    PdfElement,
    TextElement,
    VideoElement,
)

# --------------------------------------------------------------------------
# Unknown tags
# --------------------------------------------------------------------------


def test_unknown_server_tag_is_rejected() -> None:
    """An event name the union does not know must raise, never coerce.

    The whole reason for replacing socket.io's string event names: an
    unknown one there was silently ignored by every listener.
    """
    with pytest.raises(msgspec.ValidationError):
        decode_server(b'{"t":"set_chat_profile","name":"fast"}')


def test_unknown_client_tag_is_rejected() -> None:
    with pytest.raises(msgspec.ValidationError):
        decode_client(b'{"t":"client_message","message":{"id":"x"}}')


def test_missing_tag_is_rejected() -> None:
    with pytest.raises(msgspec.ValidationError):
        decode_server(b'{"running":true}')


def test_a_server_tag_is_not_accepted_on_the_client_union() -> None:
    """The two unions share a tag field; they must not share a vocabulary."""
    with pytest.raises(msgspec.ValidationError):
        decode_client(b'{"t":"task.indicator","running":true}')


def test_a_client_tag_is_not_accepted_on_the_server_union() -> None:
    with pytest.raises(msgspec.ValidationError):
        decode_server(b'{"t":"hello","sessionId":"s"}')


# --------------------------------------------------------------------------
# Sibling branches
# --------------------------------------------------------------------------


def test_element_branch_rejects_a_sibling_branch_payload() -> None:
    payload = msgspec.json.encode(CustomElement(id="e", props={"a": 1}))
    # As the union it decodes to its own branch...
    assert isinstance(msgspec.json.decode(payload, type=Element), CustomElement)
    # ...but a sibling branch must refuse it outright.
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(payload, type=PdfElement)


def test_ask_spec_branch_rejects_a_sibling_branch_payload() -> None:
    payload = msgspec.json.encode(AskFileSpec(step_id="s", max_files=3))
    assert isinstance(
        msgspec.json.decode(payload, type=AskFileSpec | AskTextSpec), AskFileSpec
    )
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(payload, type=AskTextSpec)


def test_ask_reply_branch_rejects_a_sibling_branch_payload() -> None:
    payload = msgspec.json.encode(AskActionReply(action=_action()))
    assert isinstance(msgspec.json.decode(payload, type=AskReplyValue), AskActionReply)
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(payload, type=AskTextReply)


def test_server_branch_rejects_a_sibling_branch_payload() -> None:
    payload = msgspec.json.encode(s.TaskIndicator(running=True))
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(payload, type=s.AskEnd)


def test_client_branch_rejects_a_sibling_branch_payload() -> None:
    payload = msgspec.json.encode(c.Stop())
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(payload, type=c.SessionClear)


def _action():
    from chainlit.protocol.payloads import Action

    return Action(id="a", name="confirm")


# --------------------------------------------------------------------------
# Per-type element fields
# --------------------------------------------------------------------------

# field name -> the branches allowed to carry it
PER_TYPE_ELEMENT_FIELDS: dict[str, set[str]] = {
    "props": {"CustomElement"},
    "page": {"PdfElement"},
    "auto_play": {"AudioElement"},
    "player_config": {"VideoElement"},
    "size": {"ImageElement", "VideoElement"},
    "language": {"TextElement"},
}

ELEMENT_BRANCHES = (
    ImageElement,
    TextElement,
    PdfElement,
    AudioElement,
    VideoElement,
    CustomElement,
)


@pytest.mark.parametrize("field", sorted(PER_TYPE_ELEMENT_FIELDS))
def test_per_type_element_field_lives_only_on_its_own_branch(field: str) -> None:
    """A flat ElementDict let a pdf carry autoPlay. The union must not."""
    allowed = PER_TYPE_ELEMENT_FIELDS[field]
    for branch in ELEMENT_BRANCHES:
        has_it = field in branch.__struct_fields__
        assert has_it is (branch.__name__ in allowed), (
            f"{branch.__name__} {'carries' if has_it else 'lacks'} {field!r}; "
            f"only {sorted(allowed)} may carry it"
        )


def test_a_foreign_per_type_field_cannot_survive_a_decode() -> None:
    """Retag a custom element as a pdf: `props` must not come along."""
    raw = json.loads(msgspec.json.encode(CustomElement(id="e", props={"a": 1})))
    raw["type"] = "pdf"
    decoded = msgspec.json.decode(json.dumps(raw).encode(), type=Element)
    assert isinstance(decoded, PdfElement)
    assert not hasattr(decoded, "props")


def test_element_union_decodes_each_branch_by_its_tag() -> None:
    cases: list[tuple[str, type]] = [
        ("image", ImageElement),
        ("text", TextElement),
        ("pdf", PdfElement),
        ("audio", AudioElement),
        ("video", VideoElement),
        ("custom", CustomElement),
    ]
    for tag, expected in cases:
        payload = json.dumps({"type": tag, "id": "e"}).encode()
        assert isinstance(msgspec.json.decode(payload, type=Element), expected)


def test_ask_spec_union_decodes_each_branch_by_its_tag() -> None:
    from chainlit.protocol.payloads import AskSpec

    cases: list[tuple[str, type]] = [
        ("text", AskTextSpec),
        ("file", AskFileSpec),
        ("action", AskActionSpec),
        ("element", AskElementSpec),
    ]
    for tag, expected in cases:
        payload = json.dumps({"type": tag, "stepId": "s"}).encode()
        assert isinstance(msgspec.json.decode(payload, type=AskSpec), expected)
