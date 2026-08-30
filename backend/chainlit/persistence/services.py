"""Services — record in, record out.

The services are the only place that knows both halves of the mapping: the
msgspec records the app speaks, and the rows the schema holds. Handlers,
socket callbacks and the data-layer shim above them never see a model.

The domain API on every service is ``fetch`` / ``save`` / ``remove``. The
generic verbs advanced_alchemy contributes — ``get``, ``upsert``, ``delete``
and friends — stay inherited and callable, but they take and return *models*
and skip every rule in this module: AA's ``delete`` on a thread, for one,
drops the row without the explicit child deletes below.

Two conversions run through everything here:

* ids are ``str`` on the wire and ``uuid.UUID`` in the database, because the
  deployed columns are native ``uuid``;
* timestamps are ISO ``str`` on the wire and ``datetime`` in the models, where
  ``ISOTimestamp`` puts them back into the schema's TEXT columns.
"""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Dict, Iterator, List, Optional, Sequence, cast

from advanced_alchemy.extensions.litestar import exceptions, service
from msgspec import UNSET, Struct
from msgspec.structs import asdict, fields
from sqlalchemy import CursorResult, Result, Row, RowMapping, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlalchemy.sql import Executable

from chainlit.logger import logger
from chainlit.persistence import statements
from chainlit.persistence.models import (
    ELEMENTS,
    FEEDBACKS,
    STEPS,
    THREADS,
    Element,
    Feedback,
    Step,
    Thread,
    User,
    iso_datetime,
    iso_text,
)
from chainlit.persistence.records import (
    ElementRecord,
    FeedbackRecord,
    PageInfoRecord,
    StepRecord,
    ThreadDetail,
    ThreadPage,
    ThreadPatch,
    ThreadQuery,
    ThreadRecord,
    UserRecord,
)
from chainlit.persistence.repositories import (
    ElementRepository,
    FeedbackRepository,
    StepRepository,
    ThreadRepository,
    UserRepository,
)

# Columns holding a native uuid, by their database name.
UUID_COLUMNS = frozenset(
    {"id", "threadId", "parentId", "forId", "userId", "parentThreadId"}
)
# Columns holding an ISO timestamp in a TEXT column.
TIMESTAMP_COLUMNS = frozenset({"createdAt", "updatedAt", "start", "end"})


class InvalidIdError(ValueError):
    """An id that is not a uuid reached the persistence package.

    A ``ValueError`` subclass, so the read paths that already fail closed on
    a malformed id (the history cursor, the user filter) keep working
    unchanged -- and a distinct type, so nothing else has to guess whether a
    bare ``ValueError`` out of a service was a bad id or a bug.

    On an HTTP route this should never fire: the route annotates the id as
    ``UUID`` and Litestar refuses the request before the handler runs, which
    is the difference between validating at the signature and validating
    inside the handler. It exists for the paths that have no route -- a
    websocket frame, the writer, a background task -- where the id arrives as
    text and the alternative is a 500.
    """


def to_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    """Parse an id coming off the wire."""
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as error:
        raise InvalidIdError(f"not a valid id: {value!r}") from error


def from_uuid(value: Optional[uuid.UUID]) -> Optional[str]:
    """Render an id for the wire."""
    return None if value is None else str(value)


# The wire<->column timestamp format. Defined next to ``ISOTimestamp`` in
# models, so a record, a stored value and a page cursor cannot drift apart.
to_datetime = iso_datetime
from_datetime = iso_text


def now() -> datetime:
    return datetime.now(UTC)


def _column_values(record: Struct, skip: Sequence[str] = ()) -> Dict[str, Any]:
    """The provided fields of a record, keyed by database column.

    Field names round-trip through msgspec's ``rename="camel"``, which is the
    same camelCase the columns use — so the encode name *is* the column name
    and there is no third naming scheme to maintain. ``UNSET`` fields are left
    out entirely: that is what tells the upsert to leave the column alone.
    """
    values: Dict[str, Any] = {}
    for field in fields(record):
        if field.name in skip:
            continue
        value = getattr(record, field.name)
        if value is UNSET:
            continue
        column = field.encode_name
        if column in UUID_COLUMNS:
            value = to_uuid(value)
        elif column in TIMESTAMP_COLUMNS:
            value = to_datetime(value)
        elif column == "showInput":
            # The column is TEXT: the wire value may be a bool, and "false"
            # is load-bearing further down (it hides the step's input).
            value = None if value is None else str(value).lower()
        values[column] = value
    return values


