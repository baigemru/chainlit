"""Server → client messages.

One ``msgspec.Struct`` per message, gathered into the ``ServerMsg`` tagged
union discriminated on ``t``. Decoding an unknown tag raises
``msgspec.ValidationError`` instead of silently producing the wrong type,
which is the whole point of replacing socket.io's untyped event names.
"""

from __future__ import annotations

from typing import Any, Literal, Union, get_args

import msgspec
from msgspec import UNSET, UnsetType

from chainlit.protocol.payloads import (
    Action,
    AskSpec,
    Element,
    Step,
    StepPatch,
    Thread,
    ToastType,
)

__all__ = [
    "SERVER_TAGS",
    "ActionAdd",
    "ActionRemove",
    "AskEnd",
    "AskEndReason",
    "AskStart",
    "ElementRemove",
    "ElementUpsert",
    "Error",
    "Heartbeat",
    "ProfileChanged",
    "Reload",
    "ServerMsg",
    "SessionHandoff",
    "SessionReady",
    "SidebarSet",
    "StepDelete",
    "StepStreamStart",
    "StepStreamToken",
    "StepUpdate",
    "StepUpsert",
    "TaskIndicator",
    "ThreadFirstInteraction",
    "ThreadOpen",
    "ThreadParent",
    "ThreadResume",
    "Toast",
]

AskEndReason = Literal["answered", "timeout", "cancelled", "superseded", "stale"]


class _Msg(msgspec.Struct, tag_field="t", rename="camel", omit_defaults=True):
    """Base of every server message. ``t`` carries the tag."""


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


class SessionReady(_Msg, tag="session.ready"):
    """The handshake is complete and the session may be used.

    New in this protocol. socket.io had no such message: the client emitted
    ``connection_successful`` and then guessed, which is why the old
    ``switch_chat_profile`` and ``ask_reply`` handlers both had to park on
    an internal ``connection_inited`` gate.
    """

    session_id: str
    thread_id: str | None = None
    chat_profile: str | None = None
    restored: bool = False
    heartbeat_interval_ms: int = 20000


class Error(_Msg, tag="error"):
    """A refusal or failure the client can act on.

    New in this protocol: the old wire signalled failures either by
    silence, by a socket.io ``ConnectionRefusedError`` string, or by an
    ``ErrorMessage`` step in the transcript.
    """

    code: str
    message: str = ""
    detail: dict[str, Any] | None = None
    fatal: bool = False


class Heartbeat(_Msg, tag="hb"):
    """Liveness probe. The client answers with ``hb.ack``. New."""

    seq: int = 0


class Reload(_Msg, tag="reload"):
    """The dev file-watcher rebuilt the app; drop the session and reload."""


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------


class StepUpsert(_Msg, tag="step.upsert"):
    """Insert the step, or merge it into the one already stored by id."""

    step: Step


class StepUpdate(_Msg, tag="step.update"):
    """Merge a partial update into an existing step.

    Kept separate from ``step.upsert`` on purpose, and the payload is a
    ``StepPatch`` rather than a ``Step`` for the same reason: the client's
    merge semantics differ. An upsert *creates* the step when the id is
    unknown and states the whole object, so its value defaults are the
    value; an update addresses a step that must already exist, its absence
    is a no-op rather than a new bubble at the bottom of the feed, and it
    must be able to say "streaming is now false" without also restating —
    or silently clearing — every field it does not mention. Only the fields
    present in the frame are written; ``wait: null`` ends wait mode, an
    absent ``wait`` leaves it alone.
    """

    step: StepPatch


class StepDelete(_Msg, tag="step.delete"):
    step_id: str


class StepStreamStart(_Msg, tag="step.stream.start"):
    step: Step


class StepStreamToken(_Msg, tag="step.stream.token"):
    id: str
    token: str
    is_sequence: bool = False
    is_input: bool = False


# --------------------------------------------------------------------------
# Elements and actions
# --------------------------------------------------------------------------


class ElementUpsert(_Msg, tag="element.upsert"):
    element: Element


class ElementRemove(_Msg, tag="element.remove"):
    id: str


class ActionAdd(_Msg, tag="action.add"):
    action: Action


class ActionRemove(_Msg, tag="action.remove"):
    id: str


