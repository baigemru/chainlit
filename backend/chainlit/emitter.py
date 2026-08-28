"""What ``cl.*`` says to the browser, and nothing else.

One method per thing the application can put on screen, each producing one
frame from ``chainlit.protocol.server`` and handing it to the session's
queue. The transcript the reconnect replay reads is maintained here too,
because the replay must show exactly what was sent, in the order it was
sent, and the only place that knows both is the place that sends.

Persistence is deliberately absent. ``chainlit.persist`` writes rows; this
module writes frames; ``init_thread`` is the one seam where the two meet,
and it says so.

Everything takes the dictionaries ``to_dict()`` produces and converts them
at the edge. The conversion is the validation: a dict that does not fit the
frame is a bug in the caller, and it fails here rather than on the wire.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any, List, Mapping, Optional, Sequence, Union

import msgspec
from msgspec import UNSET, UnsetType

from chainlit.logger import logger
from chainlit.protocol.payloads import (
    Action,
    AskActionReply,
    AskElementReply,
    AskFileReply,
    AskFileSpec,
    AskSpec,
    AskTextReply,
    Element,
    Step,
    StepPatch,
)
from chainlit.protocol.server import (
    ActionAdd,
    ActionRemove,
    AskEnd,
    AskEndReason,
    AskStart,
    ElementRemove,
    ElementUpsert,
    Error,
    ProfileChanged,
    SessionHandoff,
    SidebarSet,
    StepDelete,
    StepStreamStart,
    StepStreamToken,
    StepUpdate,
    StepUpsert,
    TaskIndicator,
    ThreadFirstInteraction,
    ThreadOpen,
    Toast,
    ToastType,
)
from chainlit.types import AskSlotBusyError
from chainlit.ws.session import PendingAsk, Session, TranscriptEntry

if TYPE_CHECKING:
    from chainlit.transit_store import TransitStore

__all__ = ["Emitter"]


def _as_step(step: Mapping[str, Any]) -> Step:
    return msgspec.convert(step, Step)


def _as_element(element: Mapping[str, Any]) -> Element:
    return msgspec.convert(element, Element)


def _as_action(action: Mapping[str, Any]) -> Action:
    return msgspec.convert(action, Action)


class Emitter:
    """The application's voice on one session."""

    __slots__ = ("session", "transit")

    def __init__(self, session: Session, *, transit: Optional["TransitStore"] = None):
        self.session = session
        self.transit = transit

    # ------------------------------------------------------------ transcript

    def _entry(self, step_id: str) -> Optional[TranscriptEntry]:
        for entry in self.session.transcript:
            if entry.step.id == step_id:
                return entry
        return None

    def _remember(self, step: Step) -> None:
        """Upsert the step in the transcript, keeping its attachments."""
        entry = self._entry(step.id)
        if entry is None:
            self.session.transcript.append(TranscriptEntry(step=step))
        else:
            entry.step = step

    # ----------------------------------------------------------------- steps

    def send_step(self, step: Mapping[str, Any]) -> None:
        """Put a step on screen, or replace the one with the same id."""
        payload = _as_step(step)
        self._remember(payload)
        self.session.send(StepUpsert(step=payload))

    def update_step(self, step: Mapping[str, Any]) -> None:
        """Change the fields a step dict carries; absent ones stay."""
        patch = msgspec.convert(step, StepPatch)
        entry = self._entry(patch.id)
        if entry is not None:
            entry.step = msgspec.convert(
                {**msgspec.to_builtins(entry.step), **dict(step)}, Step
            )
        self.session.send(StepUpdate(step=patch))

    def delete_step(self, step_id: str) -> None:
        self.session.transcript[:] = [
            entry for entry in self.session.transcript if entry.step.id != step_id
        ]
        self.session.send(StepDelete(step_id=step_id))

    def stream_start(self, step: Mapping[str, Any]) -> None:
        payload = _as_step(step)
        self._remember(payload)
        self.session.send(StepStreamStart(step=payload))

    def send_token(
        self, id: str, token: str, is_sequence: bool = False, is_input: bool = False
    ) -> None:
        entry = self._entry(id)
        if entry is not None:
            field = "input" if is_input else "output"
            current = getattr(entry.step, field)
            setattr(entry.step, field, token if is_sequence else current + token)
        self.session.send(
            StepStreamToken(
                id=id, token=token, is_sequence=is_sequence, is_input=is_input
            )
        )

    # -------------------------------------------------------------- elements

    def send_element(self, element: Mapping[str, Any]) -> None:
        """Show an element, attached to the step it names in ``forId``."""
        payload = _as_element(element)
        if payload.for_id and (entry := self._entry(payload.for_id)) is not None:
            entry.elements[:] = [e for e in entry.elements if e.id != payload.id]
            entry.elements.append(payload)
        self.session.send(ElementUpsert(element=payload))

    def remove_element(self, element_id: str) -> None:
        for entry in self.session.transcript:
            entry.elements[:] = [e for e in entry.elements if e.id != element_id]
        self.session.send(ElementRemove(id=element_id))

    # --------------------------------------------------------------- actions

    def add_action(self, action: Mapping[str, Any]) -> None:
        self.session.send(ActionAdd(action=_as_action(action)))

    def remove_action(self, action_id: str) -> None:
        self.session.send(ActionRemove(id=action_id))

    # ------------------------------------------------------------------ asks

    async def send_ask_user(
        self,
        step: Mapping[str, Any],
        spec: AskSpec,
        raise_on_timeout: bool = False,
        *,
        restore_actions: Optional[Sequence[Mapping[str, Any]]] = None,
        restore_element: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        """Put a question on screen and wait for its answer.

        Returns what the question type returns -- the answering step dict
        for a text ask, the spooled files for a file ask, the action dict or
        the element props otherwise -- or ``None`` on a timeout unless
        ``raise_on_timeout`` asks for the ``TimeoutError``.

        The deadline is fixed here and never extended: a reconnect re-sends
        the form with whatever is left of it (see ``handshake.restore``).
        """
        session = self.session
        existing = session.pending_ask
        if existing is not None and not existing.future.done():
            # A second question would silently replace the first form on
            # screen and orphan the coroutine waiting on it. Refuse instead.
            if _strict_ask_slot():
                raise AskSlotBusyError(existing.step_id)
            logger.error(
                "An ask is already pending for session %s; returning None", session.id
            )
            return None

        payload = _as_step(step)
        loop = asyncio.get_running_loop()
        ask = PendingAsk(
            step_id=spec.step_id,
            step=payload,
            spec=spec,
            future=loop.create_future(),
            deadline=time.monotonic() + spec.timeout,
            restore_actions=[_as_action(a) for a in restore_actions or ()],
            restore_element=(
                _as_element(restore_element) if restore_element is not None else None
            ),
        )
        session.pending_ask = ask
        self._remember(payload)
        self._deliver_parked(ask)

        try:
            if isinstance(spec, AskFileSpec):
                # The upload route checks the file against the ask that
                # wants it, keyed by the message the form is drawn under.
                session.files_spec[payload.parent_id or payload.id] = spec

            session.send(AskStart(step=payload, spec=spec))
            # The spinner goes dark while the user is the one who has to
            # act. Level state: restored from the tasks, not counted.
            session.send(TaskIndicator(running=False))

            try:
                value = await asyncio.wait_for(
                    asyncio.shield(ask.future), timeout=spec.timeout
                )
            except TimeoutError:
                # wait_for can time out in the same tick the answer landed;
                # the answer wins.
                if ask.future.done() and not ask.future.cancelled():
                    value = ask.future.result()
                else:
                    session.send(AskEnd(step_id=spec.step_id, reason="timeout"))
                    if raise_on_timeout:
                        raise
                    return None

            result = await self._answer(ask, value)
            session.send(AskEnd(step_id=spec.step_id, reason="answered"))
            return result
        except asyncio.CancelledError:
            session.send(AskEnd(step_id=spec.step_id, reason="cancelled"))
            raise
        finally:
            # Identity check: a stop may already have installed a successor.
            if session.pending_ask is ask:
                session.pending_ask = None
            session.files_spec.pop(payload.parent_id or payload.id, None)
            self.resync_task_indicator()

    def _deliver_parked(self, ask: PendingAsk) -> None:
        """An answer that arrived before its question was (re)asked."""
        for parked in list(self.session.parked_replies):
            if parked.get("stepId") != ask.step_id:
                continue
            self.session.parked_replies.remove(parked)
            if not ask.future.done():
                ask.future.set_result(parked.get("value"))
            return

    async def _answer(self, ask: PendingAsk, value: Any) -> Any:
        """Turn the wire reply into what the asking code expects back."""
        session = self.session
        runner = session.runner
        if isinstance(value, AskTextReply):
            answer = msgspec.to_builtins(value.step)
            # The client echoed the answer without a parent; the form is
            # its parent.
            answer["parentId"] = ask.step.parent_id
            # The client's local echo has no parent either; re-sent so the
            # bubble moves under the form it answered.
            self.send_step(answer)
            if runner is not None:
                await runner.record_user_message(session, value.step)
            return answer
        if isinstance(value, AskFileReply):
            files = [
                session.files[ref.id] for ref in value.files if ref.id in session.files
            ]
            if runner is not None:
                await runner.record_ask_files(session, files, for_id=ask.step.id)
            return files
        if isinstance(value, AskActionReply):
            return msgspec.to_builtins(value.action)
        if isinstance(value, AskElementReply):
            return {"submitted": value.submitted, "props": dict(value.props)}
        return (
            msgspec.to_builtins(value) if isinstance(value, msgspec.Struct) else value
        )

    def end_ask(self, step_id: str, reason: AskEndReason = "cancelled") -> None:
        self.session.send(AskEnd(step_id=step_id, reason=reason))

    # ---------------------------------------------------------- task spinner

    def resync_task_indicator(self) -> None:
        """Say whether anything is running, from the tasks, never a counter."""
        self.session.send(TaskIndicator(running=self.session.has_live_task))

    # ---------------------------------------------------------------- thread

    def first_interaction(self, interaction: str) -> None:
        """Tell the client its thread now exists and what it is called.

        Wire half only. The write half -- naming the row and releasing the
        held writes -- is ``chainlit.persist.open_thread``, which calls this;
        the two are kept apart so a session with no database still gets its
        thread id.
        """
        session = self.session
        session.first_interaction = interaction
        if session.thread_id:
            session.send(
                ThreadFirstInteraction(
                    interaction=interaction, thread_id=session.thread_id
                )
            )

    async def set_chat_profile(
        self,
        name: str,
        *,
        keep_transcript: bool = False,
        transit_message: Any = None,
    ) -> None:
        """Ask the client to start a new session on another profile.

        Whatever ``transit_message`` carries is parked server-side under the
        successor's id and read by the new session's ``on_chat_start`` via
        ``cl.user_session.get("transit_message")``; it never travels through
        the browser. ``None`` revokes what an earlier call parked. The
        current thread's id rides along as the successor's parent once the
        thread exists.
        """
        session = self.session
        owner = _identifier(session.user)
        parent = session.thread_id if session.first_interaction else None

        if self.transit is not None and session.pending_transit_id:
            # Each call mints a fresh id; the record parked under the
            # previous one would otherwise outlive the switch that replaced it.
            await self.transit.discard(session.pending_transit_id)

        next_session_id: Optional[str] = None
        if transit_message is not None or parent is not None:
            next_session_id = str(uuid.uuid4())
            if self.transit is not None:
                await self.transit.park(
                    next_session_id, transit_message, owner, parent=parent
                )
        session.pending_transit_id = next_session_id

        session.send(
            SessionHandoff(
                chat_profile=name,
                next_session_id=next_session_id,
                keep_transcript=keep_transcript,
                has_transit_message=transit_message is not None,
            )
        )

    def profile_changed(
        self, name: str, *, previous: Optional[str] = None, sync: bool = False
    ) -> None:
        self.session.send(
            ProfileChanged(chat_profile=name, previous=previous, sync=sync)
        )

    def open_thread(self, thread_id: str, *, keep_transcript: bool = True) -> None:
        self.session.send(
            ThreadOpen(thread_id=thread_id, keep_transcript=keep_transcript)
        )

    # ------------------------------------------------------------------- misc

    def set_sidebar(
        self,
        *,
        title: Union[str, UnsetType, None] = UNSET,
        elements: Union[Sequence[Mapping[str, Any]], UnsetType] = UNSET,
        key: Union[str, UnsetType, None] = UNSET,
    ) -> None:
        converted: Union[List[Element], UnsetType] = (
            UNSET
            if isinstance(elements, UnsetType)
            else [_as_element(e) for e in elements]
        )
        self.session.send(SidebarSet(title=title, elements=converted, key=key))

    def send_toast(self, message: str, type: ToastType = "info") -> None:
        self.session.send(Toast(message=message, type=type))

    def send_error(
        self, message: str, *, code: str = "app", fatal: bool = False
    ) -> None:
        self.session.send(Error(code=code, message=message, fatal=fatal))


def _identifier(user: Any) -> Optional[str]:
    return getattr(user, "identifier", None) if user is not None else None


def _strict_ask_slot() -> bool:
    from chainlit.config import config

    return bool(getattr(config.features, "strict_ask_slot", False))