class ChainlitService:
    """What the five services share: how a statement runs.

    Almost every rule in this package is a Core statement rather than a
    repository call, which is deliberate -- the rules have to run inside the
    database. But ``session.execute`` sits *outside* the repository, and
    advanced_alchemy's error translation lives inside it: only repository
    methods apply ``wrap_sqlalchemy_exception``. Executing through here puts
    it back, so a foreign key violation on ``steps."threadId"`` arrives as
    advanced_alchemy's ``ForeignKeyError`` rather than a raw SQLAlchemy
    error -- which is the only form Litestar's ``exception_to_http_response``
    can turn into a 409 instead of a 500.
    """

    # Bound by SQLAlchemyAsyncRepositoryService.__init__; annotated, never
    # assigned, so nothing here shadows it.
    repository: Any

    # Only for advanced_alchemy's error translation, which keys its taxonomy on
    # the dialect. The statements themselves are PostgreSQL-only and never ask.
    @property
    def dialect(self) -> str:
        return self.repository.session.get_bind().dialect.name

    @contextmanager
    def _translated(self) -> Iterator[None]:
        """advanced_alchemy's translation, with the original error kept.

        The translation is lossy on purpose -- an HTTP client gets "There was
        an issue processing the statement", not the SQL -- but that line was
        also all the *server* log had, and a 409 that hides whether Postgres
        refused the statement or asyncpg never sent it cannot be diagnosed
        after the fact. Expected outcomes (no row, too many rows) are the
        caller's business and stay quiet.
        """
        try:
            with exceptions.wrap_sqlalchemy_exception(dialect_name=self.dialect):
                yield
        except exceptions.RepositoryError as error:
            cause = error.__cause__ or error
            # `.one()` on an empty result is an answer, not a failure; the
            # translation files it under InvalidRequestError, so it is told
            # apart by its cause rather than by its translated type.
            if isinstance(cause, NoResultFound | MultipleResultsFound):
                raise
            logger.error(
                "Database statement failed: %s: %s",
                type(cause).__name__,
                cause,
                exc_info=cause,
            )
            raise

    async def execute(self, statement: Executable) -> Result[Any]:
        with self._translated():
            return await self.repository.session.execute(statement)

    # Reading the result is inside the wrap too, and that is the whole point
    # of these three. `NoResultFound` and `MultipleResultsFound` are raised by
    # `.one()` / `.one_or_none()`, not by `execute`, so a caller that executes
    # here and then unwraps outside gets the raw SQLAlchemy error back --
    # exactly the taxonomy this class exists to close.

    async def fetch_one(self, statement: Executable) -> Any:
        with self._translated():
            result = await self.repository.session.execute(statement)
            return result.one()

    async def fetch_one_or_none(self, statement: Executable) -> Optional[Any]:
        with self._translated():
            result = await self.repository.session.execute(statement)
            return result.one_or_none()

    async def fetch_scalar(self, statement: Executable) -> Any:
        with self._translated():
            result = await self.repository.session.execute(statement)
            return result.scalar_one_or_none()


