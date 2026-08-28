"""One live websocket session.

The session is what survives a socket. A browser that reloads, loses its
network for a minute or opens a second tab is still the same conversation,
and everything that has to outlive the connection lives here: the pending
question, the running task, the transcript the client is shown again on
reconnect, the files it has uploaded, and the dict the application keeps
its own state in.

Deliberately transport-side only. Nothing here imports ``chainlit.context``,
``chainlit.emitter``, ``chainlit.message`` or ``chainlit.step``: this module
knows how to hold a conversation's state and how to put frames on a wire,
and knows nothing about the decorators an application registers. The bridge
between the two is injected -- ``CallbackRunner`` below -- so that this can
be built, tested and reasoned about without an application at all, which is
what the scenario table needs in order to drive it.

The three consumers it has to satisfy at once, all specified elsewhere:

* ``ws.registry.SessionView`` -- five read-only members the eviction policy
  reads. ``live_ask_step_ids`` yields **both** ids a pending ask carries;
  they can differ, and protecting only one of them lets the other's step be
  deleted out from under a question that is still on screen.
* the ``LiveSession`` protocols in ``controllers.project`` and
  ``controllers.files`` -- ``user``, ``call_action`` and the file spool.
* ``Outbound`` -- the session owns the queue; the connection owns the writer.
"""

from __future__ import annotations

import asyncio
import mimetypes
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Collection,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)

from chainlit.protocol.payloads import Action, AskSpec, Element, Step as StepPayload
from chainlit.ws.outbound import Outbound

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chainlit.protocol.server import ServerMsg

__all__ = [
    "CallbackRunner",
    "PendingAsk",
    "Session",
    "TranscriptEntry",
]


@runtime_checkable
class CallbackRunner(Protocol):
    """The application, as a session sees it.

    Every entry point the transport can trigger, and nothing else. The real
    implementation initialises the context variable the ``cl.*`` API reads
    and lands with the application bridge; the scenario table injects a
    recorder, which is the whole reason this is a port rather than an import.
    """

    async def call_action(self, session: "Session", action: Mapping[str, Any]) -> Any:
        """Run the callback registered for ``action["name"]``.

        Raises:
            LookupError: no callback is registered under that name.
        """
        ...

    async def on_message(self, session: "Session", message: StepPayload) -> None:
        """Hand the application a message the user just sent."""
        ...

    async def on_stop(self, session: "Session") -> None:
        """The user asked for the running task to stop."""
        ...


@dataclass
class PendingAsk:
    """A question on screen, and the coroutine waiting for its answer.

    ``deadline`` is absolute ``time.monotonic()``, fixed when the ask was
    first sent. A reconnect re-sends the question with whatever is left of
    it and never extends it -- a form that resets its own timer every time
    the network hiccups is a form that never times out.

    ``restore_actions`` and ``restore_element`` are the form's furniture,
    snapshotted rather than referenced: the client loses them on a reload,
    and re-serialising the live objects at restore time would roll back any
    change made to them while the question was waiting.
    """

    step_id: str
    step: StepPayload
    spec: AskSpec
    future: "asyncio.Future[Any]"
    deadline: float
    restore_actions: List[Action] = field(default_factory=list)
    restore_element: Optional[Element] = None

    @property
    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    @property
    def expired(self) -> bool:
        return self.remaining <= 0

    @property
    def is_live(self) -> bool:
        """Still answerable: the deadline has not passed and no reply landed."""
        return not self.expired and not self.future.done()

    @property
    def step_ids(self) -> Tuple[str, ...]:
        """Every step this question is displaying.

        Two ids, because they can differ: the spec addresses the form, the
        step dict is the bubble it is drawn in. Resume-delete has to protect
        both or it takes half a live question away.
        """
        if self.step.id and self.step.id != self.step_id:
            return (self.step_id, self.step.id)
        return (self.step_id,)

    def cancel(self) -> None:
        if not self.future.done():
            self.future.cancel()


@dataclass
class TranscriptEntry:
    """One step the session is holding, with the attachments sent alongside it.

    The transcript is what a reconnecting client is shown before anything
    else. It is kept in send order, because that is the order it has to be
    replayed in.
    """

    step: StepPayload
    elements: List[Element] = field(default_factory=list)


