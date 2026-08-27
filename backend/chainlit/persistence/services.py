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
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Sequence, cast

from advanced_alchemy.service import SQLAlchemyAsyncRepositoryService
from msgspec import UNSET, Struct
from msgspec.structs import fields
from sqlalchemy import CursorResult, Row, RowMapping, delete, select

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


def to_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    """Parse an id coming off the wire."""
    if value is None:
        return None
    return uuid.UUID(value)


def from_uuid(value: Optional[uuid.UUID]) -> Optional[str]:
    """Render an id for the wire."""
    return None if value is None else str(value)


def to_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp, tolerating the trailing Z this schema uses."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def from_datetime(value: Optional[datetime]) -> Optional[str]:
    """Render a timestamp the way the stored strings are written."""
    if value is None:
        return None
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return (
        moment.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds")
        + "Z"
    )


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


class UserService(SQLAlchemyAsyncRepositoryService[User, UserRepository]):
    """Users, keyed on their identifier."""

    repository_type = UserRepository

    @property
    def dialect(self) -> str:
        return self.repository.session.get_bind().dialect.name

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
            dialect_name=self.dialect,
        )
        result = await self.repository.session.execute(statement)
        return self.row_to_record(result.one())

    def to_record(self, model: User) -> UserRecord:
        return UserRecord(
            id=str(model.id),
            identifier=model.identifier,
            created_at=from_datetime(model.created_at) or "",
            metadata=model.metadata_ or {},
        )

    def row_to_record(self, row: Row[Any]) -> UserRecord:
        mapping: RowMapping = row._mapping
        return UserRecord(
            id=str(mapping["id"]),
            identifier=mapping["identifier"],
            created_at=from_datetime(mapping["createdAt"]) or "",
            metadata=mapping["metadata"] or {},
        )


class StepService(SQLAlchemyAsyncRepositoryService[Step, StepRepository]):
    """Steps, written by a conditional upsert."""

    repository_type = StepRepository

    @property
    def dialect(self) -> str:
        return self.repository.session.get_bind().dialect.name

    async def save(self, record: StepRecord) -> None:
        """Write the columns the caller provided, and only those.

        A streaming token touches ``output``; the step's ``start`` and its
        ``type`` were settled by an earlier write and must survive this one.
        """
        values = _column_values(record, skip=("feedback",))
        await self.repository.session.execute(
            statements.upsert_step(values, self.dialect)
        )

    async def fetch(self, step_id: str) -> Optional[StepRecord]:
        identifier = to_uuid(step_id)
        assert identifier is not None
        result = await self.repository.session.execute(
            statements.step_query(identifier)
        )
        row = result.one_or_none()
        return None if row is None else row_to_step(row)

    async def remove(self, step_id: str) -> None:
        """Remove a step and everything hanging off it."""
        identifier = to_uuid(step_id)
        session = self.repository.session
        await session.execute(
            delete(FEEDBACKS).where(FEEDBACKS.c["forId"] == identifier)
        )
        await session.execute(delete(ELEMENTS).where(ELEMENTS.c["forId"] == identifier))
        await session.execute(delete(STEPS).where(STEPS.c["id"] == identifier))


class ElementService(SQLAlchemyAsyncRepositoryService[Element, ElementRepository]):
    """Elements attached to steps."""

    repository_type = ElementRepository

    @property
    def dialect(self) -> str:
        return self.repository.session.get_bind().dialect.name

    async def save(self, record: ElementRecord) -> None:
        values = _column_values(record)
        await self.repository.session.execute(
            statements.upsert_element(values, self.dialect)
        )

    async def fetch(self, thread_id: str, element_id: str) -> Optional[ElementRecord]:
        table = ELEMENTS
        result = await self.repository.session.execute(
            select(*table.c).where(
                table.c["id"] == to_uuid(element_id),
                table.c["threadId"] == to_uuid(thread_id),
            )
        )
        row = result.one_or_none()
        return None if row is None else row_to_element(row)

    async def remove(self, element_id: str, thread_id: Optional[str] = None) -> None:
        table = ELEMENTS
        statement = delete(table).where(table.c["id"] == to_uuid(element_id))
        if thread_id is not None:
            statement = statement.where(table.c["threadId"] == to_uuid(thread_id))
        await self.repository.session.execute(statement)