class UserService(
    ChainlitService, service.SQLAlchemyAsyncRepositoryService[User, UserRepository]
):
    """Users, keyed on their identifier."""

    repository_type = UserRepository

    async def get_by_identifier(self, identifier: str) -> Optional[UserRecord]:
        row = await self.repository.get_one_or_none(identifier=identifier)
        return None if row is None else self.to_record(row)

    async def save(
        self, identifier: str, metadata: Optional[Dict[str, Any]] = None
    ) -> UserRecord:
        """Create the user, or refresh the metadata of the existing one.

        One statement, so two logins racing each other cannot both decide the
        row is missing and then collide on the unique identifier.
        """
        statement = statements.upsert_user(
            user_id=uuid.uuid4(),
            identifier=identifier,
            metadata=metadata or {},
            created_at=now(),
        )
        return row_to_user(await self.fetch_one(statement))

    def to_record(self, model: User) -> UserRecord:
        return UserRecord(
            id=str(model.id),
            identifier=model.identifier,
            created_at=from_datetime(model.created_at) or "",
            metadata=model.metadata_ or {},
        )


class StepService(
    ChainlitService, service.SQLAlchemyAsyncRepositoryService[Step, StepRepository]
):
    """Steps, written by a conditional upsert."""

    repository_type = StepRepository

    async def save(self, record: StepRecord) -> None:
        """Write the columns the caller provided, and only those.

        A streaming token touches ``output``; the step's ``start`` and its
        ``type`` were settled by an earlier write and must survive this one.

        The thread is guaranteed first. ``steps."threadId"`` is a real foreign
        key, steps routinely arrive before the thread patch that would create
        the row, and without the guard PostgreSQL rejects the whole write.
        """
        values = _column_values(record, skip=("feedback",))
        await self.execute(statements.ensure_thread(values["threadId"], now()))
        await self.execute(statements.upsert_step(values))

    async def fetch(self, step_id: str) -> Optional[StepRecord]:
        identifier = to_uuid(step_id)
        assert identifier is not None
        row = await self.fetch_one_or_none(statements.step_query(identifier))
        return None if row is None else row_to_step(row)

    async def remove(self, step_id: str) -> None:
        """Remove a step and everything hanging off it."""
        identifier = to_uuid(step_id)
        await self.execute(delete(FEEDBACKS).where(FEEDBACKS.c["forId"] == identifier))
        await self.execute(delete(ELEMENTS).where(ELEMENTS.c["forId"] == identifier))
        await self.execute(delete(STEPS).where(STEPS.c["id"] == identifier))


class ElementService(
    ChainlitService,
    service.SQLAlchemyAsyncRepositoryService[Element, ElementRepository],
):
    """Elements attached to steps."""

    repository_type = ElementRepository

    async def save(self, record: ElementRecord) -> None:
        values = _column_values(record)
        await self.execute(statements.upsert_element(values))

    async def fetch(self, thread_id: str, element_id: str) -> Optional[ElementRecord]:
        table = ELEMENTS
        row = await self.fetch_one_or_none(
            select(*table.c).where(
                table.c["id"] == to_uuid(element_id),
                table.c["threadId"] == to_uuid(thread_id),
            )
        )
        return None if row is None else row_to_element(row)

    async def remove(self, element_id: str, thread_id: Optional[str] = None) -> None:
        table = ELEMENTS
        statement = delete(table).where(table.c["id"] == to_uuid(element_id))
        if thread_id is not None:
            statement = statement.where(table.c["threadId"] == to_uuid(thread_id))
        await self.execute(statement)


class FeedbackService(
    ChainlitService,
    service.SQLAlchemyAsyncRepositoryService[Feedback, FeedbackRepository],
):
    """Thumbs up/down on a step."""

    repository_type = FeedbackRepository

    async def save(self, record: FeedbackRecord) -> str:
        feedback_id = record.id or str(uuid.uuid4())
        values = {
            "id": to_uuid(feedback_id),
            "forId": to_uuid(record.for_id),
            "threadId": to_uuid(record.thread_id),
            "value": record.value,
            "comment": record.comment,
        }
        surviving = await self.fetch_scalar(statements.upsert_feedback(values))
        return str(surviving)

    async def remove(self, feedback_id: str) -> bool:
        table = FEEDBACKS
        result = await self.execute(
            delete(table).where(table.c["id"] == to_uuid(feedback_id))
        )
        # execute() is typed as Result; a DML statement always yields a
        # CursorResult, which is the half that counts rows.
        return bool(cast("CursorResult[Any]", result).rowcount)


