"""Shared wire payloads for the Chainlit websocket protocol.

Pure data: this module imports nothing from the rest of ``chainlit``, so
the protocol can be reviewed, versioned and tested on its own.

Conventions
-----------
* Every struct is ``rename="camel"`` — the wire is camelCase, the Python
  side stays snake_case.
* Every struct is ``omit_defaults=True`` — absent means "default", which
  keeps frames small and makes adding optional fields backward compatible.
* Unions are *tagged*. The tag field is spelled out explicitly on every
  branch so a decoder never has to guess, and so a payload belonging to a
  sibling branch is rejected instead of silently coerced.
"""

from __future__ import annotations

from typing import Any, Literal, Union

import msgspec

__all__ = [
    "Action",
    "AskActionReply",
    "AskActionSpec",
    "AskElementReply",
    "AskElementSpec",
    "AskFileReply",
    "AskFileSpec",
    "AskReplyValue",
    "AskSpec",
    "AskTextReply",
    "AskTextSpec",
    "AudioElement",
    "Command",
    "CustomElement",
    "DataframeElement",
    "Element",
    "ElementDisplay",
    "ElementSize",
    "Feedback",
    "FileElement",
    "FileRef",
    "ImageElement",
    "InputWidgetSpec",
    "InputWidgetType",
    "Mode",
    "ModeOption",
    "PdfElement",
    "PlotlyElement",
    "Step",
    "StepType",
    "TasklistElement",
    "TextElement",
    "Thread",
    "VideoElement",
    "Wait",
]

# --------------------------------------------------------------------------
# Scalars
# --------------------------------------------------------------------------

StepType = Literal[
    "assistant_message",
    "user_message",
    "system_message",
    "run",
    "tool",
    "llm",
    "embedding",
    "retrieval",
    "rerank",
    "undefined",
]

ElementDisplay = Literal["inline", "side", "page"]
ElementSize = Literal["small", "medium", "large"]

InputWidgetType = Literal[
    "switch",
    "slider",
    "select",
    "textinput",
    "tags",
    "numberinput",
    "multiselect",
    "checkbox",
    "radio",
    "datepicker",
]

ToastType = Literal["info", "success", "warning", "error"]


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------


class Wait(msgspec.Struct, rename="camel", omit_defaults=True):
    """Transient "waiting" presentation for a step.

    Never persisted: it rides the wire on ``step.upsert`` / ``step.update``
    only, and an update without it ends wait mode on the client.
    """

    texts: list[str] = []
    interval_ms: int = 5000
    loop: bool = False


class Feedback(msgspec.Struct, rename="camel", omit_defaults=True):
    value: Literal[0, 1] = 0
    id: str | None = None
    for_id: str | None = None
    thread_id: str | None = None
    comment: str | None = None


class Step(msgspec.Struct, rename="camel", omit_defaults=True):
    """One node of the conversation tree.

    The wire shape of a step, not its persistence shape — ``wait`` is
    transient and ``steps`` only appears in a thread snapshot.
    """

    id: str
    output: str = ""
    name: str = ""
    type: StepType = "undefined"
    thread_id: str | None = None
    parent_id: str | None = None
    input: str = ""
    created_at: str | None = None
    start: str | None = None
    end: str | None = None
    is_error: bool = False
    streaming: bool = False
    wait_for_answer: bool = False
    show_input: Union[bool, str] = False
    default_open: bool = False
    auto_collapse: bool = False
    language: str | None = None
    icon: str | None = None
    command: str | None = None
    modes: dict[str, str] | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    generation: dict[str, Any] | None = None
    feedback: Feedback | None = None
    wait: Wait | None = None
    steps: list["Step"] | None = None


# --------------------------------------------------------------------------
# Elements
# --------------------------------------------------------------------------


class _ElementBase(msgspec.Struct, rename="camel", omit_defaults=True):
    """Fields every element branch genuinely carries.

    Per-type fields (``props``, ``page``, ``autoPlay``, ``playerConfig``,
    ``size``, ``language``) deliberately live on their own branch: a flat
    base would let ``pdf`` carry ``autoPlay`` and make the union useless as
    a contract.
    """

    id: str
    name: str = ""
    display: ElementDisplay = "inline"
    thread_id: str | None = None
    for_id: str | None = None
    url: str | None = None
    chainlit_key: str | None = None
    object_key: str | None = None
    path: str | None = None
    mime: str | None = None


class ImageElement(_ElementBase, tag_field="type", tag="image"):
    size: ElementSize | None = None


class TextElement(_ElementBase, tag_field="type", tag="text"):
    language: str | None = None


class PdfElement(_ElementBase, tag_field="type", tag="pdf"):
    page: int | None = None


class AudioElement(_ElementBase, tag_field="type", tag="audio"):
    auto_play: bool = False


