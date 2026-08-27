import asyncio
import builtins
import time
import uuid
from typing import Any, Dict, List, Literal, Optional, Union, cast, get_args

from socketio.exceptions import TimeoutError

import chainlit.transit as transit
from chainlit.chat_context import chat_context
from chainlit.config import config
from chainlit.data import get_data_layer
from chainlit.element import Element, ElementDict, File
from chainlit.logger import logger
from chainlit.message import Message
from chainlit.mode import Mode
from chainlit.persist_barrier import create_persist_task
from chainlit.session import BaseSession, PendingAsk, WebsocketSession
from chainlit.step import StepDict
from chainlit.types import (
    AskActionResponse,
    AskElementResponse,
    AskFileSpec,
    AskSlotBusyError,
    AskSpec,
    CommandDict,
    FileDict,
    FileReference,
    MessagePayload,
    OutputAudioChunk,
    ThreadDict,
    ToastType,
)
from chainlit.user import PersistedUser
from chainlit.utils import utc_now


async def resync_task_indicator(
    session, emitter, *, emit_end_when_idle: bool = True
) -> None:
    """Re-emit the task indicator from the counter's current truth.

    Level-triggered, for the three moments the client forces its own
    loadingState and an edge emit can no longer be trusted: the reconnect
    handshake, an in-place profile switch, and the way out of an ask. A
    live ask owns the client state instead — the composer is in ask mode —
    so it keeps the indicator dark whatever the counter says.

    A hook launched moments earlier still counts 0 here (create_task has
    not ticked; its with_task wrapper acquires on the first tick and
    edge-emits task_start), so this emits task_end and the wrapper
    corrects it. Self-healing, do not "fix".

    `emit_end_when_idle=False` on the ask-exit path: send_ask_user already
    emitted the raw pause and must not emit a second task_end.

    Takes the emitter explicitly instead of living on it, so each caller
    passes the one bound to its own context — and so tests driving a
    mocked emitter still observe task_start/task_end.
    """
    pending = session.pending_ask
    has_live_ask = pending is not None and pending.is_live
    if session.task_counter > 0 and not has_live_ask:
        await emitter.task_start()
    elif emit_end_when_idle:
        await emitter.task_end()


def _strict_ask_slot(session) -> bool:
    """Whether a busy ask slot must raise instead of returning None.

    Read through the session so a chat profile's config_overrides apply;
    the module-level `config` would ignore them. Compared with `is True`
    on purpose: `Mock(spec=WebsocketSession).get_config()` hands back a
    truthy Mock, which would silently arm strict mode across the suite.
    """
    try:
        cfg = session.get_config()
    except Exception:
        cfg = config
    return getattr(getattr(cfg, "features", None), "strict_ask_slot", False) is True


def _make_legacy_ask_ack(session, future: "asyncio.Future", step_id: str):
    """Ack callback resolving the same future as the ask_reply event.

    Old cached client bundles answer an ask through the socket.io ack; the
    future-based wait accepts whichever path delivers first. The resolved
    step id is recorded on the session — the dedup memory the ask_reply
    handler consults before rescuing an orphaned reply as a plain message.
    """

    def legacy_ack(value=None):
        if not future.done():
            if session is not None:
                session.last_resolved_ask_step_id = step_id
            future.set_result(value)

    return legacy_ack