class ThreadService(
    ChainlitService, service.SQLAlchemyAsyncRepositoryService[Thread, ThreadRepository]
):
    """Threads: the history page, and the row every step hangs off."""

    repository_type = ThreadRepository

    async def fetch(self, thread_id: str) -> Optional[ThreadRecord]:
        table = THREADS
        row = await self.fetch_one_or_none(
            select(*table.c).where(table.c["id"] == to_uuid(thread_id))
        )
        return None if row is None else row_to_thread(row)

    async def get_author(self, thread_id: str) -> Optional[str]:
        table = THREADS
        return await self.fetch_scalar(
            select(table.c["userIdentifier"]).where(table.c["id"] == to_uuid(thread_id))
        )

    async def patch(self, thread_id: str, patch: ThreadPatch) -> None:
        """Create the thread if it is new, then apply the provided fields.

        Metadata is the one field that is merged rather than overwritten, and
        it is merged in the database — see ``merge_thread_metadata``.
        """
        identifier = to_uuid(thread_id)
        assert identifier is not None
        moment = now()

        values = _column_values(patch, skip=("metadata",))
        insert_values: Dict[str, Any] = {
            "id": identifier,
            "createdAt": moment,
            "updatedAt": moment,
            **values,
        }
        assignments: Dict[str, Any] = {"updatedAt": moment, **values}

        statement = insert(THREADS).values(**insert_values)
        await self.execute(
            statement.on_conflict_do_update(
                index_elements=[THREADS.c["id"]], set_=assignments
            )
        )

        if patch.metadata is not UNSET:
            await self.execute(
                statements.merge_thread_metadata(
                    thread_id=identifier,
                    patch=patch.metadata,
                    updated_at=moment,
                )
            )

    async def touch(self, thread_id: str) -> None:
        """Mark the thread as active without changing anything else."""
        await self.patch(thread_id, ThreadPatch())

    async def get_detail(self, thread_id: str) -> Optional[ThreadDetail]:
        """The thread plus everything needed to resume it."""
        summary = await self.fetch(thread_id)
        if summary is None:
            return None
        identifier = to_uuid(thread_id)
        assert identifier is not None

        step_rows = (
            await self.execute(statements.thread_steps_query([identifier]))
        ).all()
        element_rows = (
            await self.execute(statements.thread_elements_query([identifier]))
        ).all()

        # ThreadDetail is ThreadRecord plus the two lists, so the summary's
        # fields are the detail's fields; listing them again is how the two
        # drift apart.
        return ThreadDetail(
            **asdict(summary),
            steps=[row_to_step(row) for row in step_rows],
            elements=[row_to_element(row) for row in element_rows],
        )

    async def page(self, query: ThreadQuery) -> ThreadPage:
        """One keyset page of the history, newest activity first."""
        rows = (await self.execute(statements.thread_page_query(query))).all()

        # The clamped size, not query.first: the statement was built with the
        # same clamp, so trimming to the raw value would throw away rows the
        # LIMIT deliberately fetched.
        first = statements.page_size(query)
        has_next_page = len(rows) > first
        if has_next_page:
            rows = rows[:first]

        start_cursor, end_cursor = statements.cursors_for(rows)
        return ThreadPage(
            page_info=PageInfoRecord(
                has_next_page=has_next_page,
                start_cursor=start_cursor,
                end_cursor=end_cursor,
            ),
            data=[row_to_thread(row) for row in rows],
        )

    async def remove(self, thread_id: str) -> None:
        """Delete a thread and its steps, elements and feedbacks.

        The children are deleted explicitly rather than left to ON DELETE
        CASCADE: the constraints in the deployed database were created by a
        different tool and cannot be assumed to cascade.
        """
        identifier = to_uuid(thread_id)
        step_ids = select(STEPS.c["id"]).where(STEPS.c["threadId"] == identifier)
        await self.execute(delete(FEEDBACKS).where(FEEDBACKS.c["forId"].in_(step_ids)))
        await self.execute(
            delete(FEEDBACKS).where(FEEDBACKS.c["threadId"] == identifier)
        )
        await self.execute(delete(ELEMENTS).where(ELEMENTS.c["threadId"] == identifier))
        await self.execute(delete(STEPS).where(STEPS.c["threadId"] == identifier))
        await self.execute(delete(THREADS).where(THREADS.c["id"] == identifier))


