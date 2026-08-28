"""The rebuild's safety net: no message may be lost silently.

Every event name the socket.io implementation uses today is parsed out of
the source (never imported — the protocol package must stay independent of
the rest of ``chainlit``) and must be either mapped to a tag in the new
unions, or listed in ``INTENTIONALLY_DROPPED`` with a reason.

The parsed set is also pinned against ``TODAYS_EVENTS``. A parser that
rots — the old code emits four of these through ``clear()`` and
``send_timeout()`` rather than ``emit()``, and a naive regex silently finds
48 of 52 — would otherwise make this whole file pass vacuously.
"""

from __future__ import annotations

from pathlib import Path

from chainlit.protocol.client import CLIENT_TAGS
from chainlit.protocol.server import SERVER_TAGS

CHAINLIT_ROOT = Path(__file__).resolve().parents[2] / "chainlit"


# --------------------------------------------------------------------------
# The old vocabulary, pinned.
# --------------------------------------------------------------------------

TODAYS_SERVER_EVENTS: frozenset[str] = frozenset(
    {
        "action",
        "ask",
        "ask_timeout",
        "audio_chunk",
        "audio_connection",
        "audio_interrupt",
        "call_fn",
        "call_fn_timeout",
        "chat_profile_changed",
        "chat_settings",
        "clear_ask",
        "clear_call_fn",
        "delete_message",
        "element",
        "first_interaction",
        "new_message",
        "open_thread",
        "parent_thread",
        "reload",
        "remove_action",
        "remove_element",
        "resume_thread",
        "resume_thread_error",
        "set_chat_profile",
        "set_commands",
        "set_favorites",
        "set_modes",
        "set_sidebar_elements",
        "set_sidebar_title",
        "stream_start",
        "stream_token",
        "task_end",
        "task_start",
        "toast",
        "token_usage",
        "update_message",
        "window_message",
    }
)

TODAYS_CLIENT_EVENTS: frozenset[str] = frozenset(
    {
        "ask_reply",
        "audio_chunk",
        "audio_end",
        "audio_start",
        "chat_settings_change",
        "chat_settings_edit",
        "clear_session",
        "client_message",
        "connect",
        "connection_successful",
        "disconnect",
        "edit_message",
        "fetch_favorites",
        "message_favorite",
        "stop",
        "switch_chat_profile",
        "window_message",
    }
)


# --------------------------------------------------------------------------
# old name -> new tag
# --------------------------------------------------------------------------

SERVER_MAPPING: dict[str, str] = {
    "action": "action.add",
    "remove_action": "action.remove",
    "element": "element.upsert",
    "remove_element": "element.remove",
    "new_message": "step.upsert",
    # Deliberately NOT merged into step.upsert: the client's merge
    # semantics differ (upsert creates on an unknown id, update does not).
    "update_message": "step.update",
    "delete_message": "step.delete",
    "stream_start": "step.stream.start",
    "stream_token": "step.stream.token",
    "ask": "ask.start",
    # Collapsed pair: both meant "leave ask mode", differing only in why.
    "ask_timeout": "ask.end",
    "clear_ask": "ask.end",
    # Collapsed pair: one level-triggered boolean split over two names.
    "task_start": "task.indicator",
    "task_end": "task.indicator",
    "resume_thread": "thread.resume",
    "first_interaction": "thread.first_interaction",
    "parent_thread": "thread.parent",
    "open_thread": "thread.open",
    "chat_profile_changed": "profile.changed",
    # Renamed as well as retagged: it tears the session down and mints a
    # successor id, which "set_chat_profile" did not say and which made it
    # one letter away from the in-place switch_chat_profile.
    "set_chat_profile": "session.handoff",
    # Collapsed pair: the client reconciled both into one sideView atom,
    # each event reading the other's half out of the previous state.
    "set_sidebar_title": "sidebar.set",
    "set_sidebar_elements": "sidebar.set",
    "toast": "toast",
    "reload": "reload",
}

CLIENT_MAPPING: dict[str, str] = {
    # 2 -> 1: the auth dict of socket.io's connect and the separate
    # connection_successful event become one first frame.
    "connect": "hello",
    "connection_successful": "hello",
    "clear_session": "session.clear",
    "switch_chat_profile": "profile.switch",
    "stop": "stop",
    "ask_reply": "ask.reply",
    "client_message": "message.send",
}

