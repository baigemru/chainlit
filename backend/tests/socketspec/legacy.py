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
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Tuple
from unittest.mock import AsyncMock, Mock, patch

import chainlit.transit as transit
from chainlit.session import PendingAsk
from chainlit.socket import (
    ask_reply as _ask_reply,
    clean_session as _clean_session,
    connect as _connect,
    connection_successful as _connection_successful,
    stop as _stop,
)
from chainlit.types import AskSpec

from .frames import Ledger
from .spec import AskState, Driver, Given, Result, Scenario

SID = "spec-sid"

# --------------------------------------------------------------------------
# socket.io event -> protocol tag
#
# This table is the transport, spelled out. It lives here rather than beside
# the Frame type because an event name is as much of a dependency on
# socket.io as an import of it would be -- and the boundary test, which looks
# at imports, would never have caught it there.
# --------------------------------------------------------------------------

# Straight renames: the payload travels as-is under the new tag.
_RENAMES: Dict[str, str] = {
    "resume_thread": "thread.resume",
    "resume_thread_error": "thread.resume_error",
    "first_interaction": "thread.first_interaction",
    "parent_thread": "thread.parent",
    "open_thread": "thread.open",
    "chat_profile_changed": "profile.changed",
    "set_chat_profile": "session.handoff",
    "audio_interrupt": "audio.interrupt",
    "toast": "toast",
    "reload": "reload",
    "window_message": "window.message",
    "call_fn": "rpc.call",
}

# The payload moves under a named key -- the old event shipped a bare value.
_WRAPPED: Dict[str, Tuple[str, str]] = {
    "new_message": ("step.upsert", "step"),
    "update_message": ("step.update", "patch"),
    "delete_message": ("step.delete", "step"),
    "stream_start": ("step.stream.start", "step"),
    "stream_token": ("step.stream.token", "token"),
    "element": ("element.upsert", "element"),
    "remove_element": ("element.remove", "element"),
    "action": ("action.add", "action"),
    "remove_action": ("action.remove", "action"),
    "ask": ("ask.start", "ask"),
    "chat_settings": ("settings.set", "inputs"),
    "set_commands": ("commands.set", "commands"),
    "set_modes": ("modes.set", "modes"),
    "set_favorites": ("favorites.set", "steps"),
    "token_usage": ("token.usage", "count"),
    "audio_connection": ("audio.connection", "state"),
}

# Collapsed pairs. The reason is only knowable for the timeout half: the
# legacy `clear_ask` / `clear_call_fn` carry no reason at all, so the table
# must not pin one on them. Phase 5 tightens this, it cannot be tightened here
# without inventing information the current wire does not carry.
_COLLAPSED: Dict[str, Tuple[str, Dict[str, Any]]] = {
    "ask_timeout": ("ask.end", {"reason": "timeout"}),
    "clear_ask": ("ask.end", {}),
    "call_fn_timeout": ("rpc.cancel", {"reason": "timeout"}),
    "clear_call_fn": ("rpc.cancel", {}),
    "task_start": ("task.indicator", {"running": True}),
    "task_end": ("task.indicator", {"running": False}),
}


def translate(event: str, payload: Any = None) -> Tuple[str, Dict[str, Any]]:
    """Turn one socket.io event into its protocol tag and payload."""
    if event in _COLLAPSED:
        tag, extra = _COLLAPSED[event]
        return tag, dict(extra)
    if event in _RENAMES:
        return _RENAMES[event], _as_payload(payload)
    if event in _WRAPPED:
        tag, key = _WRAPPED[event]
        return tag, {key: payload}
    raise KeyError(
        f"No protocol tag for socket.io event {event!r}. Add it to "
        f"tests/socketspec/frames.py -- an unmapped event would otherwise "
        f"vanish from the ledger and the scenario would pass by silence."
    )


def _as_payload(payload: Any) -> Dict[str, Any]:
    return dict(payload) if isinstance(payload, Mapping) else {"value": payload}


