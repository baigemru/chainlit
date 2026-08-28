"""Fixtures shared by the ``cl.*`` API tests.

A real ``Session`` and a real ``Emitter`` rather than mocks: what the API
does is put frames on the session's queue and rows on its writer, and both
are observable -- ``session.outbound.pending_frames`` and ``writer.held``.
Asserting on those is asserting on what the client and the database would
see, which a mock of the emitter never was.
"""

import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import pytest
import pytest_asyncio

from chainlit import config
from chainlit.context import ChainlitContext, context_var
from chainlit.emitter import Emitter
from chainlit.protocol.payloads import FileRef, Step as StepPayload
from chainlit.user import PersistedUser
from chainlit.user_session import UserSession
from chainlit.ws.session import Session


class RecordingRunner:
    """A ``CallbackRunner`` that only remembers what it was asked."""

    def __init__(self) -> None:
        self.user_messages: List[StepPayload] = []
        self.ask_files: List[Sequence[Mapping[str, Any]]] = []
        self.actions: List[Mapping[str, Any]] = []
        self.stops = 0

    async def call_action(self, session: Session, action: Mapping[str, Any]) -> Any:
        self.actions.append(action)
        return None

    async def on_message(
        self,
        session: Session,
        message: StepPayload,
        file_references: Sequence[FileRef] = (),
    ) -> None:
        self.user_messages.append(message)

    async def on_stop(self, session: Session) -> None:
        self.stops += 1

    async def record_user_message(self, session: Session, message: StepPayload) -> Any:
        self.user_messages.append(message)
        return message

    async def record_ask_files(
        self, session: Session, files: Sequence[Mapping[str, Any]], *, for_id: str
    ) -> None:
        self.ask_files.append(files)


@pytest.fixture
def persisted_test_user():
    return PersistedUser(
        id="test_user_id",
        createdAt=datetime.datetime.now().isoformat(),
        identifier="test_user_identifier",
    )


@pytest.fixture
def session_factory(
    persisted_test_user: PersistedUser, tmp_path: Path
) -> Callable[..., Session]:
    def create(**kwargs: Any) -> Session:
        session = Session(
            id=kwargs.get("id", "test_session_id"),
            runner=kwargs.get("runner", RecordingRunner()),
            user=kwargs.get("user", persisted_test_user),
            thread_id=kwargs.get("thread_id", "test_thread_id"),
            chat_profile=kwargs.get("chat_profile"),
            client_type=kwargs.get("client_type", "webapp"),
            user_env=kwargs.get("user_env", {"test_env": "value"}),
            files_root=tmp_path,
        )
        session.writer = kwargs.get("writer")
        return session

    return create


@pytest.fixture
def session(session_factory) -> Session:
    return session_factory()


@asynccontextmanager
async def bind_context(session: Session):
    """Bind the current task to ``session`` for the duration of the block."""
    ctx = ChainlitContext(session, Emitter(session))
    token = context_var.set(ctx)
    try:
        yield ctx
    finally:
        context_var.reset(token)


@pytest_asyncio.fixture
async def mock_chainlit_context(session: Session):
    """An ``async with`` block inside which ``cl.context`` is ``session``.

    The name is historical; nothing in it is a mock any more.
    """
    return bind_context(session)


@pytest.fixture
def frames() -> Callable[..., List[Any]]:
    """The frames a session has queued, optionally only one kind of them."""

    def read(session: Session, kind: Optional[type] = None) -> List[Any]:
        pending = list(session.outbound.pending_frames)
        if kind is None:
            return pending
        return [frame for frame in pending if isinstance(frame, kind)]

    return read


@pytest.fixture
def user_session():
    return UserSession()


@pytest.fixture
def test_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CHAINLIT_ROOT_PATH", str(tmp_path))

    test_config = config.load_config()

    monkeypatch.setattr("chainlit.callbacks.config", test_config)
    monkeypatch.setattr("chainlit.config.config", test_config)

    return test_config


@pytest.fixture
def state_of(session: Session) -> Dict[str, Any]:
    return session.state
