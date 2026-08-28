"""The application, as the transport sees it.

``ChainlitPlugin`` builds one of these and hands it to the websocket in
three pieces: ``make_session`` for every id that arrives, ``on_arrival`` and
``on_ready`` around the handshake, ``on_disconnect`` after it. The session
holds it as its ``runner`` for everything the client can say afterwards.

Every entry into the application's code goes through ``_bind``: it sets the
context variable the ``cl.*`` API reads, and it is the *only* thing that
does. A callback that finds no context is a callback launched from
somewhere else, and that is the bug to fix.

What this module does not do is decide what a reconnect means. That is the
handshake's job (``chainlit.ws.handshake``); this module is told the outcome
and reacts to it.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Mapping, Optional, Sequence

import msgspec

from chainlit import persist
from chainlit.context import init_context
from chainlit.emitter import Emitter
from chainlit.logger import logger
from chainlit.persistence.records import ThreadDetail, ThreadPatch
from chainlit.persistence.writer import PatchThread, SessionWriter, WriterRegistry
from chainlit.protocol.payloads import (
    AskTextReply,
    AskTextSpec,
    Element,
    FileRef,
    Step,
)
from chainlit.utils import utc_now
from chainlit.ws.handshake import Arrival, sweep_superseded
from chainlit.ws.registry import SessionRegistry
from chainlit.ws.session import Session, TranscriptEntry

if TYPE_CHECKING:
    from chainlit.persistence.config import Persistence
    from chainlit.protocol.client import Hello
    from chainlit.transit_store import TransitStore

__all__ = ["ApplicationRunner", "ThreadStoreAdapter"]

# How long a session survives its socket. The old default; a reload takes
# well under it and a closed tab is gone for good after it.
DEFAULT_SESSION_TIMEOUT = 300.0


def _identifier(user: Any) -> Optional[str]:
    return getattr(user, "identifier", None) if user is not None else None


def _is_text_answer(step: Step) -> bool:
    """A user message that can answer a text question, and nothing else.

    Narrow on purpose: it runs before ``on_message``, and a message carrying
    a command or an attachment is a message, not an answer.
    """
    return (
        step.type == "user_message"
        and step.command is None
        and step.modes is None
        and isinstance(step.output, str)
    )


class ApplicationRunner:
    """Runs ``config.code`` on behalf of sessions."""

    def __init__(
        self,
        config: Any,
        *,
        registry: SessionRegistry,
        persistence: Optional["Persistence"] = None,
        transit: Optional["TransitStore"] = None,
        session_timeout: float = DEFAULT_SESSION_TIMEOUT,
    ) -> None:
        self.config = config
        self.registry = registry
        self.persistence = persistence
        self.transit = transit
        self.session_timeout = session_timeout
        self.writers = WriterRegistry()
        self._background: set["asyncio.Task[Any]"] = set()

    # --------------------------------------------------------------- helpers

    @property
    def code(self) -> Any:
        return self.config.code

    def _bind(self, session: Session) -> Emitter:
        emitter = Emitter(session, transit=self.transit)
        init_context(session, emitter)
        return emitter

    def _launch(
        self, session: Session, coro: Awaitable[Any], *, slot: str = "current_task"
    ) -> "asyncio.Task[Any]":
        """Run application code as a task the session owns.

        The context is bound inside the task, so it travels with the
        coroutine and not with whoever created it.
        """

        async def run() -> Any:
            self._bind(session)
            try:
                return await coro
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Callback failed in session %s", session.id)
                return None

        task = asyncio.create_task(run())
        setattr(session, slot, task)
        return task

    # ---------------------------------------------------------- construction

    def make_session(self, session_id: str, hello: "Hello", user: Any) -> Session:
        """A session for an id the registry has decided is new."""
        thread_id = hello.thread_id or str(uuid.uuid4())
        session = Session(
            id=session_id,
            runner=self,
            user=user,
            thread_id=thread_id,
            chat_profile=hello.chat_profile,
            client_type=hello.client_type,
            user_env=dict(hello.user_env or {}),
        )
        if self.persistence is not None:
            # Held until the first interaction: a session that never says
            # anything leaves no rows behind.
            session.writer = SessionWriter(
                self.persistence,
                thread_id,
                registry=self.writers,
                hold_until_interaction=True,
            ).start()
        return session

    def reload_clients(self) -> None:
        """Tell every connected client to drop its session and reload.

        The dev file-watcher's signal: the module was rebuilt, and whatever
        the sessions hold was built by the old one.
        """
        from chainlit.protocol.server import Reload

        for entry in list(self.registry):
            session = entry.session
            if isinstance(session, Session):
                session.send(Reload())

    # ------------------------------------------------------------- handshake

    async def on_arrival(self, arrival: Arrival) -> None:
        """State only. Nothing here may send: ``session.ready`` has not gone."""
        session = arrival.session
        assert session is not None
        if arrival.outcome.value == "kept":
            return

        for entry in arrival.superseded:
            await self.teardown(entry.session)  # type: ignore[arg-type]
        for entry in sweep_superseded(self.registry, session.thread_id, session):
            await self.teardown(entry.session)  # type: ignore[arg-type]

        if await self._resume(session):
            return
        await self._claim_transit(session)

    async def _claim_transit(self, session: Session) -> None:
        if self.transit is None or session.first_interaction:
            return
        record = await self.transit.claim(session.id, _identifier(session.user))
        if record is None:
            return
        session.parent_thread_id = record.parent
        if record.value is None:
            return
        session.state["transit_message"] = record.value
        name = record.value if isinstance(record.value, str) and record.value else None
        await persist.open_thread(session, name or session.chat_profile or "transit")

    async def _resume(self, session: Session) -> bool:
        """Load the thread this session was opened on, if it is the user's."""
        if self.persistence is None or session.first_interaction:
            return False
        if not (self.code.on_chat_resume or self.code.on_thread_ready):
            return False
        thread_id = session.thread_id
        identifier = _identifier(session.user)
        if not thread_id or identifier is None:
            return False

        async with self.persistence.uow() as unit:
            detail = await unit.threads.get_detail(thread_id)
        if detail is None or detail.user_identifier != identifier:
            return False

        if self.transit is not None:
            # A record parked for this id would outlive a resume, which
            # never reads it, and leak into an unrelated chat later.
            await self.transit.claim(session.id, identifier)

        metadata = dict(detail.metadata or {})
        session.state.update(
            {k: v for k, v in metadata.items() if k not in ("env", "client_type")}
        )
        if profile := metadata.get("chat_profile"):
            session.chat_profile = profile
        session.parent_thread_id = detail.parent_thread_id
        session.transcript[:] = _transcript_of(detail)
        session.first_interaction = "resume"
        session.resumed_thread_id = thread_id
        if session.writer is not None:
            session.writer.open_gate()
        # Handed to on_ready, which launches the hooks once the screen is
        # rebuilt. Stored on the session rather than returned: the two hooks
        # do not share a frame.
        session.state["__resumed_thread"] = _thread_dict(detail)
        return True

    async def on_ready(self, arrival: Arrival) -> None:
        """The screen is rebuilt; now the application may speak."""
        session = arrival.session
        assert session is not None
        emitter = self._bind(session)

        if getattr(self.config.features, "hot_swap_chat_profile", False):
            if session.chat_profile:
                emitter.profile_changed(session.chat_profile, sync=True)

        resumed = session.state.pop("__resumed_thread", None)
        if resumed is not None:
            self._launch(session, self._resume_hooks(session, resumed))
        elif not session.chat_started and self.code.on_chat_start:
            session.chat_started = True
            self._launch(session, self.code.on_chat_start())

        emitter.resync_task_indicator()

    async def _resume_hooks(self, session: Session, thread: Mapping[str, Any]) -> None:
        """``on_chat_resume`` first, then ``on_thread_ready`` in its own slot.

        The second runs even if the first raised: a resume that crashed is
        still a resume, and the hook that waits on it has always been told.
        """
        try:
            if self.code.on_chat_resume:
                await self.code.on_chat_resume(thread)
        finally:
            if self.code.on_thread_ready:
                self._launch(
                    session, self.code.on_thread_ready(thread), slot="thread_ready_task"
                )
            self._bind(session).resync_task_indicator()

    # ------------------------------------------------------------- lifecycle

    async def on_disconnect(self, session: Session) -> None:
        """The socket is gone. Persist what the thread remembers, then wait."""
        self._bind(session)
        if self.code.on_chat_end:
            try:
                await self.code.on_chat_end()
            except Exception:
                logger.exception("on_chat_end failed in session %s", session.id)

        if session.first_interaction and session.thread_id and session.writer:
            session.writer.submit(
                PatchThread(
                    session.thread_id,
                    ThreadPatch(metadata=persist.thread_state(session)),
                )
            )

        if session.reaper is not None and not session.reaper.done():
            session.reaper.cancel()
        session.reaper = asyncio.create_task(self._reap(session))

    async def _reap(self, session: Session) -> None:
        await asyncio.sleep(self.session_timeout)
        if session.connected:
            return
        entry = self.registry.get(session.id)
        if entry is not None and entry.session is session:
            self.registry.discard(entry)
        await self.teardown(session)

    async def teardown(self, session: Session) -> None:
        """Stop the session's work and release what it holds. Idempotent."""
        session.cancel_work()
        if session.pending_ask is not None:
            Emitter(session).end_ask(session.pending_ask.step_id, "superseded")
        writer = session.writer
        session.writer = None
        if isinstance(writer, SessionWriter):
            try:
                await writer.aclose()
            except Exception:
                logger.exception(
                    "Writer for thread %s did not close cleanly", session.thread_id
                )
        session.discard_files()
        session.outbound.abort()

    # ---------------------------------------------------- CallbackRunner API

    async def on_message(
        self, session: Session, message: Step, file_references: Sequence[FileRef] = ()
    ) -> None:
        """A message from the composer: an answer if a text question is open."""
        ask = session.pending_ask
        if (
            ask is not None
            and not ask.future.done()
            and isinstance(ask.spec, AskTextSpec)
            and not file_references
            and _is_text_answer(message)
        ):
            # No await between the check and the result: a stop or a timeout
            # can cancel the future in any gap, and set_result on a
            # cancelled future raises out of the reader.
            ask.future.set_result(AskTextReply(step=message))
            return

        self._launch(session, self._process(session, message, file_references))

    async def _process(
        self, session: Session, message: Step, file_references: Sequence[FileRef]
    ) -> None:
        from chainlit.message import ErrorMessage

        emitter = self._bind(session)
        emitter.resync_task_indicator()
        try:
            user_message = await self.record_user_message(
                session, message, file_references=file_references
            )
            if self.code.on_message:
                await self.code.on_message(user_message)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            logger.exception("on_message failed in session %s", session.id)
            await ErrorMessage(
                author="Error", content=str(error) or error.__class__.__name__
            ).send()
        finally:
            emitter.resync_task_indicator()

    async def record_user_message(
        self,
        session: Session,
        message: Step,
        *,
        file_references: Sequence[FileRef] = (),
    ) -> Any:
        """Transcript, persistence, first interaction -- not ``on_message``."""
        from chainlit.chat_context import chat_context
        from chainlit.element import Element as ElementObject
        from chainlit.message import Message

        self._bind(session)
        step_dict = msgspec.to_builtins(message)
        user_message = Message.from_dict(step_dict)
        user_message.created_at = utc_now()
        chat_context.add(user_message)
        await user_message._create()

        if not session.first_interaction:
            await persist.open_thread(session, user_message.content)

        files = [
            session.files[ref.id] for ref in file_references if ref.id in session.files
        ]
        if files:
            user_message.elements = [
                ElementObject.from_dict(
                    {
                        "id": file["id"],
                        "name": file["name"],
                        "path": str(file["path"]),
                        "chainlitKey": file["id"],
                        "display": "inline",
                        "type": ElementObject.infer_type_from_mime(file["type"]),
                        "mime": file["type"],
                    }
                )
                for file in files
            ]
            for element in user_message.elements:
                await element.send(for_id=user_message.id)
        return user_message

    async def record_ask_files(
        self, session: Session, files: Sequence[Mapping[str, Any]], *, for_id: str
    ) -> None:
        from chainlit.element import File

        self._bind(session)
        for file in files:
            await File(
                id=file["id"],
                name=file["name"],
                path=str(file["path"]),
                mime=file["type"],
                chainlit_key=file["id"],
                for_id=for_id,
            )._create()
        if not session.first_interaction:
            await persist.open_thread(
                session, ",".join(str(file["name"]) for file in files)
            )

    async def on_stop(self, session: Session) -> None:
        """Cancel everything, then run ``on_stop`` -- as a task, not inline.

        The caller is the socket's reader. An ``on_stop`` that asks a
        question or awaits anything would hold the reader, and the reply it
        waits for arrives on the reader it is holding.
        """
        self._stop_work(session)
        task = asyncio.create_task(self._after_stop(session))
        # Held, or the loop may collect it before it runs.
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _stop_work(self, session: Session) -> None:
        emitter = self._bind(session)
        ask = session.pending_ask
        if ask is not None:
            ask.cancel()
            # Freed now, so a follow-up ask from on_stop is not refused
            # while the cancelled waiter's finally has yet to run.
            session.pending_ask = None
            emitter.end_ask(ask.step_id, "cancelled")

        for task in (
            session.current_task,
            session.thread_ready_task,
            session.profile_start_task,
        ):
            if task is not None and not task.done():
                task.cancel()

    async def _after_stop(self, session: Session) -> None:
        from chainlit.message import Message

        emitter = self._bind(session)
        await Message(content="Task manually stopped.").send()
        if self.code.on_stop:
            try:
                await self.code.on_stop()
            except Exception:
                logger.exception("on_stop failed in session %s", session.id)
        emitter.resync_task_indicator()

    async def call_action(self, session: Session, action: Mapping[str, Any]) -> Any:
        from chainlit.action import Action

        callback = self.code.action_callbacks.get(action.get("name", ""))
        if callback is None:
            raise LookupError(action.get("name", ""))
        self._bind(session)
        return await callback(Action(**dict(action)))


