"""The driver for the native websocket stack.

In-process and transport-free on purpose. Litestar's ``WebSocketTestSession``
is a blocking portal over a ``queue.Queue``; driving sixty scenarios through
it would make every row a timing question. What the socket handler does with
a frame is a handful of calls -- ``arrive``, ``on_arrival``, ``ready_frame``,
``restore``, ``on_ready`` for a ``hello``, ``decode_client`` and ``_dispatch``
for everything after it -- and this driver makes those same calls, in that
same order, against a real ``ApplicationRunner`` over a real registry,
transit store and session. The only thing not exercised is the socket loop
itself, which ``tests/test_runner.py`` covers end to end.

What a ``Given`` becomes
------------------------
The table states facts about the conversation; the driver builds the objects
that make them true and then lets the implementation decide everything else.

* ``server_holds_session`` -- a ``Session`` registered under the id the
  client offers, carrying the transcript, the question, the running task and
  the parked reply the row describes. One exception, and it is a translation
  rather than a shortcut: a row with ``restored=True`` and neither a started
  chat nor a first interaction is the successor of a profile switch. The old
  backend built that session ahead of the client; this one mints only the
  id and builds the session on arrival, so nothing is held.
* ``restored`` -- the session is *handed back*. In production that is the
  ``kept`` outcome of ``registry.claim``, which the ``reload`` rows pin on
  their own; every other family states the hand-back as a precondition, and
  several of them state it about a session the claim would replace (idle,
  and the client reloaded). So for the first ``hello`` of a ``restored`` row
  the driver builds the ``kept`` ``Arrival`` itself, doing exactly what
  ``arrive``'s kept branch does. Every hello after the first goes through
  ``arrive`` as a reconnect.
* ``stored_thread`` -- a stub unit of work under the real
  ``ThreadStoreAdapter`` and the real ``ApplicationRunner._resume``, so the
  record-to-frame conversion under test is the implementation's.
* ``hooks`` -- recording callbacks on the test config's ``code``.
* ``handover`` -- a record parked in a real ``TransitStore``.

What it reports
---------------
``Result.state`` carries every key a row reads (grep ``result.state[`` in
``cases/``), whatever the row. The ledger is the session's outbound queue,
read through the wire codec, so a tag is a fact about the wire and not about
a struct's name.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
)

import msgspec
import pytest

from chainlit.context import context
from chainlit.emitter import Emitter
from chainlit.persistence.records import ElementRecord, StepRecord, ThreadDetail
from chainlit.protocol.client import Hello
from chainlit.protocol.codec import ErrorCode, decode_client, encode_server
from chainlit.protocol.payloads import (
    Action,
    AskActionReply,
    AskActionSpec,
    AskElementSpec,
    AskFileSpec,
    AskSpec,
    AskTextReply,
    AskTextSpec,
    Element,
    Step,
    Wait,
)
from chainlit.protocol.server import Error
from chainlit.runner import ApplicationRunner, ThreadStoreAdapter
from chainlit.transit_store import TransitStore
from chainlit.ws.connection import HEARTBEAT_INTERVAL_MS, _dispatch
from chainlit.ws.handshake import Arrival, arrive, ready_frame, restore
from chainlit.ws.registry import ClaimOutcome, SessionRegistry
from chainlit.ws.session import PendingAsk, Session, TranscriptEntry

from .frames import Frame, Ledger
from .spec import AskState, Given, Incoming, Result, Scenario

__all__ = ["KNOWN_BUGS", "NativeDriver"]

SESSION_ID = "s1"
USER = "user-1"
SOMEONE_ELSE = "someone-else"
DEFAULT_THREAD = "thread-main"
#: How many loop turns a frame is given to settle. Fixed, never gated on
#: the session's tasks: the ``running_task`` rows hold a sleeper on purpose.
SETTLE_TURNS = 25
ACTION = {"id": "a1", "name": "continue", "forId": "step-1"}


KNOWN_BUGS: Dict[str, str] = {
    # Every entry is an implementation defect a row exposes, with a pointer
    # to it. ``test_spec`` marks these xfail(strict), so the row flips back
    # to green -- and the entry has to go -- the moment the fix lands.
}


# ----------------------------------------------------------------- helpers


def _element(raw: Mapping[str, Any]) -> Element:
    """An element dict from the table, which names only id and forId."""
    return msgspec.convert(
        {"type": "text", "name": raw.get("id", ""), **dict(raw)}, Element
    )


def _action(raw: Mapping[str, Any]) -> Action:
    return msgspec.convert(dict(raw), Action)


def _step(raw: Mapping[str, Any]) -> Step:
    return msgspec.convert(dict(raw), Step)


def _spec(state: AskState) -> AskSpec:
    kinds: Dict[str, Callable[..., AskSpec]] = {
        "text": AskTextSpec,
        "file": AskFileSpec,
        "action": AskActionSpec,
        "element": AskElementSpec,
    }
    return kinds[state.type](step_id=state.step_id, timeout=state.timeout)


def _pending_ask(state: AskState) -> PendingAsk:
    """The table's ``AskState``, field for field, as the session holds it."""
    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    if state.answered:
        future.set_result("already answered")
    remaining = -1.0 if state.remaining is None else float(state.remaining)
    return PendingAsk(
        step_id=state.step_id,
        step=Step(
            id=state.step_id,
            parent_id=state.parent_id,
            type="assistant_message",
            wait_for_answer=True,
        ),
        spec=_spec(state),
        future=future,
        deadline=time.monotonic() + remaining,
        restore_actions=[_action(a) for a in state.actions],
        restore_element=_element(state.element) if state.element else None,
    )


