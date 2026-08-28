"""SQLAlchemy models mapped onto the deployed chainlit schema.

The schema this maps was created by the legacy ``SQLAlchemyDataLayer`` and is
live in production, so the mapping is written to fit it rather than the other
way round: lowercase table names, quoted camelCase columns, native ``uuid``
keys, and timestamps kept as ISO **text** with a literal trailing ``Z``.

Nothing here inherits from advanced_alchemy's ``UUIDBase``/audit mixins: those
hardcode snake_case ``created_at``/``updated_at`` columns, which this schema
does not have.
"""

from datetime import UTC, datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from advanced_alchemy.base import BasicAttributes
from advanced_alchemy.types import JsonB
from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    Dialect,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    TypeDecorator,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA_NAME = "chainlit"

# Constraint names are generated, not hand-written, so that alembic's
# autogenerate can name an index the same way twice running.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# text[] on PostgreSQL; SQLite has no array type, so the test/dev variant
# stores the same list as JSON.
TagArray = ARRAY(Text()).with_variant(JSON(), "sqlite")


def iso_text(value: Optional[datetime]) -> Optional[str]:
    """Render a datetime the way this schema's TEXT columns hold it.

    The one place the format is written down: the column type below, the
    records the services hand out and the page cursors all go through here,
    so a cursor minted from a row compares byte-for-byte with the stored value.
    """
    if value is None:
        return None
    # A naive datetime is taken as UTC: the legacy writer used local time
    # with a "Z" glued on, so there is no offset to recover anyway.
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return (
        moment.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds")
        + "Z"
    )


def iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse one of those strings back, tolerating the trailing Z.

    Rows written before this package existed are not guaranteed to parse; a
    malformed value reads back as ``None`` rather than breaking the whole page
    of results.
    """
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class ISOTimestamp(TypeDecorator[datetime]):
    """A datetime stored as the ISO text this schema already holds.

    The deployed columns are ``TEXT`` carrying ``2026-08-27T10:11:12.131415Z``.
    Rewriting them to ``timestamptz`` would be a migration over live data, so
    the type decorator absorbs the format instead.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(
        self, value: Optional[datetime], dialect: Dialect
    ) -> Optional[str]:
        return iso_text(value)

    def process_result_value(
        self, value: Optional[str], dialect: Dialect
    ) -> Optional[datetime]:
        return iso_datetime(value)


class Base(BasicAttributes, DeclarativeBase):
    """Declarative base pinned to the ``chainlit`` schema.

    ``BasicAttributes`` is advanced_alchemy's plain mixin — TYPE_CHECKING
    declarations and a ``to_dict``, no columns and no ``__tablename__``
    inference. It is what makes these models satisfy AA's ``ModelProtocol``
    without dragging in the audit mixins that would insist on snake_case
    ``created_at``/``updated_at`` columns this schema does not have.
    """

    metadata = MetaData(schema=SCHEMA_NAME, naming_convention=NAMING_CONVENTION)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column("id", Uuid(), primary_key=True)
    identifier: Mapped[str] = mapped_column(
        "identifier", Text(), nullable=False, unique=True
    )
    # `metadata` is taken by DeclarativeBase, hence the trailing underscore on
    # the attribute; the column keeps its name.
    metadata_: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JsonB, nullable=False, default=dict
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        "createdAt", ISOTimestamp(), nullable=True
    )


