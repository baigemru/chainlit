"""Partial updates must be able to say "false" and "empty".

``step.update`` and ``sidebar.set`` are merge messages: the receiver writes
the fields the frame carries and leaves the rest of the object alone. Under
``omit_defaults`` that only works if "not mentioned" and "mentioned as
``False`` / ``""`` / ``null``" produce different bytes — which a value
default (``streaming: bool = False``) cannot do, because it is dropped from
the frame exactly like an unmentioned field. Hence ``UNSET``.
"""

from __future__ import annotations

import json
from typing import Any

import msgspec
import pytest
from msgspec import UNSET

from chainlit.protocol import server as s
from chainlit.protocol.codec import decode_server, encode_server
from chainlit.protocol.payloads import Feedback, Step, StepPatch, Wait

STEP_ID = "4d8f0e5e-0b3a-4a1e-8a1f-1f0f3a2b9c11"


def _step_of(msg: s.ServerMsg) -> dict[str, Any]:
    return json.loads(encode_server(msg))["step"]


# --------------------------------------------------------------------------
# The defect itself: an explicit False is a different frame from silence
# --------------------------------------------------------------------------


def test_turning_streaming_off_is_not_the_same_frame_as_saying_nothing() -> None:
    """The regression this type exists to prevent.

    With ``streaming: bool = False`` on the payload both of these encoded
    to ``{"t":"step.update","step":{"id":...}}`` — byte-identical — so a
    client could never be told that a step had stopped streaming.
    """
    stop_streaming = encode_server(
        s.StepUpdate(step=StepPatch(id=STEP_ID, streaming=False))
    )
    no_opinion = encode_server(s.StepUpdate(step=StepPatch(id=STEP_ID)))

    assert stop_streaming != no_opinion
    assert _step_of(s.StepUpdate(step=StepPatch(id=STEP_ID, streaming=False))) == {
        "id": STEP_ID,
        "streaming": False,
    }
    assert _step_of(s.StepUpdate(step=StepPatch(id=STEP_ID))) == {"id": STEP_ID}


@pytest.mark.parametrize(
    ("field", "explicit"),
    [
        ("streaming", False),
        ("wait_for_answer", False),
        ("is_error", False),
        ("default_open", False),
        ("auto_collapse", False),
        ("show_input", False),
        ("output", ""),
        ("input", ""),
        ("name", ""),
    ],
)
def test_a_falsy_value_survives_the_round_trip_as_a_stated_value(
    field: str, explicit: Any
) -> None:
    """An explicit falsy value is transmitted; an unmentioned field is not."""
    stated_field: dict[str, Any] = {field: explicit}
    stated = StepPatch(id=STEP_ID, **stated_field)
    silent = StepPatch(id=STEP_ID)

    encoded_name = StepPatch.__struct_encode_fields__[
        StepPatch.__struct_fields__.index(field)
    ]
    assert _step_of(s.StepUpdate(step=stated))[encoded_name] == explicit
    assert encoded_name not in _step_of(s.StepUpdate(step=silent))

    decoded_stated = decode_server(encode_server(s.StepUpdate(step=stated)))
    decoded_silent = decode_server(encode_server(s.StepUpdate(step=silent)))
    assert isinstance(decoded_stated, s.StepUpdate)
    assert isinstance(decoded_silent, s.StepUpdate)
    assert getattr(decoded_stated.step, field) == explicit
    assert getattr(decoded_silent.step, field) is UNSET


def test_an_unmentioned_field_decodes_to_unset_not_to_a_value() -> None:
    """UNSET is what lets the receiver skip the field instead of writing it."""
    decoded = decode_server(b'{"t":"step.update","step":{"id":"x"}}')
    assert isinstance(decoded, s.StepUpdate)
    for field in StepPatch.__struct_fields__:
        if field == "id":
            continue
        assert getattr(decoded.step, field) is UNSET, field


def test_a_nullable_field_can_be_cleared_explicitly() -> None:
    """``null`` clears; absence leaves alone. Both must reach the wire."""
    cleared = _step_of(s.StepUpdate(step=StepPatch(id=STEP_ID, command=None)))
    assert cleared == {"id": STEP_ID, "command": None}

    decoded = decode_server(encode_server(s.StepUpdate(step=StepPatch(id=STEP_ID))))
    assert isinstance(decoded, s.StepUpdate)
    assert decoded.step.command is UNSET


def test_wait_mode_can_be_ended_and_left_alone_by_different_frames() -> None:
    ending = _step_of(s.StepUpdate(step=StepPatch(id=STEP_ID, wait=None)))
    leaving = _step_of(s.StepUpdate(step=StepPatch(id=STEP_ID)))
    starting = _step_of(
        s.StepUpdate(step=StepPatch(id=STEP_ID, wait=Wait(texts=["thinking"])))
    )

    assert ending == {"id": STEP_ID, "wait": None}
    assert leaving == {"id": STEP_ID}
    assert starting["wait"] == {"texts": ["thinking"]}


