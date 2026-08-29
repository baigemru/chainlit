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
    Sequence,
    Tuple,
    runtime_checkable,
)

from chainlit.protocol.payloads import (
    Action,
    AskSpec,
    Element,
    FileRef,
    Step as StepPayload,
)
from chainlit.ws.outbound import Outbound

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chainlit.protocol.server import ServerMsg
    from chainlit.ws.connection import Connection

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

    async def on_message(
        self,
        session: "Session",
        message: StepPayload,
        file_references: Sequence[FileRef] = (),
    ) -> None:
        """Hand the application a message the user just sent."""
        ...

    async def on_stop(self, session: "Session") -> None:
        """The user asked for the running task to stop."""
        ...

    async def record_user_message(
        self, session: "Session", message: StepPayload
    ) -> Any:
        """Take note of something the user said, without running ``on_message``.

        The answer to a text question is a user message too: it goes into
        the transcript, into persistence, and it is the thread's first
        interaction if nothing preceded it -- but it is delivered to the
        code that asked, not to ``on_message``.
        """
        ...

    async def record_ask_files(
        self, session: "Session", files: Sequence[Mapping[str, Any]], *, for_id: str
    ) -> None:
        """The files answering a file question, spooled and ready to persist."""
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

        self.transcript: List[TranscriptEntry] = []

        self.files: Dict[str, Dict[str, Any]] = {}
        self.files_spec: Dict[str, Any] = {}
        self._files_root = files_root

        #: False while no socket is carrying this session. The registry reads
        #: it: a disconnected session parked on a question is evictable, a
        #: connected one showing the same question is somebody's open tab.
        self.connected = True

        #: The connection that speaks for this session, and the counter it
        #: numbers itself from. One owner, named: everything a handler does
        #: on its way out -- marking the session disconnected, stopping the
        #: writer, running ``on_chat_end`` -- is conditional on still being
        #: this one, and the question used to be answered by comparing
        #: socket objects through the queue, which the takeover had already
        #: cleared. A live session was reaped on that answer.
        self.current: Optional["Connection"] = None
        self.generation = 0

        self.first_interaction: Optional[str] = None
        self.parent_thread_id: Optional[str] = None
        self.pending_transit_id: Optional[str] = None

        #: The writer batching this session's rows, when there is a database.
        #: Owned here rather than looked up by thread: two tabs on one
        #: thread are two writers, and FIFO is a per-writer promise.
        self.writer: Optional[Any] = None

        #: Whether this session's chat has begun. Written and read in one
        #: place -- the arrival, where start-or-resume-or-nothing is decided
        #: -- and kept across reconnects, because the hook it stands for is
        #: a beginning and not a greeting.
        self.chat_started = False

        #: The thread this session resumed, once the resume has happened.
        #: Guards the hooks that fire once per resumed session against the
        #: reconnects that follow.
        self.resumed_thread_id: Optional[str] = None
        #: The thread the client *asked* to resume in its ``hello``, as
        #: opposed to the one this session was minted with. Only an asked-for
        #: thread is looked up, and only its absence is worth reporting: a
        #: fresh session's own id is never in the database yet, and telling
        #: the client "thread not found" about it made every new chat start
        #: with a refusal nobody had asked about.
        self.requested_thread_id: Optional[str] = None

        #: The task waiting out the disconnect grace before the session is
        #: torn down. Held on the session so the one place that decides the
        #: session lives on -- the ``kept`` branch of the handshake -- can
        #: cancel it without knowing who scheduled it.
        self.reaper: Optional["asyncio.Task[Any]"] = None

    # ------------------------------------------------------------ SessionView

    @property
    def has_live_ask(self) -> bool:
        return self.pending_ask is not None and self.pending_ask.is_live

    @property
    def has_live_task(self) -> bool:
        return any(
            task is not None and not task.done()
            for task in (self.current_task, self.thread_ready_task)
        )

    @property
    def is_busy(self) -> bool:
        """Whether the spinner should be lit: working, and not waiting on the user.

        Not ``has_live_task``: a task parked on a question is alive -- it
        keeps the session and protects its steps -- but the one who has to
        act is the user, and a lit spinner locks the composer they would
        act in. The old counter got this right by accident; this gets it
        right by definition.
        """
        return self.has_live_task and not self.has_live_ask

    @property
    def has_parked_reply(self) -> bool:
        return bool(self.parked_replies)

    @property
    def live_ask_step_ids(self) -> Collection[str]:
        if self.pending_ask is None or not self.pending_ask.is_live:
            return ()
        return self.pending_ask.step_ids

    # ---------------------------------------------------------------- sending

    def adopt(self, connection: "Connection") -> Optional["Connection"]:
        """Hand the session to a new connection, and name the one it replaces.

        The single point where "which transport speaks for this session"
        changes. It happens before anything is queued or closed, so that
        every loop still running for the previous connection can tell, at
        its next await, that it is no longer the one to act.
        """
        self.generation += 1
        connection.generation = self.generation
        previous, self.current = self.current, connection
        return previous

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
        for task in (self.current_task, self.thread_ready_task):
            if task is not None and not task.done():
                task.cancel()
        if self.pending_ask is not None:
            self.pending_ask.cancel()

    def discard_files(self) -> None:
        """Remove the spool directory, if this session ever made one."""
        directory = self.files_dir
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