class Thread(Base):
    __tablename__ = "threads"
    # Index names are the ones the database already carries, not the ones the
    # naming convention would mint, so autogenerate sees no drift.
    __table_args__ = (
        Index("threads_parent_thread_id_idx", "parentThreadId"),
        Index("threads_user_id_idx", "userId"),
        Index("threads_user_id_updated_at_idx", "userId", "updatedAt", "id"),
    )

    id: Mapped[UUID] = mapped_column("id", Uuid(), primary_key=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        "createdAt", ISOTimestamp(), nullable=True
    )
    # Added by migration 0002. The history page orders by it, so the thread
    # service writes it on every mutation instead of deriving it from steps.
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        "updatedAt", ISOTimestamp(), nullable=True
    )
    name: Mapped[Optional[str]] = mapped_column("name", Text(), nullable=True)
    user_id: Mapped[Optional[UUID]] = mapped_column(
        "userId",
        Uuid(),
        ForeignKey(f"{SCHEMA_NAME}.users.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_identifier: Mapped[Optional[str]] = mapped_column(
        "userIdentifier", Text(), nullable=True
    )
    tags: Mapped[Optional[List[str]]] = mapped_column("tags", TagArray, nullable=True)
    # Nullable with no server default, matching production. Upstream's documented
    # DDL says `JSONB NOT NULL DEFAULT '{}'` and the deployed schema disagrees --
    # declaring it NOT NULL here makes `alembic check` against prod non-empty,
    # which is exactly the acceptance gate for these models. Reads coerce None.
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata", JsonB, nullable=True, default=dict
    )
    parent_thread_id: Mapped[Optional[UUID]] = mapped_column(
        "parentThreadId",
        Uuid(),
        ForeignKey(f"{SCHEMA_NAME}.threads.id", ondelete="SET NULL"),
        nullable=True,
    )

    # lazy="raise" everywhere: an accidental lazy load inside an async session
    # raises MissingGreenlet at runtime, which is a worse failure than the
    # explicit error you get here.
    steps: Mapped[List["Step"]] = relationship(
        back_populates="thread", lazy="raise", viewonly=True
    )
    elements: Mapped[List["Element"]] = relationship(
        back_populates="thread", lazy="raise", viewonly=True
    )


class Step(Base):
    __tablename__ = "steps"
    __table_args__ = (
        Index("steps_thread_id_idx", "threadId"),
        Index("steps_parent_id_idx", "parentId"),
    )

    id: Mapped[UUID] = mapped_column("id", Uuid(), primary_key=True)
    # NOT NULL in the deployed schema, but a partial write — a streaming
    # token, say — carries no name. The default only applies to the INSERT
    # half of the upsert, so an existing name is never overwritten by it.
    name: Mapped[str] = mapped_column("name", Text(), nullable=False, default="")
    type: Mapped[str] = mapped_column("type", Text(), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(
        "threadId",
        Uuid(),
        ForeignKey(f"{SCHEMA_NAME}.threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[Optional[UUID]] = mapped_column("parentId", Uuid(), nullable=True)
    streaming: Mapped[bool] = mapped_column(
        "streaming", Boolean(), nullable=False, default=False
    )
    wait_for_answer: Mapped[Optional[bool]] = mapped_column(
        "waitForAnswer", Boolean(), nullable=True
    )
    is_error: Mapped[Optional[bool]] = mapped_column(
        "isError", Boolean(), nullable=True
    )
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata", JsonB, nullable=True
    )
    tags: Mapped[Optional[List[str]]] = mapped_column("tags", TagArray, nullable=True)
    input: Mapped[Optional[str]] = mapped_column("input", Text(), nullable=True)
    output: Mapped[Optional[str]] = mapped_column("output", Text(), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        "createdAt", ISOTimestamp(), nullable=True
    )
    command: Mapped[Optional[str]] = mapped_column("command", Text(), nullable=True)
    start: Mapped[Optional[datetime]] = mapped_column(
        "start", ISOTimestamp(), nullable=True
    )
    end: Mapped[Optional[datetime]] = mapped_column(
        "end", ISOTimestamp(), nullable=True
    )
    generation: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "generation", JsonB, nullable=True
    )
    # Stored as text, not boolean: the wire value is either a bool or the name
    # of a renderer ("json", "python", ...).
    show_input: Mapped[Optional[str]] = mapped_column(
        "showInput", Text(), nullable=True
    )
    language: Mapped[Optional[str]] = mapped_column("language", Text(), nullable=True)
    indent: Mapped[Optional[int]] = mapped_column("indent", Integer(), nullable=True)
    default_open: Mapped[Optional[bool]] = mapped_column(
        "defaultOpen", Boolean(), nullable=True
    )
    modes: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "modes", JsonB, nullable=True
    )
    auto_collapse: Mapped[Optional[bool]] = mapped_column(
        "autoCollapse", Boolean(), nullable=True
    )

    thread: Mapped["Thread"] = relationship(
        back_populates="steps", lazy="raise", viewonly=True
    )
    feedback: Mapped[Optional["Feedback"]] = relationship(
        back_populates="step", lazy="raise", uselist=False, viewonly=True
    )