class BaseChainlitEmitter:
    """
    Chainlit Emitter Stub class. This class is used for testing purposes.
    It stubs the ChainlitEmitter class and does nothing on function calls.
    """

    session: BaseSession
    enabled: bool = True

    def __init__(self, session: BaseSession) -> None:
        """Initialize with the user session."""
        self.session = session

    async def emit(self, event: str, data: Any):
        """Stub method to get the 'emit' property from the session."""
        pass

    async def emit_call(self):
        """Stub method to get the 'emit_call' property from the session."""
        pass

    async def resume_thread(self, thread_dict: ThreadDict):
        """Stub method to resume a thread."""
        pass

    async def send_resume_thread_error(self, error: str):
        """Stub method to send a resume thread error."""
        pass

    async def send_element(self, element_dict: ElementDict):
        """Stub method to send an element to the UI."""
        pass

    async def update_audio_connection(self, state: Literal["on", "off"]):
        """Audio connection signaling."""
        pass

    async def send_audio_chunk(self, chunk: OutputAudioChunk):
        """Stub method to send an audio chunk to the UI."""
        pass

    async def send_audio_interrupt(self):
        """Stub method to interrupt the current audio response."""
        pass

    async def send_step(self, step_dict: StepDict):
        """Stub method to send a message to the UI."""
        pass

    async def update_step(self, step_dict: StepDict):
        """Stub method to update a message in the UI."""
        pass

    async def delete_step(self, step_dict: StepDict):
        """Stub method to delete a message in the UI."""
        pass

    def send_timeout(self, event: Literal["ask_timeout", "call_fn_timeout"]):
        """Stub method to send a timeout to the UI."""
        pass

    def clear(self, event: Literal["clear_ask", "clear_call_fn"]):
        pass

    async def init_thread(self, interaction: str):
        pass

    async def process_message(self, payload: MessagePayload) -> Message:
        """Stub method to process user message."""
        return Message(content="")

    async def send_ask_user(
        self,
        step_dict: StepDict,
        spec: AskSpec,
        raise_on_timeout=False,
        *,
        restore_actions: Optional[List[Dict[str, Any]]] = None,
        restore_element: Optional[Any] = None,
    ) -> Optional[
        Union["StepDict", "AskActionResponse", "AskElementResponse", List["FileDict"]]
    ]:
        """Stub method to send a prompt to the UI and wait for a response."""
        pass

    async def send_call_fn(
        self, name: str, args: Dict[str, Any], timeout=300, raise_on_timeout=False
    ) -> Optional[Dict[str, Any]]:
        """Stub method to send a call function event to the copilot and wait for a response."""
        pass

    async def update_token_count(self, count: int):
        """Stub method to update the token count for the UI."""
        pass

    async def task_start(self):
        """Stub method to send a task start signal to the UI."""
        pass

    async def task_end(self):
        """Stub method to send a task end signal to the UI."""
        pass

    async def task_acquire(self):
        """Stub method to register an owner of the task indicator."""
        pass

    async def task_release(self):
        """Stub method to release an owner of the task indicator."""
        pass

    async def stream_start(self, step_dict: StepDict):
        """Stub method to send a stream start signal to the UI."""
        pass

    async def send_token(self, id: str, token: str, is_sequence=False, is_input=False):
        """Stub method to send a message token to the UI."""
        pass

    async def set_chat_settings(self, settings: dict):
        """Stub method to set chat settings."""
        pass

    async def set_commands(self, commands: List[CommandDict]):
        """Stub method to send the available commands to the UI."""
        pass

    async def set_modes(self, modes: List[Mode]):
        """Stub method to send the available modes to the UI."""
        pass

    async def set_chat_profile(
        self,
        name: str,
        *,
        keep_transcript: bool = False,
        transit_message: Any = None,
    ) -> None:
        """Stub method to switch the chat profile in the UI."""
        pass

    async def open_thread(
        self,
        thread_id: str,
        *,
        keep_transcript: bool = True,
    ) -> None:
        """Stub method to open an existing thread in the UI."""
        pass

    async def send_window_message(self, data: Any):
        """Stub method to send custom data to the host window."""
        pass

    async def send_toast(self, message: str, type: Optional[ToastType] = "info"):
        """Stub method to send a toast message to the UI."""
        pass

    async def set_favorites(self, steps: List[StepDict]):
        """Stub method to send the favorite messages to the UI."""
        pass