class FeedbackService(SQLAlchemyAsyncRepositoryService[Feedback, FeedbackRepository]):
    """Thumbs up/down on a step."""

    repository_type = FeedbackRepository

    @property
    def dialect(self) -> str:
        return self.repository.session.get_bind().dialect.name

    async def save(self, record: FeedbackRecord) -> str:
        feedback_id = record.id or str(uuid.uuid4())
        values = {
            "id": to_uuid(feedback_id),
            "forId": to_uuid(record.for_id),
            "threadId": to_uuid(record.thread_id),
            "value": record.value,
            "comment": record.comment,
        }
        await self.repository.session.execute(
            statements.upsert_feedback(values, self.dialect)
        )
        return feedback_id

    async def remove(self, feedback_id: str) -> bool:
        table = FEEDBACKS
        result = await self.repository.session.execute(
            delete(table).where(table.c["id"] == to_uuid(feedback_id))
        )
        # execute() is typed as Result; a DML statement always yields a
        # CursorResult, which is the half that counts rows.
        return bool(cast("CursorResult[Any]", result).rowcount)


class ThreadService(SQLAlchemyAsyncRepositoryService[Thread, ThreadRepository]):
    """Threads: the history page, and the row every step hangs off."""

    repository_type = ThreadRepository

    @property
    def dialect(self) -> str:
        return self.repository.session.get_bind().dialect.name

    async def fetch(self, thread_id: str) -> Optional[ThreadRecord]:
        table = THREADS
        result = await self.repository.session.execute(
            select(*table.c).where(table.c["id"] == to_uuid(thread_id))
        )
        row = result.one_or_none()
        return None if row is None else row_to_thread(row)

    async def get_author(self, thread_id: str) -> Optional[str]:
        table = THREADS
        result = await self.repository.session.execute(
            select(table.c["userIdentifier"]).where(table.c["id"] == to_uuid(thread_id))
        )
        return result.scalar_one_or_none()

    async def patch(self, thread_id: str, patch: ThreadPatch) -> None:
        """Create the thread if it is new, then apply the provided fields.

        Metadata is the one field that is merged rather than overwritten, and
        it is merged in the database — see ``merge_thread_metadata``.
        """
        identifier = to_uuid(thread_id)
        assert identifier is not None
        moment = now()
        session = self.repository.session

        values = _column_values(patch, skip=("metadata",))
        insert_values: Dict[str, Any] = {
            "id": identifier,
            "createdAt": moment,
            "updatedAt": moment,
            **values,
        }
        assignments: Dict[str, Any] = {"updatedAt": moment, **values}

        statement = statements.insert_for(self.dialect)(THREADS).values(**insert_values)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[THREADS.c["id"]], set_=assignments
            )
        )

        if patch.metadata is not UNSET:
            await session.execute(
                statements.merge_thread_metadata(
                    thread_id=identifier,
                    patch=patch.metadata,
                    updated_at=moment,
                    dialect_name=self.dialect,
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
        session = self.repository.session

        step_rows = (
            await session.execute(statements.thread_steps_query([identifier]))
        ).all()
        element_rows = (
            await session.execute(statements.thread_elements_query([identifier]))
        ).all()

        return ThreadDetail(
            id=summary.id,
            created_at=summary.created_at,
            updated_at=summary.updated_at,
            name=summary.name,
            user_id=summary.user_id,
            user_identifier=summary.user_identifier,
            tags=summary.tags,
            metadata=summary.metadata,
            parent_thread_id=summary.parent_thread_id,
            steps=[row_to_step(row) for row in step_rows],
            elements=[row_to_element(row) for row in element_rows],
        )

    async def page(self, query: ThreadQuery) -> ThreadPage:
        """One keyset page of the history, newest activity first."""
        rows = (
            await self.repository.session.execute(statements.thread_page_query(query))
        ).all()

        has_next_page = len(rows) > query.first
        if has_next_page:
            rows = rows[: query.first]

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
        session = self.repository.session
        step_ids = select(STEPS.c["id"]).where(STEPS.c["threadId"] == identifier)
        await session.execute(
            delete(FEEDBACKS).where(FEEDBACKS.c["forId"].in_(step_ids))
        )
        await session.execute(
            delete(FEEDBACKS).where(FEEDBACKS.c["threadId"] == identifier)
        )
        await session.execute(
            delete(ELEMENTS).where(ELEMENTS.c["threadId"] == identifier)
        )
        await session.execute(delete(STEPS).where(STEPS.c["threadId"] == identifier))
        await session.execute(delete(THREADS).where(THREADS.c["id"] == identifier))


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