def row_to_user(row: Row[Any]) -> UserRecord:
    """A ``users`` row -- what the RETURNING upsert hands back."""
    mapping: RowMapping = row._mapping
    return UserRecord(
        id=str(mapping["id"]),
        identifier=mapping["identifier"],
        created_at=from_datetime(mapping["createdAt"]) or "",
        metadata=mapping["metadata"] or {},
    )


def row_to_thread(row: Row[Any]) -> ThreadRecord:
    """A ``threads`` row as the record the history page renders."""
    mapping: RowMapping = row._mapping
    return ThreadRecord(
        id=str(mapping["id"]),
        created_at=from_datetime(mapping["createdAt"]),
        updated_at=from_datetime(mapping["updatedAt"]),
        name=mapping["name"],
        user_id=from_uuid(mapping["userId"]),
        user_identifier=mapping["userIdentifier"],
        tags=mapping["tags"],
        metadata=mapping["metadata"] or {},
        parent_thread_id=from_uuid(mapping["parentThreadId"]),
    )


def row_to_step(row: Row[Any]) -> StepRecord:
    """A ``steps`` row, with its feedback if the query joined one in."""
    mapping: RowMapping = row._mapping
    feedback: Optional[FeedbackRecord] = None
    if mapping.get("feedbackValue") is not None:
        feedback = FeedbackRecord(
            id=from_uuid(mapping.get("feedbackId")),
            for_id=str(mapping["id"]),
            value=mapping["feedbackValue"],
            comment=mapping.get("feedbackComment"),
        )

    show_input = mapping["showInput"]
    return StepRecord(
        id=str(mapping["id"]),
        name=mapping["name"],
        type=mapping["type"],
        thread_id=str(mapping["threadId"]),
        parent_id=from_uuid(mapping["parentId"]),
        command=mapping["command"],
        modes=mapping["modes"],
        streaming=bool(mapping["streaming"]),
        wait_for_answer=mapping["waitForAnswer"],
        is_error=mapping["isError"],
        metadata=mapping["metadata"] or {},
        tags=mapping["tags"],
        # An input the sender asked not to show is not sent at all: it is
        # frequently the raw prompt, and the UI has no way to hide what it
        # has already received.
        input=mapping["input"] if show_input not in (None, "false") else "",
        output=mapping["output"] or "",
        created_at=from_datetime(mapping["createdAt"]),
        start=from_datetime(mapping["start"]),
        end=from_datetime(mapping["end"]),
        generation=mapping["generation"],
        show_input=show_input,
        default_open=mapping["defaultOpen"],
        auto_collapse=mapping["autoCollapse"],
        language=mapping["language"],
        indent=mapping["indent"],
        feedback=feedback,
    )


def row_to_element(row: Row[Any]) -> ElementRecord:
    """An ``elements`` row as the record the UI renders."""
    mapping: RowMapping = row._mapping
    return ElementRecord(
        id=str(mapping["id"]),
        thread_id=from_uuid(mapping["threadId"]),
        type=mapping["type"] or "file",
        chainlit_key=mapping["chainlitKey"],
        url=mapping["url"],
        object_key=mapping["objectKey"],
        name=mapping["name"],
        display=mapping["display"],
        size=mapping["size"],
        language=mapping["language"],
        page=mapping["page"],
        props=mapping["props"],
        auto_play=mapping["autoPlay"],
        player_config=mapping["playerConfig"],
        for_id=from_uuid(mapping["forId"]),
        mime=mapping["mime"],
    )


__all__: List[str] = [
    "ElementService",
    "FeedbackService",
    "StepService",
    "ThreadService",
    "UserService",
]