class VideoElement(_ElementBase, tag_field="type", tag="video"):
    size: ElementSize | None = None
    player_config: dict[str, Any] | None = None


class FileElement(_ElementBase, tag_field="type", tag="file"):
    pass


class PlotlyElement(_ElementBase, tag_field="type", tag="plotly"):
    pass


class DataframeElement(_ElementBase, tag_field="type", tag="dataframe"):
    pass


class CustomElement(_ElementBase, tag_field="type", tag="custom"):
    props: dict[str, Any] = {}


class TasklistElement(_ElementBase, tag_field="type", tag="tasklist"):
    pass


Element = Union[
    ImageElement,
    TextElement,
    PdfElement,
    AudioElement,
    VideoElement,
    FileElement,
    PlotlyElement,
    DataframeElement,
    CustomElement,
    TasklistElement,
]


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------


class Action(msgspec.Struct, rename="camel", omit_defaults=True):
    id: str
    name: str
    payload: dict[str, Any] = {}
    label: str = ""
    tooltip: str = ""
    icon: str | None = None
    for_id: str | None = None


# --------------------------------------------------------------------------
# Asks
# --------------------------------------------------------------------------


class _AskSpecBase(msgspec.Struct, rename="camel", omit_defaults=True):
    step_id: str
    timeout: int = 90


class AskTextSpec(_AskSpecBase, tag_field="type", tag="text"):
    pass


class AskFileSpec(_AskSpecBase, tag_field="type", tag="file"):
    accept: Union[list[str], dict[str, list[str]]] = []
    max_files: int = 1
    max_size_mb: int = 2


class AskActionSpec(_AskSpecBase, tag_field="type", tag="action"):
    keys: list[str] = []


class AskElementSpec(_AskSpecBase, tag_field="type", tag="element"):
    element_id: str = ""


AskSpec = Union[AskTextSpec, AskFileSpec, AskActionSpec, AskElementSpec]


class FileRef(msgspec.Struct, rename="camel", omit_defaults=True):
    id: str


class AskTextReply(
    msgspec.Struct, tag_field="kind", tag="text", rename="camel", omit_defaults=True
):
    step: Step


class AskFileReply(
    msgspec.Struct, tag_field="kind", tag="file", rename="camel", omit_defaults=True
):
    files: list[FileRef] = []


class AskActionReply(
    msgspec.Struct, tag_field="kind", tag="action", rename="camel", omit_defaults=True
):
    action: Action


class AskElementReply(
    msgspec.Struct, tag_field="kind", tag="element", rename="camel", omit_defaults=True
):
    """Reply of a custom-element ask.

    The old wire spread the element's own props over the top level
    (``{**props, "submitted": True}``), which no closed struct can hold and
    which let an app prop shadow a protocol field. They are nested now.
    """

    submitted: bool = False
    props: dict[str, Any] = {}


AskReplyValue = Union[AskTextReply, AskFileReply, AskActionReply, AskElementReply]


# --------------------------------------------------------------------------
# Threads
# --------------------------------------------------------------------------


class Thread(msgspec.Struct, rename="camel", omit_defaults=True):
    id: str
    created_at: str = ""
    name: str | None = None
    user_id: str | None = None
    user_identifier: str | None = None
    parent_thread_id: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    steps: list[Step] = []
    elements: list[Element] = []


# --------------------------------------------------------------------------
# Composer vocabulary
# --------------------------------------------------------------------------


class Command(msgspec.Struct, rename="camel", omit_defaults=True):
    id: str
    icon: str = ""
    description: str = ""
    button: bool = False
    persistent: bool = False
    selected: bool = False


class ModeOption(msgspec.Struct, rename="camel", omit_defaults=True):
    id: str
    name: str
    description: str | None = None
    icon: str | None = None
    default: bool = False


class Mode(msgspec.Struct, rename="camel", omit_defaults=True):
    id: str
    name: str
    options: list[ModeOption] = []


class InputWidgetSpec(msgspec.Struct, rename="camel", omit_defaults=True):
    """One field of the chat-settings form.

    Deliberately one struct rather than a tagged union: the widget kinds
    share a single renderer on the client and differ only by which optional
    knobs they set, so a union would buy nothing and break every app that
    adds a widget kind.
    """

    id: str
    label: str
    type: InputWidgetType = "textinput"
    initial: Any = None
    tooltip: str | None = None
    description: str | None = None
    disabled: bool = False
    # slider / numberinput
    min: float | None = None
    max: float | None = None
    step: float | None = None
    # select / multiselect / radio
    items: Any = None
    # textinput
    placeholder: str | None = None
    multiline: bool = False
    # datepicker
    mode: str | None = None
    format: str | None = None
    min_date: str | None = None
    max_date: str | None = None
    # tags
    inputs: Any = None