# Dropped with the feature behind them. Each name here was a tag in the
# first draft of this protocol, and each was cut once the consumer audit
# showed the feature is off or unreachable. The reason is the evidence:
# these are deletions, not oversights, and a future reader who wants one
# back is being told what has to become true first.
INTENTIONALLY_DROPPED: dict[str, str] = {
    # socket.io synthesises this one; a raw websocket signals it with the
    # close frame, which the transport already handles. Nothing in the
    # message vocabulary needs a name for it.
    "disconnect": "transport-level: the websocket close frame replaces it",
    # Audio: `[features.audio] enabled = false`, no on_audio_* handler and
    # no cl.Audio anywhere in the consumer. Dropping the four names is what
    # takes the last bytes off this wire and makes the codec JSON-only.
    "audio_connection": "audio is disabled; no on_audio_* handler exists",
    "audio_chunk": "audio is disabled; the wire carries no binary frames",
    "audio_interrupt": "audio is disabled",
    "audio_start": "audio is disabled",
    "audio_end": "audio is disabled",
    # Chat settings: no @on_settings_update, no cl.ChatSettings.
    "chat_settings": "chat settings are unused; no cl.ChatSettings instance",
    "chat_settings_change": "chat settings are unused",
    "chat_settings_edit": "chat settings are unused",
    # Commands and modes: no cl.Command; the one cl.Mode call site is
    # commented out in the consumer.
    "set_commands": "commands are unused; no cl.Command instance",
    "set_modes": "modes are unused; the only call site is commented out",
    # Favorites: `favorites = false`. The abstract get_favorite_steps goes
    # with them, or the feature is deleted and its tax is not.
    "set_favorites": "`favorites = false`; the feature is off",
    "message_favorite": "`favorites = false`",
    "fetch_favorites": "`favorites = false`",
    # Message editing: `edit_message = false`.
    "edit_message": "`edit_message = false`; the feature is off",
    # RPC into the host page: a copilot/embedding facility. The consumer
    # imports no copilot and declares no CopilotFunction.
    "call_fn": "no copilot embedding; nothing calls into the host page",
    "clear_call_fn": "no copilot embedding",
    "call_fn_timeout": "no copilot embedding",
    "window_message": "no host-page integration; postMessage is unused",
    # Token usage: the client atom was written and never read -- no
    # component, no test, no export consumer.
    "token_usage": "the client atom it fed is written and never read",
    # Resume errors are errors. A second name for one failure meant the
    # client kept a whole atom to distinguish it from `error`.
    "resume_thread_error": "folded into `error` with a code",
}

# Tags with no counterpart in today's protocol — additions, not renames.
NEW_SERVER_TAGS: frozenset[str] = frozenset({"session.ready", "error", "hb"})
NEW_CLIENT_TAGS: frozenset[str] = frozenset({"hb.ack"})


def test_every_old_server_event_is_mapped_or_dropped() -> None:
    for event in sorted(TODAYS_SERVER_EVENTS):
        assert event in SERVER_MAPPING or event in INTENTIONALLY_DROPPED, (
            f"server event {event!r} has no counterpart in ServerMsg and is "
            f"not listed in INTENTIONALLY_DROPPED"
        )


def test_every_old_client_event_is_mapped_or_dropped() -> None:
    for event in sorted(TODAYS_CLIENT_EVENTS):
        assert event in CLIENT_MAPPING or event in INTENTIONALLY_DROPPED, (
            f"client event {event!r} has no counterpart in ClientMsg and is "
            f"not listed in INTENTIONALLY_DROPPED"
        )


def test_mapped_targets_exist_in_the_unions() -> None:
    for event, tag in SERVER_MAPPING.items():
        assert tag in SERVER_TAGS, f"{event!r} maps to unknown server tag {tag!r}"
    for event, tag in CLIENT_MAPPING.items():
        assert tag in CLIENT_TAGS, f"{event!r} maps to unknown client tag {tag!r}"


def test_no_tag_is_unaccounted_for() -> None:
    """Every new tag is either a rename of an old event or a declared addition."""
    assert SERVER_TAGS == set(SERVER_MAPPING.values()) | NEW_SERVER_TAGS
    assert CLIENT_TAGS == set(CLIENT_MAPPING.values()) | NEW_CLIENT_TAGS


def test_dropped_events_carry_a_reason() -> None:
    for event, reason in INTENTIONALLY_DROPPED.items():
        assert reason.strip(), f"{event!r} is dropped without a reason"
        assert event not in SERVER_MAPPING
        assert event not in CLIENT_MAPPING


def test_readme_documents_every_old_event() -> None:
    """The client rewrite reads the README as its checklist; keep it honest."""
    readme = (CHAINLIT_ROOT / "protocol" / "README.md").read_text(encoding="utf-8")
    for event in sorted(TODAYS_SERVER_EVENTS | TODAYS_CLIENT_EVENTS):
        assert f"`{event}`" in readme, f"{event!r} is missing from protocol/README.md"
