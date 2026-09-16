"""What ``cl.*`` writes down, when there is somewhere to write it.

The counterpart of ``chainlit.emitter``: that module produces frames, this
one produces rows. Every function reads the current session's writer and
does nothing when there is none -- an application without a database is the
default Chainlit has always had, and its ``Message.send()`` must cost
nothing extra.

Nothing here awaits the database. The writer batches and orders; the
callers keep running. The exception is ``open_thread``, which has to know
the user's row id before it can attribute the thread, and says so.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence, Union

import msgspec

from chainlit.context import context
from chainlit.logger import logger
from chainlit.persistence.records import ElementRecord, StepRecord, ThreadPatch
from chainlit.persistence.storage.disposition import content_disposition, element_mime
from chainlit.persistence.writer import (
    DeleteElement,
    DeleteStep,
    PatchThread,
    SaveStep,
    SessionWriter,
)
from chainlit.ws.sidebar import SIDEBAR_META_KEY, sidebar_meta

if TYPE_CHECKING:
    from chainlit.ws.session import Session

__all__ = [
    "delete_element",
    "delete_step",
    "drop_elements",
    "open_thread",
    "patch_sidebar",
    "save_element",
    "save_step",
    "writer_of",
]

# State keys that describe a live session, never a thread. ``transit`` is a
# hand-off between two sessions and would resurrect on every resume; the
# rest are mirrors of the session the accessor keeps for the app's
# convenience, and the conversation log, which is the steps table's job.
#
# ``__sidebar`` is here for the opposite reason: it is the thread's, not the
# application's, and an app writing that key into ``user_session`` must not
# be able to shadow the panel's own record. The other half of the rule is
# the exclusion tuple in ``runner._resume``, which keeps the stored value
# from coming back out into ``session.state`` -- two lists, and they have to
# agree.
_VOLATILE_STATE = frozenset(
    {"transit_message", "__chat_messages", "id", "user", "env", SIDEBAR_META_KEY}
)


def writer_of(session: Optional["Session"] = None) -> Optional[SessionWriter]:
    """The writer of the current (or given) session, if it has a database."""
    if session is None:
        session = context.session
    writer = session.writer
    return writer if isinstance(writer, SessionWriter) else None


def save_step(step: Mapping[str, Any]) -> None:
    """Queue a step dict as it stands. Fields it does not carry are kept."""
    writer = writer_of()
    if writer is None:
        return
    record = msgspec.convert(_stripped(step), StepRecord)
    writer.submit(SaveStep(record))


def delete_step(step_id: str) -> None:
    writer = writer_of()
    if writer is not None:
        writer.submit(DeleteStep(step_id))


def save_element(
    element: Mapping[str, Any],
    *,
    path: Optional[str] = None,
    content: Optional[Union[bytes, str]] = None,
) -> None:
    """Queue an element, uploading its blob first when there is somewhere to.

    The upload is handed to the writer as a callable and runs only once the
    writer decides the element is going to be written at all -- a session
    that never interacts uploads nothing. The storage client settles
    ``url`` and ``objectKey``; without one the row is written as given.
    """
    writer = writer_of()
    if writer is None:
        return
    record = msgspec.convert(_stripped(element), ElementRecord)
    storage = writer.persistence.storage
    if storage is None or (path is None and content is None):
        writer.submit_element(record)
        return

    async def upload() -> Optional[ElementRecord]:
        data: Union[bytes, str]
        if path is not None:
            data = await asyncio.to_thread(Path(path).read_bytes)
        else:
            assert content is not None
            data = content
        owner = getattr(context.session.user, "identifier", None) or "unknown"
        object_key = f"{owner}/{record.id}" + (f"/{record.name}" if record.name else "")
        # The disposition is written onto the object, not only into the
        # response the element route builds: a presigned url serves the blob
        # straight out of the bucket, and what it carries is the object's own
        # header. An xlsx fetched that way without one opens as a tab of XML.
        mime = element_mime(record)
        uploaded = await storage.upload_file(
            object_key=object_key,
            data=data,
            mime=mime,
            overwrite=True,
            content_disposition=content_disposition(record.name, mime),
        )
        if not uploaded:
            raise ValueError(f"Storage refused the blob of element {record.id}")
        return msgspec.structs.replace(
            record,
            url=uploaded.get("url", record.url),
            object_key=uploaded.get("object_key", record.object_key),
        )

    writer.submit_element(record, upload)


def delete_element(element_id: str, thread_id: Optional[str] = None) -> None:
    writer = writer_of()
    if writer is not None:
        writer.submit(DeleteElement(element_id, thread_id))


def drop_elements(session: "Session", element_ids: Sequence[str]) -> None:
    """Delete the rows of elements nothing in the session is showing any more.

    The panel's half of ``element.remove``: a card taken off the screen whose
    row survives is a card the next cold resume puts back.

    An id that is not a uuid is skipped rather than submitted. A slot filled
    with ``persist=False`` accepts any id the application likes, no such
    element ever reached the ``elements`` table, and a ``DeleteElement``
    carrying one would fail ``to_uuid`` inside the transaction and take every
    innocent write in the batch with it.
    """
    writer = writer_of(session)
    if writer is None:
        return
    for element_id in element_ids:
        try:
            uuid.UUID(element_id)
        except ValueError, AttributeError, TypeError:
            continue
        writer.submit(DeleteElement(element_id, session.thread_id))


def patch_sidebar(session: "Session") -> None:
    """Write down what the element panel is, as one metadata patch.

    Submitted *after* the element rows and deletes of the same mutation, so
    the queue orders them: a stored frame naming an element whose row is
    still behind it would rebuild a panel with a hole in it for as long as
    the writer takes to catch up.

    Only structural moves reach here -- filling a slot, closing one, clearing
    the panel, and the user closing a tab. ``hide``, ``show`` and ``activate``
    do not: a click must not cost a database write, and a ``visible`` lost to
    a crash costs the user one chevron.

    The patch merges by key, so naming ``__sidebar`` alone is enough and the
    rest of the thread's metadata is left where it is.
    """
    writer = writer_of(session)
    if writer is None or not session.thread_id:
        return
    writer.submit(
        PatchThread(
            session.thread_id,
            ThreadPatch(metadata={SIDEBAR_META_KEY: sidebar_meta(session.sidebar)}),
        )
    )


async def open_thread(session: "Session", name: str, *, announce: bool = True) -> None:
    """The thread's first interaction: name the row, then release the writes.

    The one place the wire and the database meet on purpose. The client is
    told first, so a session with no database still learns its thread id;
    the row is attributed and the gate opens after, behind the patch that
    creates it -- steps queued before this moment are waiting for exactly
    that row. ``announce=False`` records the interaction without the frame,
    for the one caller that runs before ``session.ready`` may go out.
    """
    from chainlit.emitter import Emitter

    if announce:
        Emitter(session).first_interaction(name)
    else:
        session.first_interaction = name

    writer = writer_of(session)
    if writer is None or not session.thread_id:
        return

    user_id: Optional[str] = None
    identifier = getattr(session.user, "identifier", None)
    if identifier:
        try:
            async with writer.persistence.uow() as unit:
                user = await unit.users.get_by_identifier(identifier)
            user_id = user.id if user is not None else None
        except Exception:
            logger.exception(
                "Could not attribute thread %s to its user", session.thread_id
            )

    writer.open_gate(
        PatchThread(
            session.thread_id,
            ThreadPatch(
                name=name,
                user_id=user_id,
                user_identifier=identifier,
                metadata=thread_state(session),
                parent_thread_id=session.parent_thread_id,
            ),
        )
    )


def thread_state(session: "Session") -> dict[str, Any]:
    """The session state as the thread remembers it across a resume."""
    from chainlit.config import config

    state = {k: v for k, v in session.state.items() if k not in _VOLATILE_STATE}
    state["chat_profile"] = session.chat_profile
    state["client_type"] = session.client_type
    state["device"] = session.device
    state["env"] = dict(session.user_env) if config.project.persist_user_env else {}
    # After the filter rather than through it, so the panel's own record
    # wins over an application key of the same name. Every existing
    # ``PatchThread`` site carries it from here -- the ``open_thread``
    # prelude, the disconnect, the release -- which is what makes the
    # immediate patches of ``patch_sidebar`` insurance rather than the only
    # record.
    state[SIDEBAR_META_KEY] = sidebar_meta(session.sidebar)
    return _jsonable(state)


def _jsonable(value: Mapping[str, Any]) -> dict[str, Any]:
    """Drop what JSON cannot carry rather than fail the whole write.

    Round-tripped through msgspec key by key: what survives is plain JSON
    data, never an object msgspec happens to know how to encode but the
    database driver does not.
    """
    return {
        k: msgspec.json.decode(msgspec.json.encode(v))
        for k, v in value.items()
        if _encodable(v)
    }


def _encodable(value: Any) -> bool:
    try:
        msgspec.json.encode(value)
    except TypeError, msgspec.EncodeError:
        return False
    return True


def _stripped(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The dict without the keys that are transient or wire-only."""
    return {k: v for k, v in payload.items() if k not in ("wait", "steps")}