# --------------------------------------------------------------------------
# Asks
# --------------------------------------------------------------------------


class AskStart(_Msg, tag="ask.start"):
    """Put the composer into ask mode and render the form."""

    spec: AskSpec
    step: Step


class AskEnd(_Msg, tag="ask.end"):
    """Leave ask mode.

    Collapses the old ``ask_timeout`` and ``clear_ask``, which differed
    only in why the ask ended — and the client treated them almost
    identically. ``step_id`` addresses the ask being ended: the old
    messages were unaddressed, which is the sole reason the server had to
    choreograph "never clear over a live successor ask". An addressed end
    lets the client drop a stale one itself.
    """

    step_id: str
    reason: AskEndReason = "cancelled"


# --------------------------------------------------------------------------
# Task indicator
# --------------------------------------------------------------------------


class TaskIndicator(_Msg, tag="task.indicator"):
    """The single loading boolean.

    Collapses ``task_start``/``task_end``: they were one level-triggered
    signal split across two event names, and every level-triggered resync
    in the old code had to pick which of the two to emit.
    """

    running: bool


# --------------------------------------------------------------------------
# Threads
# --------------------------------------------------------------------------


class ThreadResume(_Msg, tag="thread.resume"):
    """A full snapshot of the resumed thread; replaces the client's feed."""

    thread: Thread


class ThreadFirstInteraction(_Msg, tag="thread.first_interaction"):
    """The thread row now exists; adopt its id."""

    interaction: str
    thread_id: str


class ThreadParent(_Msg, tag="thread.parent"):
    """The thread the current one descends from, for the return button."""

    parent_thread_id: str


class ThreadOpen(_Msg, tag="thread.open"):
    """Navigate to an existing thread of this user."""

    thread_id: str
    keep_transcript: bool = True


# --------------------------------------------------------------------------
# Profiles and sidebar
# --------------------------------------------------------------------------


class ProfileChanged(_Msg, tag="profile.changed"):
    """The profile changed in place — same session, same thread.

    ``sync=True`` means "adopt this value" after a reconnect rather than
    "a switch happened".
    """

    chat_profile: str
    previous: str | None = None
    sync: bool = False


class SessionHandoff(_Msg, tag="session.handoff"):
    """Tear this session down and connect a new one on ``chat_profile``.

    The old name was ``set_chat_profile``, one letter away from the
    in-place ``switch_chat_profile`` while doing something entirely
    different. ``next_session_id`` is minted server-side; the browser
    adopts it verbatim.
    """

    chat_profile: str
    next_session_id: str | None = None
    keep_transcript: bool = False
    has_transit_message: bool = False


class SidebarSet(_Msg, tag="sidebar.set"):
    """Title and contents of the element sidebar in one message.

    Collapses ``set_sidebar_title`` and ``set_sidebar_elements``, which the
    client had to reconcile into a single ``sideView`` atom — each event
    reading the other's half out of the previous state.

    An *absent* field means "leave it alone"; an explicit ``null`` on
    ``title`` or ``key`` clears it. The two are different instructions and
    a ``None`` default could not express the second: ``omit_defaults``
    would drop it from the frame, so the client could never be told to
    clear a title it is already showing. ``elements`` has no null form —
    an empty list already closes the sidebar.
    """

    title: Union[str, UnsetType, None] = UNSET
    elements: Union[list[Element], UnsetType] = UNSET
    key: Union[str, UnsetType, None] = UNSET


# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------


class Toast(_Msg, tag="toast"):
    message: str
    type: ToastType = "info"


ServerMsg = Union[
    SessionReady,
    Error,
    Heartbeat,
    Reload,
    StepUpsert,
    StepUpdate,
    StepDelete,
    StepStreamStart,
    StepStreamToken,
    ElementUpsert,
    ElementRemove,
    ActionAdd,
    ActionRemove,
    AskStart,
    AskEnd,
    TaskIndicator,
    ThreadResume,
    ThreadFirstInteraction,
    ThreadParent,
    ThreadOpen,
    ProfileChanged,
    SessionHandoff,
    SidebarSet,
    Toast,
]

SERVER_TAGS: frozenset[str] = frozenset(
    str(branch.__struct_config__.tag) for branch in get_args(ServerMsg)
)