def ask_start(payload: Mapping[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """``emit_ask({"msg": ..., "spec": ...})`` under the new field names."""
    return "ask.start", {"step": payload.get("msg"), "spec": payload.get("spec")}


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


def _element(payload: Mapping[str, Any]) -> Mock:
    element = Mock()
    element.to_dict = Mock(return_value=dict(payload))
    return element


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
        chat_profile=given.chat_profile,
    )
    # A session that just greeted the server is connected by definition --
    # the flag is cleared when the socket is handed over, one step before
    # this one. Left as a Mock attribute it reads truthy, which would make
    # the session look abandoned to every check that asks.
    session.socket_disconnected = False
    if given.parked_reply:
        session.deferred_ask_reply_tasks = [_unfinished()]
    if given.resuming_thread:
        # The session's thread *is* the one it resumes: the live-ask and
        # live-task protections match candidates by thread id, and a session
        # filed under a different one would silently protect nothing.
        session.thread_id = given.resuming_thread
    session.restored = given.restored
    session.chat_started = given.chat_started
    session.fresh_page_load = given.fresh_page_load
    session.thread_id_to_resume = given.resuming_thread
    session.current_task = Mock() if given.running_task else None
    session.transcript_element_dicts = {
        step.id: [dict(element) for element in step.stored_elements]
        for step in given.transcript
        if step.stored_elements
    }
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
        message.id = step.id
        message.to_dict = Mock(return_value={"id": step.id, "output": step.output})
        message.elements = [_element(element) for element in step.elements]
        message._active_wait_payload = dict(step.wait) if step.wait else None
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


class SessionClass:
    """Stands in for the session class while one scenario runs.

    Opening a connection both looks a session up on the class and builds a
    new one through it, so the two cannot be patched apart -- and every
    handler afterwards reaches its session the same way. Which session that
    is changes mid-scenario: once a stale one has been replaced, ``get``
    has to answer with the replacement, exactly as the registry would.
    """

    def __init__(
        self,
        session: Mock,
        given: Given,
        factory: Callable[..., Mock],
        outcome: Dict[str, Any],
    ) -> None:
        self._session = session
        self._held = session if given.server_holds_session else None
        self._factory = factory
        self._outcome = outcome

    def __call__(self, **kwargs: Any) -> Mock:
        built = self._factory(id=kwargs.get("id"))
        built.socket_disconnected = False
        self._outcome["built"] = built
        return built

    def get(self, _sid: str) -> Mock:
        return self._outcome.get("built") or self._session

    def get_by_id(self, _session_id: str) -> Optional[Mock]:
        return self._held


def _unfinished() -> Mock:
    task = Mock()
    task.done = Mock(return_value=False)
    return task


def _registry(
    session: Mock,
    given: Given,
    factory: Callable[..., Mock],
    evicted: List[str],
) -> Dict[str, Mock]:
    """The live-session registry: this scenario's session and its bystanders.

    Always replaced, never merged. Superseding and the live-work checks walk
    this dict directly, so a session another test forgot to close would make
    a scenario about eviction pass -- or fail -- for a reason no row states.

    A bystander's ``delete`` removes it from the registry as it goes, because
    the real one does: eviction only helps if the checks that run right after
    it can already see the thread as idle, and a fake that merely recorded
    the call would leave every such row green for the wrong reason.
    """
    registry: Dict[str, Mock] = {session.id: session}
    for index, other in enumerate(given.bystanders):
        bystander = factory(
            id=f"bystander-{index}",
            pending_ask=_pending_ask(other.pending_ask) if other.pending_ask else None,
            current_task=_unfinished() if other.running_task else None,
        )
        bystander.thread_id = other.thread or session.thread_id
        bystander.socket_disconnected = not other.connected

        async def delete(target: Mock = bystander) -> None:
            evicted.append(target.id)
            registry.pop(target.id, None)

        bystander.delete = Mock(side_effect=delete)
        registry[bystander.id] = bystander
    return registry


class Storage:
    """Persistence holding exactly what the scenario says it holds.

    Stateful, because the rows about the resume="delete" flag are about what
    the *next* read sees. A fake that accepted a deletion and forgot it would
    hand the deleted step straight back on the following handshake, and a row
    claiming "deleted once, and gone" would be asserting against a storage
    that never forgets.

    ``undeletable`` is how a scenario says the storage refuses: the deletion
    is attempted, recorded, and raises. Both halves matter -- the row about
    retrying needs to see the attempt, and the row about keeping the step
    needs the failure to be real.
    """

    def __init__(
        self,
        thread: Mapping[str, Any],
        undeletable: Tuple[str, ...],
        owner: Optional[str],
    ) -> None:
        self._present = bool(thread)
        self._thread: Dict[str, Any] = dict(thread)
        # The resuming user owns the thread unless the scenario says
        # otherwise -- the table has no business knowing the fixture's
        # identifier, and a mismatch would silently route every resume row
        # into the "thread not found" branch.
        self._thread.setdefault("userIdentifier", owner)
        self._undeletable = set(undeletable)
        self.deleted_steps: List[str] = []
        self.deleted_elements: List[str] = []

    async def get_thread(self, thread_id: Optional[str] = None) -> Optional[Dict]:
        if not self._present:
            return None
        thread = dict(self._thread)
        thread["steps"] = [dict(step) for step in self._thread.get("steps") or []]
        thread["elements"] = [dict(el) for el in self._thread.get("elements") or []]
        return thread

    def append(self, steps: Tuple[Mapping[str, Any], ...]) -> None:
        """The conversation carried on while nobody was connected."""
        if steps:
            self._thread["steps"] = list(self._thread.get("steps") or []) + [
                dict(step) for step in steps
            ]

    async def delete_step(self, step_id: str) -> None:
        self.deleted_steps.append(step_id)
        if step_id in self._undeletable:
            raise RuntimeError(f"storage refuses to delete step {step_id}")
        self._thread["steps"] = [
            step
            for step in self._thread.get("steps") or []
            if step.get("id") != step_id
        ]

    async def delete_element(
        self, element_id: str, thread_id: Optional[str] = None
    ) -> None:
        self.deleted_elements.append(element_id)
        if element_id in self._undeletable:
            raise RuntimeError(f"storage refuses to delete element {element_id}")
        self._thread["elements"] = [
            el
            for el in self._thread.get("elements") or []
            if el.get("id") != element_id
        ]


def _data_layer(given: Given, owner: Optional[str]) -> Optional[Storage]:
    """The storage this scenario runs against, or none at all.

    Patched even when the answer is "there is none": the data layer is a
    module-global another test may have installed, and a scenario that reaches
    it by accident is asserting against someone else's fixture.
    """
    if given.stored_thread is None:
        return None
    return Storage(given.stored_thread, given.undeletable, owner)


# The application callbacks a scenario can say are registered. Several
# handshake branches exist only because one of these is not None, so "no app
# is running" is a state the table has to be able to leave.
_HOOK_NAMES = ("chat_start", "chat_resume", "thread_ready")


class Hooks:
    """The application, reduced to what a scenario can assert about it.

    A hook is not a frame, so it is recorded twice over: as a count, and as
    what the callback could *see* from inside itself. The second is the point
    -- ``on_chat_start`` reading the handover out of ``cl.user_session`` is
    the entire reason the handover is applied before the task is scheduled,
    and no assertion made from outside the callback can tell that ordering
    from the reverse one.
    """

    def __init__(self, session: Mock, sessions: Dict[str, Any], ledger: Ledger) -> None:
        self._session = session
        self._sessions = sessions
        self._ledger = ledger
        self.runs: Dict[str, int] = {}
        self.saw: Dict[str, Any] = {}

    def build(self, name: str) -> Callable[..., Awaitable[None]]:
        async def hook(*args: Any) -> None:
            self.runs[name] = self.runs.get(name, 0) + 1
            self._ledger.effect(f"hook:{name}")
            stored = self._sessions.get(self._session.id) or {}
            self.saw[f"{name}_saw_handover"] = stored.get("transit_message")
            if args:
                self.saw[f"{name}_thread"] = args[0]

        return hook


def _config(given: Given, hooks: Hooks) -> Mock:
    config = Mock()
    for name in _HOOK_NAMES:
        registered = hooks.build(name) if name in given.hooks else None
        setattr(config.code, f"on_{name}", registered)
    config.code.on_profile_start = None
    config.code.on_stop = None
    config.code.on_message = None
    config.features.hot_swap_chat_profile = False
    # No required environment variables: a Mock here reads truthy, and every
    # connection would be refused for want of variables no scenario asked for.
    config.project.user_env = None
    return config


async def _settle_hook_tasks(session: Mock) -> None:
    """Let the callbacks the handshake scheduled actually run.

    They are launched with ``create_task`` and never awaited by the handler,
    so a report read the moment it returns would be taken before the
    application did anything -- and the pending task would then surface as a
    warning inside whichever test ran next.
    """
    tasks = [
        task
        for name in ("current_task", "thread_ready_task", "profile_start_task")
        for task in (getattr(session, name, None),)
        if isinstance(task, asyncio.Task)
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _hello(_session: Mock, _payload: Mapping[str, Any]) -> None:
    await _connection_successful(SID)


async def _ask_reply_frame(_session: Mock, payload: Mapping[str, Any]) -> None:
    await _ask_reply(SID, dict(payload))


async def _stop_frame(_session: Mock, _payload: Mapping[str, Any]) -> None:
    await _stop(SID)


async def _clear_frame(_session: Mock, _payload: Mapping[str, Any]) -> None:
    await _clean_session(SID)


OPEN = "session.open"
"""The frame that opens a connection.

Not in ``HANDLERS``: it is the one frame that arrives *before* there is a
session to hand to a handler, which is the whole reason the behaviour it
carries is worth stating.
"""


async def _open(
    given: Given, payload: Mapping[str, Any], outcome: Dict[str, Any]
) -> None:
    """Run the real connection handler and record what it decided."""
    auth = {
        "sessionId": "test_session_id",
        "userEnv": None,
        "clientType": "webapp",
        "chatProfile": given.chat_profile,
        "threadId": None,
        "pageLoad": bool(payload.get("reload")),
    }
    try:
        await _connect(SID, {}, auth)  # type: ignore[arg-type]
    except ConnectionRefusedError:
        outcome["result"] = "refused"
        return
    built = outcome.get("built")
    if built is None:
        outcome["result"] = "kept"
    else:
        outcome["result"] = "replaced" if outcome.get("dropped") else "created"


# The client -> server half of the vocabulary. Not used to dispatch -- the
# handlers below are imported directly -- but written down so the boundary
# test can check that no portable module names one of them.
_INBOUND_EVENTS = (
    "connect",
    "connection_successful",
    "disconnect",
    "clear_session",
    "switch_chat_profile",
    "stop",
    "ask_reply",
    "client_message",
    "edit_message",
    "message_favorite",
    "fetch_favorites",
    "window_message",
    "audio_start",
    "audio_chunk",
    "audio_end",
    "chat_settings_change",
    "chat_settings_edit",
)


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


def _report(
    session: Mock,
    pending: Optional[PendingAsk],
    hooks: Hooks,
    sessions: Dict[str, Any],
    owner: Optional[str],
    storage: Optional[Storage],
    registry: Dict[str, Mock],
    evicted: List[str],
    outcome: Dict[str, Any],
) -> Dict[str, Any]:
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
        "has_first_interaction": session.has_first_interaction,
        "parent_thread_id": session.parent_thread_id,
        "hook_runs": dict(hooks.runs),
        # Read, not observed: a record still parked is the *absence* of a
        # frame, and no ledger can show that.
        "handover_parked": transit.pop(session.id, owner) is not transit.NO_TRANSIT,
        "handover_delivered": (sessions.get(session.id) or {}).get("transit_message"),
    }
    current = outcome.get("built") or session
    state["on_open"] = outcome.get("result")
    state["fresh_page_load"] = current.fresh_page_load
    state["evicted"] = list(evicted)
    state["live_sessions"] = sorted(registry)
    state["deleted_steps"] = list(storage.deleted_steps) if storage else []
    state["deleted_elements"] = list(storage.deleted_elements) if storage else []
    state.update(hooks.saw)
    if pending is not None and pending.future.done() and not pending.future.cancelled():
        state["ask_answer"] = pending.future.result()
    return state


def _park_handover(session: Mock, given: Given, owner: Optional[str]) -> None:
    """Put the scenario's handover into the real transit store.

    The real one, not a stand-in: the ownership check and the "a record may
    carry a parent and no message" shape are the behaviour under test, and a
    fake would only prove the fake agrees with the scenario. Cleared either
    way -- an unclaimed record from a previous test is exactly the leak
    ``pop`` exists to make impossible, so the spec must not depend on one
    being absent by luck.
    """
    transit.clear()
    if given.handover is None:
        return
    transit.store(
        session.id,
        given.handover.message,
        "someone-else" if given.handover.foreign else owner,
        given.handover.parent,
    )


async def run(scenario: Scenario, session_factory: Callable[..., Mock]) -> Result:
    """Drive one scenario against the socket.io handlers."""
    ledger = Ledger()
    session = _session(session_factory, scenario.given, ledger)
    pending: Optional[PendingAsk] = session.pending_ask
    # The user who is arriving, which is not always the one the held session
    # belongs to -- that difference is the whole ownership check.
    arriving = session.user
    owner = arriving.identifier if arriving else None
    if scenario.given.owned_by_someone_else:
        session.user = Mock(identifier="a-different-person")

    # Everything here is a module global the previous test may have written
    # to. Each is replaced whether or not the scenario mentions it: a spec
    # whose world depends on test ordering is not a spec.
    sessions: Dict[str, Any] = {}
    hooks = Hooks(session, sessions, ledger)
    evicted: List[str] = []
    outcome: Dict[str, Any] = {}

    async def dropped() -> None:
        outcome["dropped"] = True

    session.delete = Mock(side_effect=dropped)
    registry = _registry(session, scenario.given, session_factory, evicted)
    storage = _data_layer(scenario.given, owner)
    _park_handover(session, scenario.given, owner)

    context = Mock()
    context.session = session
    context.emitter = RecordingEmitter(ledger, _interrupt(session, scenario.given))

    try:
        with (
            patch("chainlit.socket.init_ws_context", return_value=context),
            patch("chainlit.socket.config", _config(scenario.given, hooks)),
            patch(
                "chainlit.socket.WebsocketSession",
                SessionClass(session, scenario.given, session_factory, outcome),
            ),
            patch("chainlit.socket.require_login", Mock(return_value=True)),
            patch(
                "chainlit.socket._authenticate_connection",
                AsyncMock(return_value=(arriving, "token")),
            ),
            patch("chainlit.socket.Message", side_effect=_message_factory(ledger)),
            patch("chainlit.socket.chat_context", _transcript(scenario.given)),
            patch("chainlit.socket.user_sessions", sessions),
            patch("chainlit.socket.wait_for_persist", AsyncMock()),
            patch("chainlit.session.ws_sessions_id", registry),
            patch("chainlit.socket.get_data_layer", return_value=storage),
        ):
            for index, frame in enumerate(scenario.when):
                if frame.tag == OPEN:
                    await _open(scenario.given, frame.payload, outcome)
                    continue
                handler = HANDLERS.get(frame.tag)
                if handler is None:
                    raise KeyError(
                        f"The legacy driver has no handler for inbound {frame.tag!r}."
                    )
                await handler(session, frame.payload)
                if index == 0 and storage is not None:
                    storage.append(scenario.given.produced_between_connections)

            # Rescuing an orphaned reply is a background task parked on the
            # handshake gate. A ledger read before it finishes would be missing
            # the frames the scenario is about.
            parked = [
                task
                for task in getattr(session, "deferred_ask_reply_tasks", ()) or ()
                if isinstance(task, asyncio.Task)
            ]
            if parked:
                await asyncio.gather(*parked, return_exceptions=True)

            await _settle_hook_tasks(session)

        return Result(
            ledger=ledger,
            state=_report(
                session,
                pending,
                hooks,
                sessions,
                owner,
                storage,
                registry,
                evicted,
                outcome,
            ),
        )
    finally:
        transit.clear()


def build(request: Any) -> Driver:
    """The driver, with the fixtures it happens to need.

    Each driver pulls its own: this one wants the session mock factory, and
    the driver for the new transport will want a Litestar test client and
    nothing else. A runner that passed one implementation's fixtures to every
    driver would have to change the moment a second one existed.
    """
    session_factory = request.getfixturevalue("mock_session_factory")

    def drive(scenario: Scenario) -> Awaitable[Result]:
        return run(scenario, session_factory)

    return drive


__all__ = ["HANDLERS", "OPEN", "RecordingEmitter", "build", "run"]
