"""The driver for the implementation being replaced: socket.io handlers.

This is the only module in the package that imports ``chainlit``. Everything
it does -- patch ``init_ws_context``, hand the handler a mocked session and
config, read the outbound calls back off the mocks -- is socket.io-shaped
scaffolding that dies with the transport. The table above it does not.

The handlers are called directly, at the seam ``test_socket.py`` already
proves works, rather than through a real socket.io server: a scenario states
the session it starts from, and standing a server up would only add a second
way to construct one.

``hello`` is the one inbound frame that is not one legacy event. socket.io
split the handshake into ``connect`` (which builds the session) and
``connection_successful`` (which initialises it), and the split is the source
of the ordering hazards worked around all over ``socket.py``. A scenario's
``given`` *is* the built session, so ``hello`` runs the second half.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, Mapping, Optional
from unittest.mock import Mock, patch

from chainlit.session import PendingAsk, WebsocketSession
from chainlit.socket import (
    ask_reply as _ask_reply,
    clean_session as _clean_session,
    connection_successful as _connection_successful,
    stop as _stop,
)
from chainlit.types import AskSpec

from .frames import Ledger, ask_start, translate
from .spec import AskState, Given, Result, Scenario

SID = "spec-sid"

# Emitter helpers that put a frame on the wire in real code. Under a mocked
# emitter they would vanish, so each one is recorded as the frame it sends.
_HELPER_FRAMES: Dict[str, Callable[..., Any]] = {
    "send_step": lambda step: ("step.upsert", {"step": step}),
    "update_step": lambda step: ("step.update", {"patch": step}),
    "delete_step": lambda step: ("step.delete", {"step": step}),
    "send_element": lambda element: ("element.upsert", {"element": element}),
    "resume_thread": lambda thread: ("thread.resume", {"thread": thread}),
    "send_resume_thread_error": lambda error: (
        "thread.resume_error",
        {"error": error},
    ),
    "set_favorites": lambda steps: ("favorites.set", {"steps": steps}),
    "update_audio_connection": lambda state: ("audio.connection", {"state": state}),
    "task_end": lambda: ("task.indicator", {"running": False}),
    "init_thread": lambda interaction: (
        "thread.first_interaction",
        {"interaction": interaction},
    ),
}


class RecordingEmitter:
    """An emitter that writes to the ledger instead of to a socket.

    Anything not named here is still callable -- it is recorded as an effect
    rather than a frame, so a helper this package does not know about shows up
    in the record instead of passing by silence.

    ``interrupt`` is how a scenario says "this happens while the server is
    mid-restore": it fires on the first frame sent, which is the gap the real
    thing races in. A test that mutated the session before the handler ran
    would be describing a different situation entirely.
    """

    def __init__(
        self, ledger: Ledger, interrupt: Optional[Callable[[str], None]] = None
    ) -> None:
        self._ledger = ledger
        self._interrupt = interrupt

    def _sent(self, tag: str) -> None:
        if self._interrupt is not None:
            interrupt, self._interrupt = self._interrupt, None
            interrupt(tag)

    async def emit(self, event: str, payload: Any = None) -> None:
        tag, body = translate(event, payload)
        self._ledger.wire(tag, body)
        self._sent(tag)

    async def clear(self, event: str) -> None:
        self._ledger.wire(*translate(event))

    async def send_timeout(self, event: str) -> None:
        self._ledger.wire(*translate(event))

    async def process_message(self, payload: Any) -> Any:
        self._ledger.effect("process_message")
        return Mock()

    def __getattr__(self, name: str) -> Callable[..., Any]:
        builder = _HELPER_FRAMES.get(name)

        async def record(*args: Any, **kwargs: Any) -> None:
            if builder is None:
                self._ledger.effect(name)
                return
            self._ledger.wire(*builder(*args, **kwargs))

        return record


def _pending_ask(state: AskState) -> PendingAsk:
    """Build the real PendingAsk a scenario's AskState describes."""
    loop = asyncio.get_event_loop()
    future: "asyncio.Future" = loop.create_future()
    if state.answered:
        future.set_result("already answered")
    remaining = -1.0 if state.remaining is None else state.remaining

    element = None
    if state.element is not None:
        element = Mock()
        element.to_dict = Mock(return_value=dict(state.element))

    return PendingAsk(
        step_dict={"id": state.step_id, "parentId": state.parent_id},
        spec=AskSpec(timeout=state.timeout, type=state.type, step_id=state.step_id),
        future=future,
        deadline=time.monotonic() + remaining,
        restore_actions=[dict(action) for action in state.actions],
        restore_element=element,
    )


def _session(factory: Callable[..., Mock], given: Given, ledger: Ledger) -> Mock:
    pending = _pending_ask(given.pending_ask) if given.pending_ask else None
    session = factory(
        pending_ask=pending,
        has_first_interaction=given.has_first_interaction,
        parent_thread_id=given.parent_thread,
        last_resolved_ask_step_id=given.last_resolved_ask_step_id,
    )
    session.restored = given.restored
    session.chat_started = given.chat_started
    session.fresh_page_load = given.fresh_page_load
    session.thread_id_to_resume = given.resuming_thread
    session.current_task = Mock() if given.running_task else None
    if given.running_task:
        session.current_task.done = Mock(return_value=False)

    # emit_ask lives on the session, not the emitter, so without this the
    # ask would be recorded on a different object than everything around it
    # and no scenario could assert an ordering across the two.
    async def emit_ask(payload: Mapping[str, Any], ack: Any = None) -> None:
        ledger.wire(*ask_start(payload))

    session.emit_ask = Mock(side_effect=emit_ask)

    async def emit(event: str, payload: Any = None) -> None:
        ledger.wire(*translate(event, payload))

    session.emit = Mock(side_effect=emit)
    return session