class Element(Base):
    __tablename__ = "elements"
    __table_args__ = (
        Index("elements_thread_id_idx", "threadId"),
        Index("elements_for_id_idx", "forId"),
    )

    id: Mapped[UUID] = mapped_column("id", Uuid(), primary_key=True)
    thread_id: Mapped[Optional[UUID]] = mapped_column(
        "threadId",
        Uuid(),
        ForeignKey(f"{SCHEMA_NAME}.threads.id", ondelete="CASCADE"),
        nullable=True,
    )
    type: Mapped[Optional[str]] = mapped_column("type", Text(), nullable=True)
    url: Mapped[Optional[str]] = mapped_column("url", Text(), nullable=True)
    chainlit_key: Mapped[Optional[str]] = mapped_column(
        "chainlitKey", Text(), nullable=True
    )
    name: Mapped[str] = mapped_column("name", Text(), nullable=False)
    display: Mapped[Optional[str]] = mapped_column("display", Text(), nullable=True)
    object_key: Mapped[Optional[str]] = mapped_column(
        "objectKey", Text(), nullable=True
    )
    size: Mapped[Optional[str]] = mapped_column("size", Text(), nullable=True)
    page: Mapped[Optional[int]] = mapped_column("page", Integer(), nullable=True)
    language: Mapped[Optional[str]] = mapped_column("language", Text(), nullable=True)
    for_id: Mapped[Optional[UUID]] = mapped_column("forId", Uuid(), nullable=True)
    mime: Mapped[Optional[str]] = mapped_column("mime", Text(), nullable=True)
    props: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "props", JsonB, nullable=True
    )
    # Added by migration 0002 — part of the element contract the frontend
    # already sends, dropped on the floor by the deployed schema.
    auto_play: Mapped[Optional[bool]] = mapped_column(
        "autoPlay", Boolean(), nullable=True
    )
    player_config: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "playerConfig", JsonB, nullable=True
    )

    thread: Mapped[Optional["Thread"]] = relationship(
        back_populates="elements", lazy="raise", viewonly=True
    )


class Feedback(Base):
    __tablename__ = "feedbacks"
    __table_args__ = (
        # Unique: a step has one piece of feedback, and both readers join on
        # this column expecting exactly that. See migration 0003.
        Index("feedbacks_for_id_idx", "forId", unique=True),
        Index("feedbacks_thread_id_idx", "threadId"),
    )

    id: Mapped[UUID] = mapped_column("id", Uuid(), primary_key=True)
    for_id: Mapped[UUID] = mapped_column(
        "forId",
        Uuid(),
        ForeignKey(f"{SCHEMA_NAME}.steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[UUID] = mapped_column(
        "threadId",
        Uuid(),
        ForeignKey(f"{SCHEMA_NAME}.threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    value: Mapped[int] = mapped_column("value", Integer(), nullable=False)
    comment: Mapped[Optional[str]] = mapped_column("comment", Text(), nullable=True)

    step: Mapped["Step"] = relationship(
        back_populates="feedback", lazy="raise", viewonly=True
    )


# The Table objects, for the Core statements. Reaching through ``__table__``
# instead types as a bare FromClause, which has no ``update()`` and cannot be
# handed to ``delete()``.
USERS: Table = Base.metadata.tables[f"{SCHEMA_NAME}.users"]
THREADS: Table = Base.metadata.tables[f"{SCHEMA_NAME}.threads"]
STEPS: Table = Base.metadata.tables[f"{SCHEMA_NAME}.steps"]
ELEMENTS: Table = Base.metadata.tables[f"{SCHEMA_NAME}.elements"]
FEEDBACKS: Table = Base.metadata.tables[f"{SCHEMA_NAME}.feedbacks"]