class ChainlitEmitter(BaseChainlitEmitter):
    """
    Chainlit Emitter class. The Emitter is not directly exposed to the developer.
    Instead, the developer interacts with the Emitter through the methods and classes exposed in the __init__ file.
    """

    session: WebsocketSession

    def __init__(self, session: WebsocketSession) -> None:
        """Initialize with the user session."""
        self.session = session

    def _get_session_property(self, property_name: str, raise_error=True):
        """Helper method to get a property from the session."""
        if not hasattr(self, "session") or not hasattr(self.session, property_name):
            if raise_error:
                raise ValueError(f"Session does not have property '{property_name}'")
            else:
                return None
        return getattr(self.session, property_name)

    @property
    def emit(self):
        """Get the 'emit' property from the session."""

        return self._get_session_property("emit")

    @property
    def emit_call(self):
        """Get the 'emit_call' property from the session."""
        return self._get_session_property("emit_call")

    def resume_thread(self, thread_dict: ThreadDict):
        """Send a thread to the UI to resume it"""
        return self.emit("resume_thread", thread_dict)

    def send_resume_thread_error(self, error: str):
        """Send a thread resume error to the UI"""
        return self.emit("resume_thread_error", error)

    async def update_audio_connection(self, state: Literal["on", "off"]):
        """Audio connection signaling."""
        await self.emit("audio_connection", state)

    async def send_audio_chunk(self, chunk: OutputAudioChunk):
        """Send an audio chunk to the UI."""
        await self.emit("audio_chunk", chunk)

    async def send_audio_interrupt(self):
        """Method to interrupt the current audio response."""
        await self.emit("audio_interrupt", {})

    async def send_element(self, element_dict: ElementDict):
        """Stub method to send an element to the UI."""
        await self.emit("element", element_dict)

    def send_step(self, step_dict: StepDict):
        """Send a message to the UI."""
        return self.emit("new_message", step_dict)

    def update_step(self, step_dict: StepDict):
        """Update a message in the UI."""
        return self.emit("update_message", step_dict)

    def delete_step(self, step_dict: StepDict):
        """Delete a message in the UI."""
        return self.emit("delete_message", step_dict)

    def send_timeout(self, event: Literal["ask_timeout", "call_fn_timeout"]):
        return self.emit(event, {})

    def clear(self, event: Literal["clear_ask", "clear_call_fn"]):
        return self.emit(event, {})

    async def flush_thread_queues(self, interaction: str):
        if data_layer := get_data_layer():
            if isinstance(self.session.user, PersistedUser):
                user_id = self.session.user.id
            else:
                user_id = None
            try:
                should_tag_thread = (
                    self.session.chat_profile and config.features.auto_tag_thread
                )
                tags = [self.session.chat_profile] if should_tag_thread else None
                kwargs: Dict[str, Any] = {
                    "thread_id": self.session.thread_id,
                    "name": interaction,
                    "user_id": user_id,
                    "tags": tags,
                }
                if self.session.parent_thread_id is not None:
                    kwargs["parent_thread_id"] = self.session.parent_thread_id
                try:
                    await data_layer.update_thread(**kwargs)
                except TypeError:
                    if "parent_thread_id" not in kwargs:
                        raise
                    # Data layers predating parent_thread_id must keep
                    # working; they just don't record the link.
                    logger.warning(
                        "Data layer does not accept parent_thread_id; "
                        "creating the thread without it."
                    )
                    del kwargs["parent_thread_id"]
                    await data_layer.update_thread(**kwargs)
            except Exception as e:
                logger.error(f"Error updating thread: {e}")
            create_persist_task(
                self.session.flush_method_queue(),
                thread_id=self.session.thread_id,
            )

    async def init_thread(self, interaction: str):
        await self.flush_thread_queues(interaction)
        await self.emit(
            "first_interaction",
            {
                "interaction": interaction,
                "thread_id": self.session.thread_id,
            },
        )

    async def process_message(self, payload: MessagePayload):
        step_dict = payload["message"]
        file_refs = payload.get("fileReferences")
        # UUID generated by the frontend should use v4
        assert uuid.UUID(step_dict["id"]).version == 4

        message = Message.from_dict(step_dict)
        # Overwrite the created_at timestamp with the current time
        message.created_at = utc_now()
        chat_context.add(message)

        # Tracked as persistence work: the inner data-layer task only
        # registers once this wrapper runs — tracking the wrapper itself
        # closes the window where wait_for_persist would see nothing pending.
        create_persist_task(message._create(), thread_id=self.session.thread_id)

        if not self.session.has_first_interaction:
            self.session.has_first_interaction = True
            # init_thread awaits update_thread (real I/O) BEFORE scheduling
            # the tracked queue flush; tracking it closes that window too.
            create_persist_task(
                self.init_thread(message.content),
                thread_id=self.session.thread_id,
            )

        if file_refs:
            files = [
                self.session.files[file["id"]]
                for file in file_refs
                if file["id"] in self.session.files
            ]

            elements = [
                Element.from_dict(
                    {
                        "id": file["id"],
                        "name": file["name"],
                        "path": str(file["path"]),
                        "chainlitKey": file["id"],
                        "display": "inline",
                        "type": Element.infer_type_from_mime(file["type"]),
                        "mime": file["type"],
                    }
                )
                for file in files
            ]

            message.elements = elements

            async def send_elements():
                for element in message.elements:
                    await element.send(for_id=message.id)

            # element.send() persists each element; track the wrapper so
            # thread reads wait for the attachments too.
            create_persist_task(send_elements(), thread_id=self.session.thread_id)

        return message

    async def send_ask_user(
        self,
        step_dict: StepDict,
        spec: AskSpec,
        raise_on_timeout=False,
        *,
        restore_actions: Optional[List[Dict[str, Any]]] = None,
        restore_element: Optional[Any] = None,
    ):
        """Send a prompt to the UI and wait for a response."""
        parent_id = str(step_dict["parentId"])
        session = self.session

        existing = session.pending_ask
        if existing is not None and not existing.future.done():
            # A concurrent ask would silently replace the previous form in
            # the UI and orphan its waiting coroutine — refuse instead. A
            # slot whose future is already resolved/cancelled only awaits
            # its owner's cleanup and does not block a new ask.
            if _strict_ask_slot(session):
                logger.error(
                    "send_ask_user: an ask is already pending for session %s; "
                    "raising AskSlotBusyError",
                    session.id,
                )
                raise AskSlotBusyError(str(existing.spec.step_id))
            logger.error(
                "send_ask_user: an ask is already pending for session %s; "
                "returning None",
                session.id,
            )
            return None

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        pending_ask = PendingAsk(
            step_dict=cast(Dict[str, Any], step_dict),
            spec=spec,
            future=future,
            deadline=time.monotonic() + spec.timeout,
            restore_actions=restore_actions or [],
            restore_element=restore_element,
        )
        session.pending_ask = pending_ask
        try:
            if spec.type == "file":
                self.session.files_spec[parent_id] = cast(AskFileSpec, spec)

            # Send the prompt to the UI. A plain emit, not a socket.io call:
            # the reply comes back through the "ask_reply" event and resolves
            # the future, which survives socket reconnections (the sid a
            # sio.call is bound to does not). A socket.io ack is still
            # attached when possible so clients running a stale cached
            # bundle (which answer through the ack) keep working.
            ask_payload = {"msg": step_dict, "spec": spec.to_dict()}
            emit_ask = getattr(session, "emit_ask", None)
            if emit_ask is not None:
                await emit_ask(
                    ask_payload, _make_legacy_ask_ack(session, future, spec.step_id)
                )
            else:
                await self.emit("ask", ask_payload)

            # Pause the indicator for the wait so the User can answer the
            # prompt. A raw emit, NOT a counter release: the ask must never
            # mutate the owner counter. Releasing here and re-acquiring in
            # the finally would poison it — a foreign owner exiting during
            # the wait would have its release eaten by the zero clamp, and
            # the paired re-acquire would then add back a unit no live task
            # ever releases (a forever-burning indicator). The counter is
            # mutated only by real acquire/release pairs of live tasks; the
            # ask reads it and re-syncs the client on exit.
            if session.task_counter > 0:
                await self.task_end()

            user_res: Optional[
                Union[
                    StepDict,
                    AskActionResponse,
                    AskElementResponse,
                    List[FileReference],
                ]
            ]
            try:
                user_res = await asyncio.wait_for(future, spec.timeout)
            # builtins-qualified on purpose: this module imports socketio's
            # own TimeoutError, which is not a subclass of the builtin one
            # that asyncio.wait_for raises.
            except builtins.TimeoutError:
                # On py3.12+ wait_for can time out in the same tick the
                # future was resolved — prefer the answer over the timeout.
                if future.done() and not future.cancelled():
                    user_res = future.result()
                else:
                    # Callers (and raise_on_timeout) expect the historical
                    # socketio.exceptions.TimeoutError, not the asyncio one.
                    raise TimeoutError from None

            final_res: Optional[
                Union[StepDict, AskActionResponse, AskElementResponse, List[FileDict]]
            ] = None

            if user_res:
                interaction: Union[str, None] = None
                if spec.type == "text":
                    message_dict_res = cast(StepDict, user_res)
                    await self.process_message(
                        {"message": message_dict_res, "fileReferences": None}
                    )
                    interaction = message_dict_res["output"]
                    final_res = message_dict_res
                elif spec.type == "file":
                    file_refs = cast(List[FileReference], user_res)
                    files = [
                        self.session.files[file["id"]]
                        for file in file_refs
                        if file["id"] in self.session.files
                    ]
                    final_res = files
                    interaction = ",".join([file["name"] for file in files])
                    if get_data_layer():
                        coros = [
                            File(
                                id=file["id"],
                                name=file["name"],
                                path=str(file["path"]),
                                mime=file["type"],
                                chainlit_key=file["id"],
                                for_id=step_dict["id"],
                            )._create()
                            for file in files
                        ]
                        await asyncio.gather(*coros)
                elif spec.type == "action":
                    action_res = cast(AskActionResponse, user_res)
                    final_res = action_res
                    interaction = action_res["name"]
                elif spec.type == "element":
                    final_res = cast(AskElementResponse, user_res)
                    interaction = "custom_element"

                if not self.session.has_first_interaction and interaction:
                    self.session.has_first_interaction = True
                    await self.init_thread(interaction=interaction)

            await self.clear("clear_ask")
            return final_res
        except TimeoutError as e:
            await self.send_timeout("ask_timeout")

            if raise_on_timeout:
                raise e
        finally:
            # Identity check: after a cancel (session.delete/stop) another
            # ask may already occupy the slot — never clobber it.
            if session.pending_ask is pending_ask:
                session.pending_ask = None
            if parent_id in self.session.files_spec:
                del self.session.files_spec[parent_id]
            # Level-triggered exit resync: the client forced loading=false
            # on receiving 'ask'/'ask_timeout', so an edge emit cannot be
            # trusted — re-emit from the counter's current truth. Owners
            # alive right now (the blocked caller included — it cannot have
            # released while waiting on this ask) relight the indicator;
            # none alive leaves it dark, so an ownerless ask no longer
            # burns forever after the answer or timeout. A live successor
            # ask (stop freed the slot, on_stop installed a new one) owns
            # the client state instead — same guard as the reconnect
            # resync.
            await resync_task_indicator(session, self, emit_end_when_idle=False)

    async def send_call_fn(
        self, name: str, args: Dict[str, Any], timeout=300, raise_on_timeout=False
    ) -> Optional[Dict[str, Any]]:
        """Stub method to send a call function event to the copilot and wait for a response."""
        try:
            call_fn_res = await self.emit_call(
                "call_fn", {"name": name, "args": args}, timeout
            )  # type: Dict

            await self.clear("clear_call_fn")
            return call_fn_res
        except TimeoutError as e:
            await self.send_timeout("call_fn_timeout")

            if raise_on_timeout:
                raise e
            return None

    def update_token_count(self, count: int):
        """Update the token count for the UI."""

        return self.emit("token_usage", count)

    def task_start(self):
        """
        Send a task start signal to the UI.

        Raw emit. Task-owning code paths must go through
        task_acquire/task_release instead; the raw emits are reserved for
        the handshake (the early task_end in connection_successful) and the
        level-triggered resyncs — reconnect and the ask-exit path in
        send_ask_user — where the client is known to have forced its
        loadingState on its own.
        """
        return self.emit("task_start", {})

    def task_end(self):
        """Send a task end signal to the UI. Raw emit — see task_start."""
        return self.emit("task_end", {})

    async def task_acquire(self):
        """Register an owner of the task indicator.

        The indicator on the client is a single boolean; the per-session
        counter makes it survive overlapping owners (a short
        process_message must not darken it from under a live
        on_thread_ready hook). Edge-triggered: only the 0→1 transition
        emits task_start.
        """
        self.session.task_counter += 1
        if self.session.task_counter == 1:
            await self.task_start()

    async def task_release(self):
        """Release an owner of the task indicator.

        Edge-triggered: only the 1→0 transition emits task_end. A release
        at zero is a silent no-op (clamped) — e.g. an app's manual
        compensating task_end sent before its callbacks migrated to the
        counter.
        """
        if self.session.task_counter <= 0:
            self.session.task_counter = 0
            return
        self.session.task_counter -= 1
        if self.session.task_counter == 0:
            await self.task_end()

    def stream_start(self, step_dict: StepDict):
        """Send a stream start signal to the UI."""
        return self.emit(
            "stream_start",
            step_dict,
        )

    def send_token(self, id: str, token: str, is_sequence=False, is_input=False):
        """Send a message token to the UI."""
        return self.emit(
            "stream_token",
            {"id": id, "token": token, "isSequence": is_sequence, "isInput": is_input},
        )

    def set_chat_settings(self, settings: Dict[str, Any]):
        self.session.chat_settings = settings

    def set_commands(self, commands: List[CommandDict]):
        """Send the available commands to the UI."""
        return self.emit(
            "set_commands",
            commands,
        )

    def set_modes(self, modes: List[Mode]):
        """Send the available modes to the UI."""
        return self.emit(
            "set_modes",
            [mode.to_dict() for mode in modes],
        )

    async def set_chat_profile(
        self,
        name: str,
        *,
        keep_transcript: bool = False,
        transit_message: Any = None,
    ) -> None:
        """Ask the UI to switch the chat profile.

        Both modes start a brand-new session and thread on `name`, so the new
        profile's `on_chat_start` runs and the new thread records that profile.
        They differ only in what stays on screen.

        Args:
            name: Profile name as declared in `@cl.set_chat_profiles`.
            keep_transcript: Keep the messages already on screen and mark the
                switch with a divider, instead of clearing them (default).
            transit_message: Optional value handed to the new session — it
                never travels through the browser. Read it in the new
                profile's `on_chat_start` via
                `cl.user_session.get("transit_message")`; any object works,
                not just text. Passing `None` (the default) drops a value
                parked by an earlier call on this session — after the first
                interaction a record carrying only the parent-thread link
                still remains.

        Picking a transit message up creates and names the new thread right
        away (the value itself when it is a non-empty string, the profile
        name otherwise) — an `on_chat_start` that then renders nothing leaves
        a named empty thread in the history. There is no auto-answer into
        `AskUserMessage` anymore: read the transit value instead of asking.
        If the switch never happens (dead socket, unknown profile name,
        copilot — which does not support `set_chat_profile` at all), the
        record is dropped by the next call on this session, or expires after
        `transit.TRANSIT_TTL_SECONDS`.

        The transcript kept by `keep_transcript` is client-side only: it
        belongs to the previous thread, is dropped on reload or when a thread
        is opened from the history, cannot be edited, and loses elements that
        were served by the previous session.

        When the current thread already exists (the session had its first
        interaction), its id rides along to the new session and is recorded
        as the new thread's `parentThreadId` by data layers that support it.

        Call this at most once per handler. Every call revokes the record
        the previous one parked and mints a new successor id, while the
        frontend tears the chat down per switch event — so with two calls
        only the last delivered event's session id is the one that connects,
        and an earlier call's message and parent link are gone.
        """
        owner = self.session.user.identifier if self.session.user else None
        # Only a thread that exists can be a parent: the row is created on
        # first interaction.
        parent = self.session.thread_id if self.session.has_first_interaction else None

        # Each call mints a fresh key, so the one this session parked before
        # has to go: otherwise records accumulate instead of overwriting the
        # session's single slot, which would both defeat the
        # MAX_TRANSIT_RECORDS backstop and turn "transit_message=None
        # revokes" into "the revoked value is still delivered".
        previous = getattr(self.session, "pending_transit_id", None)
        if previous:
            transit.discard(previous)

        if transit_message is None and parent is None:
            # Nothing to hand over: the revoke above is the whole effect.
            self.session.pending_transit_id = None
            next_session_id = None
        else:
            next_session_id = str(uuid.uuid4())
            transit.store(next_session_id, transit_message, owner, parent=parent)
            self.session.pending_transit_id = next_session_id

        await self.emit(
            "set_chat_profile",
            {
                "name": name,
                "keepTranscript": keep_transcript,
                "hasTransitMessage": transit_message is not None,
                # The id the record is parked under. The browser adopts it
                # verbatim instead of minting its own and handing it back —
                # that round trip used to ride a socket the very next line
                # closes, and losing it stranded the hand-off.
                "nextSessionId": next_session_id,
            },
        )

    async def open_thread(
        self,
        thread_id: str,
        *,
        keep_transcript: bool = True,
    ) -> None:
        """Ask the UI to open an existing thread of the current user.

        Unlike `set_chat_profile` this creates nothing: the UI navigates to
        the thread exactly as if the user picked it from the history list —
        the same resume path, with the same access checks, so a thread the
        user cannot read does not open. With `keep_transcript` the messages
        currently on screen stay above a divider; steps of the opened thread
        that are already on screen are not rendered twice.

        Args:
            thread_id: Id of an existing thread of the current user — e.g.
                `cl.context.session.parent_thread_id` to return to the
                thread a profile switch came from.
            keep_transcript: Keep the messages already on screen and mark
                the transition with a divider (default), instead of clearing
                them. The kept transcript has the same limits as with
                `set_chat_profile`: client-side only, dropped on reload.
        """
        await self.emit(
            "open_thread",
            {"threadId": thread_id, "keepTranscript": keep_transcript},
        )

    def set_favorites(self, steps: List[StepDict]):
        """Send the favorite messages to the UI."""
        return self.emit(
            "set_favorites",
            steps,
        )

    def send_window_message(self, data: Any):
        """Send custom data to the host window."""
        return self.emit("window_message", data)

    async def send_toast(self, message: str, type: Optional[ToastType] = "info"):
        """Send a toast message to the UI."""
        # check that the type is valid using ToastType
        if type not in get_args(ToastType):
            raise ValueError(f"Invalid toast type: {type}")
        await self.emit("toast", {"message": message, "type": type})