# --------------------------------------------------------------------------
# The patch is a patch, and the upsert is not
# --------------------------------------------------------------------------


def test_the_patch_mirrors_the_step_field_for_field_except_the_child_list() -> None:
    """A field added to ``Step`` and forgotten here would be unpatchable."""
    assert set(StepPatch.__struct_fields__) == set(Step.__struct_fields__) - {"steps"}
    assert StepPatch.__struct_encode_fields__ != ()
    # The camelCase names must match too: the client sees one vocabulary.
    patch_names = dict(
        zip(StepPatch.__struct_fields__, StepPatch.__struct_encode_fields__)
    )
    step_names = dict(zip(Step.__struct_fields__, Step.__struct_encode_fields__))
    for field, encoded in patch_names.items():
        assert step_names[field] == encoded


def test_every_patch_field_but_the_id_defaults_to_unset() -> None:
    patch = StepPatch(id=STEP_ID)
    assert patch.id == STEP_ID
    assert all(
        getattr(patch, field) is UNSET
        for field in StepPatch.__struct_fields__
        if field != "id"
    )


def test_the_upsert_still_states_the_whole_step() -> None:
    """``step.upsert`` keeps value defaults on purpose — it is not a patch."""
    upsert = s.StepUpsert(step=Step(id=STEP_ID, type="assistant_message"))
    assert isinstance(upsert.step, Step)
    assert upsert.step.streaming is False
    assert _step_of(upsert) == {"id": STEP_ID, "type": "assistant_message"}


def test_a_child_list_does_not_ride_along_on_a_patch() -> None:
    """A patch has no ``steps``; a frame carrying one must not grow it."""
    frame = json.dumps({"t": "step.update", "step": {"id": STEP_ID, "steps": []}})
    decoded = decode_server(frame.encode())
    assert isinstance(decoded, s.StepUpdate)
    # Unknown fields are ignored, not adopted: no child list rides along.
    assert not hasattr(decoded.step, "steps")


# --------------------------------------------------------------------------
# sidebar.set
# --------------------------------------------------------------------------


def test_the_sidebar_title_can_be_cleared_and_left_alone() -> None:
    """``None`` used to be the default, so "clear the title" was unsendable."""
    assert (
        encode_server(s.SidebarSet(title=None)) == b'{"t":"sidebar.set","title":null}'
    )
    assert encode_server(s.SidebarSet()) == b'{"t":"sidebar.set"}'

    cleared = decode_server(b'{"t":"sidebar.set","title":null}')
    untouched = decode_server(b'{"t":"sidebar.set"}')
    assert isinstance(cleared, s.SidebarSet)
    assert isinstance(untouched, s.SidebarSet)
    assert cleared.title is None
    assert untouched.title is UNSET


def test_the_sidebar_key_can_be_cleared_and_left_alone() -> None:
    assert encode_server(s.SidebarSet(key=None)) == b'{"t":"sidebar.set","key":null}'
    decoded = decode_server(b'{"t":"sidebar.set"}')
    assert isinstance(decoded, s.SidebarSet)
    assert decoded.key is UNSET


def test_an_empty_element_list_closes_the_sidebar_and_absence_does_not() -> None:
    closing = encode_server(s.SidebarSet(elements=[]))
    silent = encode_server(s.SidebarSet(title="Sources"))
    assert closing == b'{"t":"sidebar.set","elements":[]}'
    assert b"elements" not in silent

    decoded = decode_server(closing)
    assert isinstance(decoded, s.SidebarSet)
    assert decoded.elements == []


# --------------------------------------------------------------------------
# Feedback
# --------------------------------------------------------------------------


def test_a_feedback_cannot_be_constructed_without_a_value_and_a_target() -> None:
    """``Feedback()`` used to be a thumbs-down rating of nothing at all."""
    with pytest.raises(TypeError):
        Feedback()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Feedback(value=0)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Feedback(for_id="step-1")  # type: ignore[call-arg]


def test_a_feedback_missing_a_field_is_rejected_by_the_decoder() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"value":1}', type=Feedback)
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"forId":"step-1"}', type=Feedback)


def test_an_explicit_thumbs_down_still_encodes_its_zero() -> None:
    """0 is a rating, not a default: it must survive the round trip."""
    encoded = msgspec.json.encode(Feedback(value=0, for_id="step-1"))
    assert encoded == b'{"value":0,"forId":"step-1"}'
    assert msgspec.json.decode(encoded, type=Feedback) == Feedback(
        value=0, for_id="step-1"
    )
