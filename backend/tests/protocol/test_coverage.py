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

import re
from pathlib import Path

from chainlit.protocol.client import CLIENT_TAGS
from chainlit.protocol.server import SERVER_TAGS

CHAINLIT_ROOT = Path(__file__).resolve().parents[2] / "chainlit"

# Every module that names a socket event. socket.py and emitter.py carry
# most of them, but `reload`, `action`, `remove_action`, `remove_element`,
# `chat_settings` and the two `set_sidebar_*` live elsewhere.
SOURCE_FILES = (
    "socket.py",
    "emitter.py",
    "action.py",
    "element.py",
    "sidebar.py",
    "chat_settings.py",
    "server.py",
)

# Server -> client. `emit(...)` is the common form; `clear(...)` and
# `send_timeout(...)` are thin wrappers around it that take the event name
# as their only argument, and `emit_call(...)` is the request/response one.
_SERVER_EVENT_RE = re.compile(
    r"\b(?:emit|emit_call|clear|send_timeout)\(\s*(?:\n\s*)?[\"']([a-z_]+)[\"']"
)
# Client -> server.
_CLIENT_EVENT_RE = re.compile(r"@sio\.on\(\s*[\"']([a-z_]+)[\"']\s*\)")


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
    "resume_thread_error": "thread.resume_error",
    "first_interaction": "thread.first_interaction",
    "parent_thread": "thread.parent",
    "open_thread": "thread.open",
    "chat_profile_changed": "profile.changed",
    # Renamed as well as retagged: it tears the session down and mints a
    # successor id, which "set_chat_profile" did not say and which made it
    # one letter away from the in-place switch_chat_profile.
    "set_chat_profile": "session.handoff",
    "chat_settings": "settings.set",
    "set_commands": "commands.set",
    "set_modes": "modes.set",
    "set_favorites": "favorites.set",
    # Collapsed pair: the client reconciled both into one sideView atom,
    # each event reading the other's half out of the previous state.
    "set_sidebar_title": "sidebar.set",
    "set_sidebar_elements": "sidebar.set",
    "audio_connection": "audio.connection",
    "audio_chunk": "audio.out",
    "audio_interrupt": "audio.interrupt",
    "call_fn": "rpc.call",
    # Collapsed pair, now addressed by call_id.
    "clear_call_fn": "rpc.cancel",
    "call_fn_timeout": "rpc.cancel",
    "toast": "toast",
    "token_usage": "token.usage",
    "window_message": "window.message",
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
    "edit_message": "message.edit",
    "message_favorite": "message.favorite",
    "fetch_favorites": "favorites.fetch",
    "window_message": "window.message",
    "audio_start": "audio.start",
    "audio_chunk": "audio.in",
    "audio_end": "audio.end",
    "chat_settings_change": "settings.change",
    "chat_settings_edit": "settings.edit",
}

INTENTIONALLY_DROPPED: dict[str, str] = {
    # socket.io synthesises this one; a raw websocket signals it with the
    # close frame, which the transport already handles. Nothing in the
    # message vocabulary needs a name for it.
    "disconnect": "transport-level: the websocket close frame replaces it",
}

# Tags with no counterpart in today's protocol — additions, not renames.
NEW_SERVER_TAGS: frozenset[str] = frozenset({"session.ready", "error", "hb"})
NEW_CLIENT_TAGS: frozenset[str] = frozenset({"hb.ack", "rpc.result"})


def _parse(pattern: re.Pattern[str]) -> set[str]:
    found: set[str] = set()
    for name in SOURCE_FILES:
        path = CHAINLIT_ROOT / name
        assert path.is_file(), f"{path} moved; this parser needs updating"
        found.update(pattern.findall(path.read_text(encoding="utf-8")))
    return found


def test_parser_still_sees_every_event_the_old_code_emits() -> None:
    """Pin the parse. A silently shrinking parse would void every test below."""
    parsed = _parse(_SERVER_EVENT_RE)
    assert parsed == set(TODAYS_SERVER_EVENTS), {
        "missed_by_parser": sorted(TODAYS_SERVER_EVENTS - parsed),
        "new_in_the_old_code": sorted(parsed - TODAYS_SERVER_EVENTS),
    }


def test_parser_still_sees_every_handler_the_old_code_registers() -> None:
    parsed = _parse(_CLIENT_EVENT_RE)
    assert parsed == set(TODAYS_CLIENT_EVENTS), {
        "missed_by_parser": sorted(TODAYS_CLIENT_EVENTS - parsed),
        "new_in_the_old_code": sorted(parsed - TODAYS_CLIENT_EVENTS),
    }


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
