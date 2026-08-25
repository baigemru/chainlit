import asyncio
import json
import mimetypes
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Deque,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    Union,
)

import aiofiles

from chainlit.logger import logger
from chainlit.types import AskFileSpec, AskSpec, FileReference

if TYPE_CHECKING:
    from mcp import ClientSession

    from chainlit.config import ChainlitConfig
    from chainlit.types import FileDict
    from chainlit.user import PersistedUser, User

_CLOSE_TIMEOUT = 10.0  # seconds to wait for a background MCP task to finish


@dataclass
class McpSession:
    """Lifecycle wrapper for a single MCP connection.

    Each MCP connection is run inside its own ``asyncio.Task``.  That task
    creates the ``AsyncExitStack``, enters all context managers (transport,
    ``ClientSession``), calls ``initialize()``, and then blocks on
    ``stop_event.wait()``.  When the event is set the task wakes up and
    closes the exit stack **in the same task** that opened it, avoiding
    the cross-task cancel-scope corruption described in
    https://github.com/Chainlit/chainlit/issues/2182.

    Original solution by @nigiva:
    https://github.com/Chainlit/chainlit/issues/2182#issuecomment-2840283194
    """

    name: str
    client: "ClientSession"
    task: asyncio.Task
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def close(self) -> None:
        """Signal the background task to shut down and wait for it."""
        self.stop_event.set()
        try:
            await asyncio.wait_for(self.task, timeout=_CLOSE_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(
                "MCP session %r did not shut down within %.1fs — cancelling",
                self.name,
                _CLOSE_TIMEOUT,
            )
            self.task.cancel()
            try:
                await self.task
            except BaseException:
                pass
        except asyncio.CancelledError:
            pass
        except BaseException:
            logger.debug("Error while closing MCP session %r", self.name, exc_info=True)

    # Backward-compatible tuple unpacking.
    # The original Chainlit format is ``(ClientSession, AsyncExitStack)``.
    # Code that does ``client, _ = mcp_sessions[name]`` will get the
    # ``ClientSession`` and a safe sentinel (not the real exit stack,
    # which must only be closed by the owning background task).
    def __iter__(self):
        return iter((self.client, self))


ClientType = Literal["webapp", "copilot", "teams", "slack", "discord"]


class JSONEncoderIgnoreNonSerializable(json.JSONEncoder):
    def default(self, o):
        try:
            return super().default(o)
        except TypeError:
            return None


def clean_metadata(metadata: Dict, max_size: int = 1048576):
    cleaned_metadata = json.loads(
        json.dumps(metadata, cls=JSONEncoderIgnoreNonSerializable, ensure_ascii=False)
    )

    metadata_size = len(json.dumps(cleaned_metadata).encode("utf-8"))
    if metadata_size > max_size:
        # Redact the metadata if it exceeds the maximum size
        cleaned_metadata = {
            "message": f"Metadata size exceeds the limit of {max_size} bytes. Redacted."
        }

    return cleaned_metadata


class BaseSession:
    """Base object."""

    thread_id_to_resume: Optional[str] = None
    client_type: ClientType
    current_task: Optional[asyncio.Task] = None
    chat_started: bool = False

    def __init__(
        self,
        # Id of the session
        id: str,
        client_type: ClientType,
        # Thread id
        thread_id: Optional[str],
        # Logged-in user information
        user: Optional[Union["User", "PersistedUser"]],
        # Logged-in user token
        token: Optional[str],
        # User specific environment variables. Empty if no user environment variables are required.
        user_env: Optional[Dict[str, str]],
        # WSGI environment variables for the connection request
        environ: Optional[dict[str, Any]] = None,
        # Chat profile selected before the session was created
        chat_profile: Optional[str] = None,
    ):
        if thread_id:
            self.thread_id_to_resume = thread_id
        self.thread_id = thread_id or str(uuid.uuid4())
        # Thread the user came from when this session was opened by a profile
        # switch; recorded as the new thread's parentThreadId on creation.
        self.parent_thread_id: Optional[str] = None
        self.user = user
        self.client_type = client_type
        self.token = token
        self.has_first_interaction = False
        self.chat_started = False
        self.user_env = user_env or {}
        self.environ = environ or {}
        self.chat_profile = chat_profile

        self.files: Dict[str, FileDict] = {}
        self.files_spec: Dict[str, AskFileSpec] = {}

        self.id = id

        self.chat_settings: Dict[str, Any] = {}

    @property
    def files_dir(self):
        from chainlit.config import FILES_DIRECTORY

        return FILES_DIRECTORY / self.id

    async def persist_file(
        self,
        name: str,
        mime: str,
        path: Optional[str] = None,
        content: Optional[Union[bytes, str]] = None,
    ) -> FileReference:
        if not path and not content:
            raise ValueError(
                "Either path or content must be provided to persist a file"
            )

        self.files_dir.mkdir(exist_ok=True)

        file_id = str(uuid.uuid4())

        file_path = self.files_dir / file_id

        file_extension = mimetypes.guess_extension(mime)

        if file_extension:
            file_path = file_path.with_suffix(file_extension)

        if path:
            # Copy the file from the given path
            async with (
                aiofiles.open(path, "rb") as src,
                aiofiles.open(file_path, "wb") as dst,
            ):
                await dst.write(await src.read())
        elif content:
            # Write the provided content to the file
            async with aiofiles.open(file_path, "wb") as buffer:
                if isinstance(content, str):
                    content = content.encode("utf-8")
                await buffer.write(content)

        # Get the file size
        file_size = file_path.stat().st_size
        # Store the file content in memory
        self.files[file_id] = {
            "id": file_id,
            "path": file_path,
            "name": name,
            "type": mime,
            "size": file_size,
        }

        return {"id": file_id}

    def to_persistable(self) -> Dict:
        from chainlit.config import config
        from chainlit.user_session import user_sessions

        user_session = user_sessions.get(self.id) or {}  # type: Dict
        user_session["chat_settings"] = self.chat_settings
        user_session["chat_profile"] = self.chat_profile
        user_session["client_type"] = self.client_type

        # Check config setting for whether to persist user environment variables
        user_session_copy = user_session.copy()
        if not config.project.persist_user_env:
            # Remove user environment variables (API keys) before persisting to database
            user_session_copy["env"] = {}

        # The transit message is a hand-off between two live sessions, not
        # thread state: persisted, it would resurrect on every resume of this
        # thread through the metadata copy in `resume_thread`.
        user_session_copy.pop("transit_message", None)

        metadata = clean_metadata(user_session_copy)
        return metadata


class HTTPSession(BaseSession):
    """Internal HTTP session object. Used to consume Chainlit through API (no websocket)."""

    def __init__(
        self,
        # Id of the session
        id: str,
        client_type: ClientType,
        # Thread id
        thread_id: Optional[str] = None,
        # Logged-in user information
        user: Optional[Union["User", "PersistedUser"]] = None,
        # Logged-in user token
        token: Optional[str] = None,
        user_env: Optional[Dict[str, str]] = None,
        # WSGI environment variables for the connection request
        environ: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            id=id,
            thread_id=thread_id,
            user=user,
            token=token,
            client_type=client_type,
            user_env=user_env,
            environ=environ,
        )

    async def delete(self):
        """Delete the session."""
        from chainlit.chat_context import chat_contexts

        # Keyed by session id and never dropped anywhere else: leaving the
        # entry behind leaks the full message history for the process
        # lifetime.
        chat_contexts.pop(self.id, None)
        if self.files_dir.is_dir():
            shutil.rmtree(self.files_dir)


ThreadQueue = Deque[tuple[Callable, object, tuple, Dict]]


@dataclass
class PendingAsk:
    """A pending ask prompt waiting for the user's reply.

    The reply is delivered by resolving ``future`` (see the ``ask_reply``
    socket handler); the waiting coroutine lives in
    ``ChainlitEmitter.send_ask_user``. ``deadline`` is the absolute
    ``time.monotonic()`` deadline set when the ask was first emitted, so
    reconnections never extend the timeout.
    """

    step_dict: Dict[str, Any]
    spec: AskSpec
    future: "asyncio.Future"
    deadline: float
    # Serialized actions to re-emit on reconnect so the UI can rebuild the
    # form (the client loses them on refresh). Actions are immutable, so a
    # snapshot is safe.
    restore_actions: List[Dict[str, Any]] = field(default_factory=list)
    # The live element object (anything with to_dict()) — serialized at
    # restore time so element updates made while the ask is pending are not
    # rolled back by the re-emit.
    restore_element: Optional[Any] = None

    @property
    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    @property
    def expired(self) -> bool:
        return self.remaining <= 0

    @property
    def is_live(self) -> bool:
        """Still waiting for an answer: not expired, not resolved."""
        return not self.expired and not self.future.done()

    def cancel(self):
        if not self.future.done():
            self.future.cancel()


class WebsocketSession(BaseSession):
    """Internal web socket session object.

    A socket id is an ephemeral id that can't be used as a session id
    (as it is for instance regenerated after each reconnection).

    The Session object store an internal mapping between socket id and
    a server generated session id, allowing to persists session
    between socket reconnection but also retrieving a session by
    socket id for convenience.
    """

    to_clear: bool = False

    pending_ask: Optional[PendingAsk] = None

    thread_ready_task: Optional[asyncio.Task] = None

    mcp_sessions: dict[str, McpSession]

    def __init__(
        self,
        # Id from the session cookie
        id: str,
        # Associated socket id
        socket_id: str,
        # Function to emit to the client
        emit: Callable[[str, Any], None],
        # Function to emit to the client and wait for a response
        emit_call: Callable[[Literal["ask", "call_fn"], Any, Optional[int]], Any],
        # User specific environment variables. Empty if no user environment variables are required.
        user_env: Dict[str, str],
        client_type: ClientType,
        # WSGI environment variables for the connection request
        environ: Optional[dict[str, Any]] = None,
        # Thread id
        thread_id: Optional[str] = None,
        # Logged-in user information
        user: Optional[Union["User", "PersistedUser"]] = None,
        # Logged-in user token
        token: Optional[str] = None,
        # Chat profile selected before the session was created
        chat_profile: Optional[str] = None,
        # Function to emit an ask with a legacy socket.io ack attached
        emit_ask: Optional[Callable[[Any, Callable], Any]] = None,
    ):
        super().__init__(
            id=id,
            thread_id=thread_id,
            user=user,
            token=token,
            user_env=user_env,
            client_type=client_type,
            chat_profile=chat_profile,
            environ=environ,
        )

        self.socket_id = socket_id
        self.emit_call = emit_call
        self.emit = emit
        self.emit_ask = emit_ask

        self.restored = False
        self.pending_ask = None
        # True while this session's socket is disconnected. The EXPLICIT
        # liveness marker for the supersede check in the resume branch —
        # deliberately not derived from ws_sessions_sid (the sid mapping
        # changes hands during restore and proves nothing about the
        # transport). Set at the very top of the disconnect handler,
        # cleared only in restore() — the single revival path. An instance
        # attribute on purpose: readers use getattr(s, ..., False) so
        # Mock(spec=WebsocketSession) fakes without it count as connected.
        self.socket_disconnected = False
        # Task running the app's on_thread_ready hook. Its own slot on
        # purpose: current_task has two unconditional writers
        # (client_message and the orphan-reply conversion) that would evict
        # a long-lived hook almost immediately after launch, hiding it from
        # stop, the F5 keep-alive check and thread_has_live_task. Read by
        # all three of those and cancelled in delete().
        self.thread_ready_task: Optional[asyncio.Task] = None
        # One-shot launch flag for on_thread_ready, modeled on chat_started:
        # the resume branch re-enters on every reconnect and must not start
        # a second hook or overwrite the slot. Never reset.
        self.resume_task_started = False
        # Number of live owners of the task indicator (see
        # emitter.task_acquire/task_release). Per-session, NOT reset on
        # reconnect: it mirrors live server-side tasks, not client state.
        self.task_counter = 0
        # Step id of the last ask answered in this session (via the
        # ask_reply event or the legacy socket.io ack). Dedup memory for the
        # orphan-reply rescue: the pending_ask slot empties milliseconds
        # after an answer is accepted, so a redelivered reply would
        # otherwise be indistinguishable from one typed into a dead form.
        # The stop handler deliberately does NOT record here — a cancelled
        # ask was never answered, and a reply typed in the ~RTT window after
        # the stop click is live input that must be rescued.
        self.last_resolved_ask_step_id: Optional[str] = None
        # Open once a connection_successful cycle has fully finished
        # (including restore_pending_ask); closed again on every reconnect.
        # The client flushes its send buffer BEFORE emitting
        # connection_successful, so an orphaned ask_reply parked on this
        # gate would otherwise convert into a half-initialized session
        # (fresh thread, or a resumed thread renamed by init_thread).
        self.connection_inited = asyncio.Event()
        # Orphan ask_reply conversions parked on the gate above; cancelled
        # (with a warning) if the session dies before a handshake releases
        # them.
        self.deferred_ask_reply_tasks: List[asyncio.Task] = []
        # True when the current connection is the first one after a full
        # page load (the client lost its UI state); set on every connect.
        self.fresh_page_load = False
        # True once a resume of this session has been fully processed
        # (resume="delete" filtering and deletion done). Never reset: only
        # the first entry into the resume branch — a genuine resume of a
        # dead session — may delete flagged steps; re-entries of the same
        # session (F5, transport reconnect) must not.
        self.resume_processed = False
        # Doomed steps whose data-layer deletion did not fully succeed on a
        # resume entry, with their still-undeleted elements. Filtered out of
        # the thread payload and retried on this session's next entry into
        # the resume branch (re-entries never re-run split_resume_delete —
        # that would doom fresh live flagged messages).
        self.resume_delete_retry: Tuple[List[Dict], List[Dict]] = ([], [])
        # Element dicts of the resumed/replayed thread, keyed by step id.
        # Message objects rebuilt via Message.from_dict carry no elements,
        # so the in-memory transcript replay reads attachments from here.
        # Replaced wholesale on every repopulation from a thread payload.
        self.transcript_element_dicts: Dict[str, List[Dict]] = {}

        self.thread_queues: Dict[str, ThreadQueue] = {}
        self.mcp_sessions = {}

        match = (
            re.match(
                r"^\s*([a-zA-Z0-9-]+)", environ.get("HTTP_ACCEPT_LANGUAGE", "en-US")
            )
            if environ
            else None
        )
        self.language = match.group(1) if match else "en-US"

        # Start with global config; chat-profile overrides are applied
        # asynchronously via resolve_config() after construction.
        from chainlit.config import config as global_config

        self.config: ChainlitConfig = global_config

        ws_sessions_id[self.id] = self
        ws_sessions_sid[socket_id] = self

    def get_config(self) -> "ChainlitConfig":
        """
        Return the config for this session.

        If ``resolve_config()`` has already been awaited the returned
        object includes any chat-profile overrides; otherwise it falls
        back to the global config.
        """
        return self.config

    async def resolve_config(self) -> "ChainlitConfig":
        """
        Resolve chat-profile config overrides asynchronously.

        Must be awaited from an async context (the SocketIO ``connect``
        handler) *after* the session has been constructed. The old
        implementation ran ``run_until_complete`` inside the already
        running loop — a re-entry that only worked through
        nest_asyncio's patch. Once overrides are successfully applied
        the result is cached on ``self.config`` and subsequent calls
        return immediately.
        """
        from chainlit.config import config as global_config

        # Nothing to resolve when there is no chat profile.
        if not self.chat_profile:
            return self.config

        # Already resolved — return cached value.
        if self.config is not global_config:
            return self.config

        if global_config.code.set_chat_profiles:
            try:
                profiles = await global_config.code.set_chat_profiles(
                    self.user, self.language
                )
                current_profile = next(
                    (p for p in profiles if p.name == self.chat_profile), None
                )
                if current_profile and getattr(
                    current_profile, "config_overrides", None
                ):
                    self.config = global_config.with_overrides(
                        current_profile.config_overrides
                    )
            except Exception:
                pass
        return self.config

    def restore(self, new_socket_id: str):
        """Associate a new socket id to the session."""
        ws_sessions_sid.pop(self.socket_id, None)
        ws_sessions_sid[new_socket_id] = self
        self.socket_id = new_socket_id
        self.restored = True
        # The socket is live again — the session is no longer a supersede
        # candidate. (A late disconnect of the OLD socket cannot re-flag
        # it: the sid mapping above was already handed over, so that
        # handler finds no session.)
        self.socket_disconnected = False
        # Closed here, in connect, NOT at the start of connection_successful:
        # buffered client events flush in between, and a gate left open from
        # the previous connection would let an orphaned ask_reply convert
        # into the not-yet-restored session.
        self.connection_inited.clear()

    async def delete(self):
        """Delete the session."""
        from chainlit.chat_context import chat_contexts

        # Wake up any coroutine waiting on a pending ask so it doesn't
        # outlive the session and write "Timed out" into the old thread
        # long after the user is gone. Must not require a chainlit context:
        # delete() is also called from the disconnect GC timer.
        if self.pending_ask is not None:
            self.pending_ask.cancel()
            self.pending_ask = None

        # The on_thread_ready task dies with its session: delete() is also
        # the GC path (clear_on_timeout checks no liveness), and a surviving
        # hook would be a zombie writing into the thread while a successor
        # session starts a second one. current_task is deliberately NOT
        # cancelled here — that would start killing in-flight on_message
        # tasks of merely disconnected users.
        if self.thread_ready_task is not None and not self.thread_ready_task.done():
            self.thread_ready_task.cancel()

        # A conversion still parked on the handshake gate has nowhere left
        # to run — its handshake never finished. Logged, not silent: this is
        # the one path where the user's rescued input is genuinely lost.
        for task in list(getattr(self, "deferred_ask_reply_tasks", None) or []):
            if not task.done():
                logger.warning(
                    "Cancelling a parked ask_reply conversion for session %s: "
                    "the session is being deleted before a connection "
                    "handshake released it.",
                    self.id,
                )
                task.cancel()
        self.deferred_ask_reply_tasks = []

        # Same fate as user_sessions (popped in the disconnect handler):
        # chat_contexts is keyed by session id and grows with every message,
        # so a surviving entry leaks the full transcript until restart.
        chat_contexts.pop(self.id, None)
        if self.files_dir.is_dir():
            shutil.rmtree(self.files_dir)
        ws_sessions_sid.pop(self.socket_id, None)
        ws_sessions_id.pop(self.id, None)

        for mcp_session in list(self.mcp_sessions.values()):
            try:
                await mcp_session.close()
            except Exception:
                logger.debug(
                    "Error closing MCP session %r during session delete",
                    mcp_session.name,
                    exc_info=True,
                )
        self.mcp_sessions.clear()

    async def flush_method_queue(self):
        for method_name, queue in self.thread_queues.items():
            while queue:
                method, self, args, kwargs = queue.popleft()
                try:
                    await method(self, *args, **kwargs)
                except Exception as e:
                    logger.error(f"Error while flushing {method_name}: {e}")

    @classmethod
    def get(cls, socket_id: str):
        """Get session by socket id."""
        return ws_sessions_sid.get(socket_id)

    @classmethod
    def get_by_id(cls, session_id: str):
        """Get session by session id."""
        return ws_sessions_id.get(session_id)

    @classmethod
    def require(cls, socket_id: str):
        """Throws an exception if the session is not found."""
        if session := cls.get(socket_id):
            return session
        raise ValueError("Session not found")


ws_sessions_sid: Dict[str, WebsocketSession] = {}
ws_sessions_id: Dict[str, WebsocketSession] = {}