# ---------------------------------------------------------------- adapters


def _transcript_of(detail: ThreadDetail) -> list[TranscriptEntry]:
    """The stored thread in the shape the replay sends."""
    by_step: dict[str, TranscriptEntry] = {}
    entries: list[TranscriptEntry] = []
    for record in detail.steps:
        step = msgspec.convert(msgspec.to_builtins(record), Step)
        entry = TranscriptEntry(step=step)
        by_step[step.id] = entry
        entries.append(entry)
    for element in detail.elements:
        payload = msgspec.convert(msgspec.to_builtins(element), Element)
        if payload.for_id and payload.for_id in by_step:
            by_step[payload.for_id].elements.append(payload)
    return entries


def _thread_dict(detail: ThreadDetail) -> dict[str, Any]:
    """The ``ThreadDict`` the hooks have always received."""
    return msgspec.to_builtins(detail)


class ThreadStoreAdapter:
    """The handshake's ``ThreadStore`` over the persistence services."""

    def __init__(self, persistence: "Persistence") -> None:
        self.persistence = persistence

    async def transcript_of(self, thread_id: str) -> Sequence[TranscriptEntry]:
        async with self.persistence.uow() as unit:
            detail = await unit.threads.get_detail(thread_id)
        return _transcript_of(detail) if detail is not None else []

    async def delete_steps(self, thread_id: str, step_ids: set[str]) -> None:
        async with self.persistence.uow() as unit:
            for step_id in step_ids:
                await unit.steps.remove(step_id)