class Session:
    """A conversation, independent of the socket currently carrying it."""

    def __init__(
        self,
        *,
        id: str,
        outbound: Optional[Outbound] = None,
        runner: Optional[CallbackRunner] = None,
        user: Optional[Any] = None,
        thread_id: Optional[str] = None,
        chat_profile: Optional[str] = None,
        client_type: str = "webapp",
        user_env: Optional[Dict[str, str]] = None,
        files_root: Optional[Path] = None,
    ) -> None:
        self.id = id
        self.outbound = outbound if outbound is not None else Outbound(name=id)
        self.runner = runner
        self.user = user
        self.thread_id = thread_id
        self.chat_profile = chat_profile
        self.client_type = client_type
        self.user_env: Dict[str, str] = dict(user_env or {})

        #: The application's own state, persisted into thread metadata so it
        #: survives a resume. One key is deliberately excluded there -- see
        #: ``transit`` -- and that exclusion belongs to whoever writes it,
        #: not here.
        self.state: Dict[str, Any] = {}

        self.pending_ask: Optional[PendingAsk] = None

        #: Replies that arrived before the handshake finished restoring the
        #: question they answer. They are the only copy of something a user
        #: typed, so they are held rather than dropped -- and holding one is
        #: enough on its own to keep the session alive through a reconnect.
        self.parked_replies: List[Mapping[str, Any]] = []

        self.current_task: Optional["asyncio.Task[Any]"] = None
        self.thread_ready_task: Optional["asyncio.Task[Any]"] = None
        self.profile_start_task: Optional["asyncio.Task[Any]"] = None

        self.transcript: List[TranscriptEntry] = []

        self.files: Dict[str, Dict[str, Any]] = {}
        self.files_spec: Dict[str, Any] = {}
        self._files_root = files_root

        #: False while no socket is carrying this session. The registry reads
        #: it: a disconnected session parked on a question is evictable, a
        #: connected one showing the same question is somebody's open tab.
        self.connected = True

        #: Sequence number of the last heartbeat the client acknowledged.
        #: Zero means it has not answered one yet, which is also the state a
        #: session is in for its first interval -- so the probe compares
        #: against the sequence it sent, never against zero.
        self.last_ack: int = 0

        self.first_interaction: Optional[str] = None
        self.parent_thread_id: Optional[str] = None
        self.profile_switch_lock = asyncio.Lock()
        self.pending_transit_id: Optional[str] = None

    # ------------------------------------------------------------ SessionView

    @property
    def has_live_ask(self) -> bool:
        return self.pending_ask is not None and self.pending_ask.is_live

    @property
    def has_live_task(self) -> bool:
        return any(
            task is not None and not task.done()
            for task in (
                self.current_task,
                self.thread_ready_task,
                self.profile_start_task,
            )
        )

    @property
    def has_parked_reply(self) -> bool:
        return bool(self.parked_replies)

    @property
    def live_ask_step_ids(self) -> Collection[str]:
        if self.pending_ask is None or not self.pending_ask.is_live:
            return ()
        return self.pending_ask.step_ids

    # ---------------------------------------------------------------- sending

    def send(self, msg: "ServerMsg") -> bool:
        """Queue one frame for this session's client."""
        return self.outbound.send(msg)

    # --------------------------------------------------------------- the app

    async def call_action(self, action: Mapping[str, Any]) -> Any:
        """Run the application's callback for ``action``.

        Raises:
            LookupError: the application registered no callback under that
                name, which the HTTP route turns into a 404.
        """
        if self.runner is None:
            raise LookupError(action.get("name", ""))
        return await self.runner.call_action(self, action)

    # ----------------------------------------------------------------- files

    @property
    def files_dir(self) -> Path:
        """This session's spool directory. Not created until something lands."""
        if self._files_root is not None:
            return self._files_root / self.id
        from chainlit.config import FILES_DIRECTORY

        return Path(FILES_DIRECTORY) / self.id

    async def persist_file(
        self,
        name: str,
        mime: str,
        *,
        path: Optional[str] = None,
        content: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Spool one file and return the reference the client uploads against.

        Only the id goes back. The path is the server's business, and the
        old shape leaked it to anyone who could upload.
        """
        if path is None and content is None:
            raise ValueError("Either path or content must be provided")

        self.files_dir.mkdir(parents=True, exist_ok=True)

        file_id = str(uuid.uuid4())
        file_path = self.files_dir / file_id
        if extension := mimetypes.guess_extension(mime):
            file_path = file_path.with_suffix(extension)

        if path is not None:
            await asyncio.to_thread(shutil.copyfile, path, file_path)
        else:
            assert content is not None
            await asyncio.to_thread(file_path.write_bytes, content)

        self.files[file_id] = {
            "id": file_id,
            "path": file_path,
            "name": name,
            "type": mime,
            "size": file_path.stat().st_size,
        }
        return {"id": file_id}

    # ----------------------------------------------------------- termination

    def cancel_work(self) -> None:
        """Stop everything this session is running, without waiting for it.

        Synchronous on purpose: the callers are the eviction sweep and the
        close path, and both need the session to stop *now* -- an await
        between deciding to evict and the eviction taking effect is a window
        another connection can arrive in.
        """
        for task in (
            self.current_task,
            self.thread_ready_task,
            self.profile_start_task,
        ):
            if task is not None and not task.done():
                task.cancel()
        if self.pending_ask is not None:
            self.pending_ask.cancel()

    def discard_files(self) -> None:
        """Remove the spool directory, if this session ever made one."""
        directory = self.files_dir
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