def _thread_detail(thread: Mapping[str, Any]) -> ThreadDetail:
    """What persistence answers for the thread, as the services would."""
    thread_id = str(thread.get("id", DEFAULT_THREAD))
    return ThreadDetail(
        id=thread_id,
        user_identifier=USER,
        steps=[
            msgspec.convert(
                {"threadId": thread_id, "type": "undefined", **dict(step)}, StepRecord
            )
            for step in thread.get("steps") or []
        ],
        elements=[
            msgspec.convert(
                {
                    "threadId": thread_id,
                    "name": element.get("id", ""),
                    "type": "text",
                    **dict(element),
                },
                ElementRecord,
            )
            for element in thread.get("elements") or []
        ],
    )


def _frame(msg: Any) -> Frame:
    """A queued struct as the wire carries it."""
    data = json.loads(encode_server(msg))
    return Frame(tag=data.pop("t"), payload=data)


# ----------------------------------------------------- persistence, stubbed


class _Records:
    """The two services the resume path reads and the one it deletes through.

    Nothing here is a database; it is the answer a database would give, so
    that ``ApplicationRunner._resume`` and ``ThreadStoreAdapter`` -- the
    real ones -- can run over it.
    """

    def __init__(self, thread: Optional[Mapping[str, Any]], undeletable: Set[str]):
        self.detail = _thread_detail(thread) if thread else None
        self.undeletable = undeletable
        self.deleted_steps: List[str] = []
        self.deleted_elements: List[str] = []

    # ThreadService
    async def get_detail(self, thread_id: str) -> Optional[ThreadDetail]:
        if self.detail is not None and self.detail.id == thread_id:
            return self.detail
        return None

    # StepService
    async def remove(self, step_id: str) -> None:
        detail = self.detail
        if detail is not None:
            for element in detail.elements:
                if element.for_id == step_id:
                    self.deleted_elements.append(element.id)
                    if element.id in self.undeletable:
                        raise RuntimeError(f"storage refuses to delete {element.id}")
        self.deleted_steps.append(step_id)

    # UserService
    async def get_by_identifier(self, identifier: str) -> None:
        return None

    def add(self, step: Mapping[str, Any]) -> None:
        """A step the conversation produced after the thread was read."""
        if self.detail is None:
            return
        self.detail.steps.append(
            msgspec.convert(
                {"threadId": self.detail.id, "type": "undefined", **dict(step)},
                StepRecord,
            )
        )