def _transcript(given: Given) -> Mock:
    """A chat context holding exactly what the scenario says it holds.

    Patched unconditionally, including when the scenario says "nothing". The
    real one is a module-global whose contents survive whichever test ran
    before this one, and a spec whose world depends on test ordering is not a
    spec.
    """
    messages = []
    for step in given.transcript:
        message = Mock()
        message.id = step.get("id")
        message.to_dict = Mock(return_value=dict(step))
        message.elements = []
        message._active_wait_payload = step.get("wait")
        messages.append(message)

    context = Mock()
    context.get = Mock(return_value=messages)
    return context


def _interrupt(session: Mock, given: Given) -> Optional[Callable[[str], None]]:
    """Build the mid-restore interruption the scenario asks for, if any."""
    if given.during_restore is None:
        return None
    pending: PendingAsk = session.pending_ask

    def interrupt(_tag: str) -> None:
        if given.during_restore == "answer":
            if not pending.future.done():
                pending.future.set_result("answered mid-restore")
            return
        successor = _pending_ask(
            AskState(
                step_id="successor-step",
                remaining=None if given.during_restore == "successor_dead" else 60.0,
            )
        )
        session.pending_ask = successor

    return interrupt


def _config() -> Mock:
    config = Mock()
    config.code.on_chat_start = None
    config.code.on_chat_resume = None
    config.code.on_thread_ready = None
    config.code.on_profile_start = None
    config.code.on_stop = None
    config.features.hot_swap_chat_profile = False
    return config


async def _hello(_session: Mock, _payload: Mapping[str, Any]) -> None:
    await _connection_successful(SID)


async def _ask_reply_frame(_session: Mock, payload: Mapping[str, Any]) -> None:
    await _ask_reply(SID, dict(payload))


async def _stop_frame(_session: Mock, _payload: Mapping[str, Any]) -> None:
    await _stop(SID)


async def _clear_frame(_session: Mock, _payload: Mapping[str, Any]) -> None:
    await _clean_session(SID)


HANDLERS: Dict[str, Callable[[Mock, Mapping[str, Any]], Any]] = {
    "hello": _hello,
    "ask.reply": _ask_reply_frame,
    "stop": _stop_frame,
    "session.clear": _clear_frame,
}


def _message_factory(ledger: Ledger) -> Callable[..., Mock]:
    """Stand in for ``cl.Message`` so its step still reaches the ledger.

    ``stop`` sends a "Task manually stopped." message, which is a real
    outbound frame. Sending it for real would need a live context var rather
    than a patched ``init_ws_context``; dropping it would hide a frame the
    table has every reason to assert.
    """

    def build(**kwargs: Any) -> Mock:
        message = Mock()

        async def send() -> None:
            ledger.wire("step.upsert", {"step": {"output": kwargs.get("content")}})

        message.send = Mock(side_effect=send)
        return message

    return build


def _report(session: Mock, pending: Optional[PendingAsk]) -> Dict[str, Any]:
    """Protocol-level facts, read off wherever this implementation keeps them.

    ``pending`` is the object the scenario started with, not the session's
    current slot: ``stop`` cancels the ask *and* empties the slot, and a
    report that read the slot could not tell that apart from "there never was
    one".
    """
    state: Dict[str, Any] = {
        "ask_pending": session.pending_ask is not None,
        "ask_resolved": bool(pending and pending.future.done()),
        "ask_cancelled": bool(pending and pending.future.cancelled()),
        "last_resolved_ask_step_id": session.last_resolved_ask_step_id,
    }
    if pending is not None and pending.future.done() and not pending.future.cancelled():
        state["ask_answer"] = pending.future.result()
    return state


async def run(scenario: Scenario, session_factory: Callable[..., Mock]) -> Result:
    """Drive one scenario against the socket.io handlers."""
    ledger = Ledger()
    session = _session(session_factory, scenario.given, ledger)
    pending: Optional[PendingAsk] = session.pending_ask

    context = Mock()
    context.session = session
    context.emitter = RecordingEmitter(ledger, _interrupt(session, scenario.given))

    with (
        patch("chainlit.socket.init_ws_context", return_value=context),
        patch("chainlit.socket.config", _config()),
        patch.object(WebsocketSession, "get", return_value=session),
        patch("chainlit.socket.Message", side_effect=_message_factory(ledger)),
        patch("chainlit.socket.chat_context", _transcript(scenario.given)),
        # Same reason as the transcript: the data layer is a module-global
        # that another test may have left behind, and a scenario that reaches
        # it by accident is asserting against someone else's fixture.
        patch("chainlit.socket.get_data_layer", return_value=None),
    ):
        for frame in scenario.when:
            handler = HANDLERS.get(frame.tag)
            if handler is None:
                raise KeyError(
                    f"The legacy driver has no handler for inbound {frame.tag!r}."
                )
            await handler(session, frame.payload)

    return Result(ledger=ledger, state=_report(session, pending))


__all__ = ["HANDLERS", "RecordingEmitter", "run"]