@dataclass
class _Unit:
    threads: _Records
    steps: _Records
    users: _Records


class _Persistence:
    """The ``Persistence`` port as ``runner`` and the adapter use it."""

    storage = None

    def __init__(self, records: _Records) -> None:
        self.records = records

    @asynccontextmanager
    async def uow(self, session: Any = None) -> AsyncIterator[_Unit]:
        yield _Unit(self.records, self.records, self.records)


# ------------------------------------------------------------------ driver


class _User:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier
        self.id = identifier


class _Run:
    """One scenario's worth of objects, built once and torn down once."""

    def __init__(self, scenario: Scenario, config: Any, files_root: Any) -> None:
        self.scenario = scenario
        self.given: Given = scenario.given
        self.config = config
        self.files_root = files_root

        self.registry = SessionRegistry()
        self.transit = TransitStore()
        self.records = _Records(self.given.stored_thread, set(self.given.undeletable))
        persistence = (
            _Persistence(self.records) if self.given.stored_thread is not None else None
        )
        self.thread_store = (
            ThreadStoreAdapter(persistence)  # type: ignore[arg-type]
            if persistence is not None
            else None
        )
        self.runner = ApplicationRunner(
            config,
            registry=self.registry,
            persistence=persistence,  # type: ignore[arg-type]
            transit=self.transit,
        )
        self.user = _User(USER)
        self.thread_id: str = self.given.resuming_thread or str(
            (self.given.stored_thread or {}).get("id") or DEFAULT_THREAD
        )

        self.hook_runs: Dict[str, int] = {}
        self.seen: Dict[str, Any] = {}
        self.sleepers: List[asyncio.Task[Any]] = []
        self.sessions: List[Session] = []
        self.bystanders: List[Session] = []
        self.held: Optional[Session] = None
        self.session: Optional[Session] = None
        self.ask: Optional[PendingAsk] = None
        self.sent_values: List[Tuple[Any, Any]] = []
        self.on_open: Optional[str] = None
        self.fresh_page_load: bool = True

    # ------------------------------------------------------------- setup

    def _register_hooks(self) -> None:
        code = self.config.code
        runs = self.hook_runs
        seen = self.seen

        async def on_chat_start() -> None:
            runs["chat_start"] = runs.get("chat_start", 0) + 1
            seen["chat_start_saw_handover"] = context.session.state.get(
                "transit_message"
            )

        async def on_chat_resume(thread: Mapping[str, Any]) -> None:
            runs["chat_resume"] = runs.get("chat_resume", 0) + 1
            seen["chat_resume_thread"] = thread

        async def on_thread_ready(thread: Mapping[str, Any]) -> None:
            runs["thread_ready"] = runs.get("thread_ready", 0) + 1

        hooks = {
            "chat_start": on_chat_start,
            "chat_resume": on_chat_resume,
            "thread_ready": on_thread_ready,
        }
        for name in self.given.hooks:
            setattr(code, f"on_{name}", hooks[name])

    def _sleeper(self) -> asyncio.Task[Any]:
        task = asyncio.create_task(asyncio.sleep(3600))
        self.sleepers.append(task)
        return task

    def _new_session(self, session_id: str, *, thread_id: Optional[str]) -> Session:
        session = Session(
            id=session_id,
            runner=self.runner,
            user=self.user,
            thread_id=thread_id,
            chat_profile=self.given.chat_profile,
            files_root=self.files_root,
        )
        self.sessions.append(session)
        return session

    def _make_session(self, session_id: str, hello: Hello) -> Session:
        """What the socket hands ``arrive`` for an id it has decided is new.

        The runner's own factory would also start a ``SessionWriter``, which
        is the database half and not on this table.
        """
        return self._new_session(
            session_id, thread_id=hello.thread_id or str(uuid.uuid4())
        )

    @property
    def _switch_successor(self) -> bool:
        """A session handed its id by a switch: minted, never built."""
        given = self.given
        return (
            given.restored
            and not given.chat_started
            and not given.has_first_interaction
        )

    def _build_held(self) -> None:
        given = self.given
        if not given.server_holds_session or self._switch_successor:
            return
        session = self._new_session(SESSION_ID, thread_id=self.thread_id)
        session.chat_started = given.chat_started
        if given.has_first_interaction and given.stored_thread is not None:
            # A session whose memory can be empty while storage holds the
            # conversation is one that resumed it: the transcript of a
            # session that wrote the thread itself lives on the session and
            # goes nowhere. ``restore`` reads storage only for the thread the
            # application let the session resume, so that is the fact to state.
            session.first_interaction = "resume"
            session.resumed_thread_id = session.thread_id
        elif given.has_first_interaction:
            session.first_interaction = "message"
        session.parent_thread_id = given.parent_thread
        if given.pending_ask is not None:
            self.ask = _pending_ask(given.pending_ask)
            session.pending_ask = self.ask
        if given.running_task:
            session.current_task = self._sleeper()
        if given.parked_reply:
            session.parked_replies.append(
                {"stepId": "earlier-step", "value": {"kind": "action"}}
            )
        for step in given.transcript:
            wait = msgspec.convert(dict(step.wait), Wait) if step.wait else None
            entry = TranscriptEntry(
                step=Step(id=step.id, output=step.output, wait=wait),
                elements=[_element(e) for e in (*step.elements, *step.stored_elements)],
            )
            session.transcript.append(entry)
        # The socket that carried it is gone: that is why the client is
        # arriving.
        session.connected = False
        self.registry.register(
            session,
            user_identifier=SOMEONE_ELSE if given.owned_by_someone_else else USER,
            thread_id=session.thread_id,
            connected=False,
        )
        self.held = session

    def _build_bystanders(self) -> None:
        for index, bystander in enumerate(self.given.bystanders):
            session = self._new_session(
                f"bystander-{index}", thread_id=bystander.thread or self.thread_id
            )
            session.chat_started = True
            session.first_interaction = "message"
            if bystander.pending_ask is not None:
                session.pending_ask = _pending_ask(bystander.pending_ask)
            if bystander.running_task:
                session.current_task = self._sleeper()
            session.connected = bystander.connected
            self.registry.register(
                session,
                user_identifier=USER,
                thread_id=session.thread_id,
                connected=bystander.connected,
            )
            self.bystanders.append(session)

    async def _park_handover(self) -> None:
        handover = self.given.handover
        if handover is None:
            return
        await self.transit.park(
            SESSION_ID,
            handover.message,
            SOMEONE_ELSE if handover.foreign else USER,
            parent=handover.parent,
        )

    async def setup(self) -> None:
        self._register_hooks()
        self._build_held()
        self._build_bystanders()
        await self._park_handover()

    # ------------------------------------------------------------ frames

    async def _settle(self) -> None:
        for _ in range(SETTLE_TURNS):
            await asyncio.sleep(0)

    async def _between_connections(self) -> None:
        """The conversation goes on after the first frame is handled."""
        session = self.session
        if session is None:
            return
        emitter = Emitter(session)
        for step in self.given.produced_between_connections:
            emitter.send_step(dict(step))
            self.records.add(step)

    async def _hello(self, incoming: Incoming, *, first: bool) -> None:
        given = self.given
        page_load = bool(
            incoming.payload.get("pageLoad", given.fresh_page_load if first else False)
        )
        hello = Hello(
            session_id=SESSION_ID,
            thread_id=given.resuming_thread,
            chat_profile=given.chat_profile,
            page_load=page_load,
        )

        if first and given.restored and self.held is not None:
            # The hand-back the row states as a fact. What ``arrive`` does
            # on its ``kept`` branch, and nothing else.
            held = self.held
            held.connected = True
            self.registry.mark_connected(held.id)
            arrival = Arrival(
                outcome=ClaimOutcome.KEPT, session=held, fresh_page_load=page_load
            )
        else:
            arrival = await arrive(
                registry=self.registry,
                session_id=SESSION_ID,
                user_identifier=USER,
                page_load=page_load,
                thread_id=hello.thread_id,
                make_session=lambda sid: self._make_session(sid, hello),
            )

        self.on_open = arrival.outcome.value
        self.fresh_page_load = arrival.fresh_page_load
        if arrival.refused or arrival.session is None:
            return

        session = arrival.session
        self.session = session
        await self.runner.on_arrival(arrival)
        session.send(
            ready_frame(
                session,
                restored=arrival.outcome is ClaimOutcome.KEPT,
                heartbeat_ms=HEARTBEAT_INTERVAL_MS,
            )
        )
        await restore(
            session,
            thread_store=self._store_during_restore(session),
            fresh_page_load=self.fresh_page_load,
        )
        await self.runner.on_ready(arrival)
        await self._settle()

    def _store_during_restore(self, session: Session) -> Any:
        """The thread store, with ``Given.during_restore`` landing in its await.

        The restore has exactly one place something can happen while it
        runs: the read of the thread store, when memory is empty. That is
        where the event is injected. A row that names an event with no
        such gap is asking for an interleaving the implementation cannot
        produce, and the driver says so rather than pretending it happened.
        """
        event = self.given.during_restore
        store = self.thread_store
        if event is None:
            return store
        if store is None or session.transcript:
            raise AssertionError(
                f"during_restore={event!r}: handshake.restore has no await to "
                "land in without an empty memory and a thread store"
            )
        run = self
        kind: str = event
        backing: ThreadStoreAdapter = store

        class _Interposed:
            async def transcript_of(self, thread_id: str) -> Any:
                entries = await backing.transcript_of(thread_id)
                await run._during_restore(kind, session)
                return entries

        return _Interposed()

    async def _during_restore(self, event: str, session: Session) -> None:
        ask = session.pending_ask
        if event == "answer":
            if ask is not None:
                await self._frame(
                    Incoming("ask.reply", {"stepId": ask.step_id, "value": ACTION})
                )
            return
        successor = _pending_ask(AskState(step_id="successor-step", actions=(ACTION,)))
        if event == "successor_dead":
            successor.future.set_result("gone")
        else:
            # It has already sent its own form.
            Emitter(session).add_action(ACTION)
        session.pending_ask = successor

    def _reply_value(self, value: Any) -> Any:
        """The table's loose reply values, in the protocol's tagged shape.

        The table predates the tagged reply union and writes a click as the
        action dict and a typed answer as the step dict or a bare string.
        The translation is recorded so the answer can be reported back in
        the row's own terms.
        """
        converted: Any
        if isinstance(value, str):
            converted = AskTextReply(
                step=Step(id=str(uuid.uuid4()), type="user_message", output=value)
            )
        elif isinstance(value, Mapping) and "kind" in value:
            converted = value
        elif isinstance(value, Mapping) and value.get("type") == "user_message":
            converted = AskTextReply(step=_step(value))
        elif isinstance(value, Mapping) and "name" in value:
            converted = AskActionReply(
                action=_action({"id": value.get("id", value["name"]), **value})
            )
        else:
            converted = value
        self.sent_values.append((converted, value))
        return converted

    def _wire(self, incoming: Incoming) -> bytes:
        payload: Dict[str, Any] = dict(incoming.payload)
        if incoming.tag == "ask.reply" and "value" in payload:
            payload["value"] = self._reply_value(payload["value"])
        return msgspec.json.encode({"t": incoming.tag, **payload})

    async def _frame(self, incoming: Incoming) -> None:
        """What ``connection._read_loop`` does with one inbound frame."""
        session = self.session
        if session is None:
            raise AssertionError(f"{incoming.tag!r} arrived with no session open")
        try:
            message = decode_client(self._wire(incoming))
        except msgspec.ValidationError as error:
            session.send(Error(code=ErrorCode.UNKNOWN_TAG.value, message=str(error)))
            return
        except msgspec.DecodeError as error:
            session.send(Error(code=ErrorCode.BAD_MESSAGE.value, message=str(error)))
            return
        await _dispatch(session, message)
        await self._settle()

    async def run(self) -> None:
        self.session = self.held
        for index, incoming in enumerate(self.scenario.when):
            if incoming.tag == "hello":
                if index > 0:
                    await self._between_connections()
                await self._hello(incoming, first=index == 0)
            else:
                await self._frame(incoming)

    # ------------------------------------------------------------ report

    def _answer(self) -> Any:
        ask = self.ask
        if ask is None or not ask.future.done() or ask.future.cancelled():
            return None
        result = ask.future.result()
        for converted, original in self.sent_values:
            if result == converted:
                return original
        return result

    async def result(self) -> Result:
        ledger = Ledger()
        for landed in self.sessions:
            for msg in landed.outbound.pending_frames:
                frame = _frame(msg)
                ledger.wire(frame.tag, frame.payload)

        session = self.session
        ask = self.ask
        resolved = ask is not None and ask.future.done() and not ask.future.cancelled()
        parked = await self.transit.store.get(self.transit._key(SESSION_ID))
        state: Dict[str, Any] = {
            "on_open": self.on_open,
            "fresh_page_load": self.fresh_page_load,
            "ask_resolved": resolved,
            "ask_answer": self._answer(),
            "ask_cancelled": ask is not None and ask.future.cancelled(),
            "ask_pending": session is not None and session.pending_ask is not None,
            "last_resolved_ask_step_id": (
                ask.step_id
                if resolved and ask is not None
                else self.given.last_resolved_ask_step_id
            ),
            "parked_replies": list(session.parked_replies) if session else [],
            "hook_runs": dict(self.hook_runs),
            "chat_start_saw_handover": self.seen.get("chat_start_saw_handover"),
            "chat_resume_thread": self.seen.get("chat_resume_thread"),
            "has_first_interaction": bool(session and session.first_interaction),
            "handover_delivered": (
                session.state.get("transit_message") if session else None
            ),
            "handover_parked": parked is not None,
            "parent_thread_id": session.parent_thread_id if session else None,
            "evicted": [
                bystander.id
                for bystander in self.bystanders
                if bystander.id not in self.registry
            ],
            "live_sessions": [entry.id for entry in self.registry],
            "deleted_steps": list(self.records.deleted_steps),
            "deleted_elements": list(self.records.deleted_elements),
        }
        return Result(ledger=ledger, state=state)

    # ---------------------------------------------------------- teardown

    async def close(self) -> None:
        pending: List[asyncio.Task[Any]] = list(self.sleepers)
        for session in self.sessions:
            session.cancel_work()
            for task in (
                session.current_task,
                session.thread_ready_task,
                session.reaper,
            ):
                if task is not None and not task.done():
                    task.cancel()
                    pending.append(task)
        for task in list(self.runner._background):
            task.cancel()
            pending.append(task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


class NativeDriver:
    """``Driver(scenario) -> Awaitable[Result]`` over the in-process stack."""

    def __init__(self, request: pytest.FixtureRequest) -> None:
        self.config = request.getfixturevalue("test_config")
        self.files_root = request.getfixturevalue("tmp_path")

    def __call__(self, scenario: Scenario) -> Awaitable[Result]:
        return self._drive(scenario)

    async def _drive(self, scenario: Scenario) -> Result:
        run = _Run(scenario, self.config, self.files_root)
        try:
            await run.setup()
            await run.run()
            return await run.result()
        finally:
            await run.close()
