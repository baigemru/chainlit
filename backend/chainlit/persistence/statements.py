"""Core statements that carry the persistence rules.

Everything here is built with the SQLAlchemy expression language rather than
raw SQL, so the same rule compiles for PostgreSQL (production) and SQLite
(tests, and a single-file dev run) without a second implementation to keep in
sync.

The interesting one is the step upsert. Its legacy ancestor could not tell an
omitted column from an empty one, so it defended itself with a wall of
``COALESCE(NULLIF(EXCLUDED.x, ''), "Step".x)``, which also meant a caller
could never deliberately clear a field. Here the caller says what it is
writing — ``StepRecord`` leaves everything else ``UNSET`` — and the statement
only mentions the columns it was given.
"""

import json
from datetime import datetime
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    Select,
    Table,
    Text,
    Update,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as postgres_insert
from sqlalchemy.dialects.postgresql.dml import Insert as PostgresInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.sqlite.dml import Insert as SQLiteInsert
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.dml import ReturningInsert
from sqlalchemy.sql.functions import FunctionElement

from chainlit.persistence.models import ELEMENTS, FEEDBACKS, STEPS, THREADS, USERS
from chainlit.persistence.records import ThreadQuery

# The type a step gets when it is created as a stand-in for a parent that has
# not arrived yet. A later write carrying the real type must win, and a late
# placeholder must not clobber a type that is already there.
PLACEHOLDER_STEP_TYPE = "run"

# Only the dialects that carry ON CONFLICT. Both classes expose ``excluded``
# and the two ``on_conflict_*`` methods; the generic ``sqlalchemy.Insert``
# exposes neither, which is why the union is spelled out.
DialectInsert = Union[PostgresInsert, SQLiteInsert]


class Least(FunctionElement[Any]):
    """``LEAST(a, b)``, with a SQLite fallback.

    SQLite has no LEAST. Its scalar ``min()`` is close but not equal: it
    returns NULL when *either* argument is NULL, where LEAST skips NULLs. The
    difference matters here — the first write of a step often has no stored
    ``start`` to compare against, and a naive ``min()`` would throw the
    incoming value away.
    """

    name = "least"
    inherit_cache = True
    type = Text()


@compiles(Least)
def _compile_least(element: Least, compiler: SQLCompiler, **kw: Any) -> str:
    return f"least({compiler.process(element.clauses, **kw)})"


@compiles(Least, "sqlite")
def _compile_least_sqlite(element: Least, compiler: SQLCompiler, **kw: Any) -> str:
    clauses = list(element.clauses)
    if len(clauses) != 2:
        raise ValueError("Least() on SQLite takes exactly two arguments")
    left = compiler.process(clauses[0], **kw)
    right = compiler.process(clauses[1], **kw)
    return f"min(coalesce({left}, {right}), coalesce({right}, {left}))"


def insert_for(dialect_name: str) -> Callable[[Table], DialectInsert]:
    """Pick the dialect-specific INSERT that knows about ON CONFLICT."""
    if dialect_name == "sqlite":
        return sqlite_insert
    if dialect_name in {"postgresql", "cockroachdb"}:
        return postgres_insert
    raise ValueError(f"Unsupported dialect for upserts: {dialect_name}")


def upsert_step(values: Mapping[str, Any], dialect_name: str) -> DialectInsert:
    """INSERT ... ON CONFLICT (id) DO UPDATE over the provided columns only.

    Two columns are not written straight through:

    * ``start`` keeps the earliest of the two, so a late-arriving update
      cannot move a step's beginning forward;
    * ``type`` refuses a placeholder over a real type.
    """
    statement = insert_for(dialect_name)(STEPS).values(**values)
    excluded = statement.excluded

    assignments: Dict[str, Any] = {}
    for column in values:
        if column == "id":
            continue
        if column == "start":
            assignments[column] = Least(excluded[column], STEPS.c["start"])
        elif column == "type":
            assignments[column] = case(
                (excluded["type"] == PLACEHOLDER_STEP_TYPE, STEPS.c["type"]),
                else_=excluded["type"],
            )
        else:
            assignments[column] = excluded[column]

    if not assignments:
        # Nothing but the primary key was provided: the row must exist, but
        # nothing about it changes.
        return statement.on_conflict_do_nothing(index_elements=[STEPS.c["id"]])
    return statement.on_conflict_do_update(
        index_elements=[STEPS.c["id"]], set_=assignments
    )


def upsert_user(
    user_id: UUID,
    identifier: str,
    metadata: Dict[str, Any],
    created_at: datetime,
    dialect_name: str,
) -> ReturningInsert[Any]:
    """Create or update a user in one statement.

    The legacy layer did SELECT-then-INSERT-or-UPDATE, which two logins racing
    each other turn into a duplicate-key error on the unique identifier. The
    conflict target is ``identifier``, not ``id``: the caller's generated id
    loses to whatever is already stored.
    """
    statement = insert_for(dialect_name)(USERS).values(
        id=user_id,
        identifier=identifier,
        metadata=metadata,
        createdAt=created_at,
    )
    return statement.on_conflict_do_update(
        index_elements=[USERS.c["identifier"]],
        set_={"metadata": statement.excluded["metadata"]},
    ).returning(*USERS.c)


def merge_thread_metadata(
    thread_id: UUID,
    patch: Mapping[str, Any],
    updated_at: Optional[datetime],
    dialect_name: str,
) -> Update:
    """Merge a metadata patch into the stored object, in the database.

    Read-modify-write in Python loses whichever concurrent write finishes
    first, and metadata is where ``user_session`` lives — two tabs of the same
    chat are enough to hit it. A key mapped to ``None`` is deleted; every
    other key is written over.
    """
    column = THREADS.c["metadata"]
    values: Dict[str, Any] = {"metadata": _merged_metadata(column, patch, dialect_name)}
    if updated_at is not None:
        values["updatedAt"] = updated_at

    return THREADS.update().where(THREADS.c["id"] == thread_id).values(**values)


def _merged_metadata(column: Any, patch: Mapping[str, Any], dialect_name: str) -> Any:
    """The merged-metadata expression for one dialect."""
    if dialect_name == "sqlite":
        # RFC 7396 merge-patch: json_patch() deletes exactly the keys whose
        # patch value is null, which is the semantics we want, for free.
        return func.json_patch(
            func.coalesce(column, literal("{}")), literal(json.dumps(dict(patch)))
        )

    # PostgreSQL has no merge-patch before 17, so deletion and addition are
    # two operators: subtract the null-valued keys, concatenate the rest.
    deleted = [key for key, value in patch.items() if value is None]
    incoming = {key: value for key, value in patch.items() if value is not None}
    current = func.coalesce(column, cast(literal("{}"), JSONB))
    kept = current.op("-")(cast(literal(deleted), ARRAY(Text())))
    return kept.op("||")(cast(literal(json.dumps(incoming)), JSONB))


def thread_page_query(query: ThreadQuery) -> Select[Any]:
    """One keyset page of the thread history, newest first.

    Keyset, not OFFSET: the list is sorted by last activity, which changes
    while the user is scrolling, and an offset page would then repeat or skip
    threads. ``(updatedAt, id)`` is compared as a row so that threads sharing
    a timestamp — a resumed thread and the profile switch that spawned it are
    written in the same instant — still have exactly one place in the order.
    """
    statement = select(*THREADS.c)

    conditions: List[Any] = []
    if query.user_id is not None:
        conditions.append(THREADS.c["userId"] == UUID(query.user_id))
    if query.search:
        pattern = f"%{query.search}%"
        conditions.append(
            or_(
                THREADS.c["name"].ilike(pattern),
                exists(
                    select(STEPS.c["id"]).where(
                        and_(
                            STEPS.c["threadId"] == THREADS.c["id"],
                            STEPS.c["output"].ilike(pattern),
                        )
                    )
                ),
            )
        )
    if query.feedback is not None:
        conditions.append(
            exists(
                select(FEEDBACKS.c["id"]).where(
                    and_(
                        FEEDBACKS.c["threadId"] == THREADS.c["id"],
                        FEEDBACKS.c["value"] == query.feedback,
                    )
                )
            )
        )
    if query.cursor is not None:
        cursor_id = UUID(query.cursor)
        cursor_updated_at = (
            select(THREADS.c["updatedAt"])
            .where(THREADS.c["id"] == cursor_id)
            .scalar_subquery()
        )
        conditions.append(
            tuple_(THREADS.c["updatedAt"], THREADS.c["id"])
            < tuple_(cursor_updated_at, literal(cursor_id, THREADS.c["id"].type))
        )

    if conditions:
        statement = statement.where(and_(*conditions))

    # first + 1: the extra row is what answers hasNextPage without a count.
    return statement.order_by(
        THREADS.c["updatedAt"].desc(), THREADS.c["id"].desc()
    ).limit(query.first + 1)


def _steps_with_feedback() -> Select[Any]:
    """Steps left-joined to their feedback — the shape both readers want."""
    return select(
        *STEPS.c,
        FEEDBACKS.c["id"].label("feedbackId"),
        FEEDBACKS.c["value"].label("feedbackValue"),
        FEEDBACKS.c["comment"].label("feedbackComment"),
    ).select_from(STEPS.outerjoin(FEEDBACKS, FEEDBACKS.c["forId"] == STEPS.c["id"]))


def thread_steps_query(thread_ids: Sequence[UUID]) -> Select[Any]:
    """Every step of the given threads, oldest first, with its feedback."""
    return (
        _steps_with_feedback()
        .where(STEPS.c["threadId"].in_(thread_ids))
        .order_by(STEPS.c["createdAt"].asc())
    )


def step_query(step_id: UUID) -> Select[Any]:
    """One step, with its feedback."""
    return _steps_with_feedback().where(STEPS.c["id"] == step_id)


def thread_elements_query(thread_ids: Sequence[UUID]) -> Select[Any]:
    """Every element of the given threads."""
    return select(*ELEMENTS.c).where(ELEMENTS.c["threadId"].in_(thread_ids))


def upsert_element(values: Mapping[str, Any], dialect_name: str) -> DialectInsert:
    """Store an element, overwriting the columns the caller provided."""
    statement = insert_for(dialect_name)(ELEMENTS).values(**values)
    assignments = {
        column: statement.excluded[column] for column in values if column != "id"
    }
    if not assignments:
        return statement.on_conflict_do_nothing(index_elements=[ELEMENTS.c["id"]])
    return statement.on_conflict_do_update(
        index_elements=[ELEMENTS.c["id"]], set_=assignments
    )


def upsert_feedback(values: Mapping[str, Any], dialect_name: str) -> DialectInsert:
    """Store a feedback, keyed on its own id."""
    statement = insert_for(dialect_name)(FEEDBACKS).values(**values)
    return statement.on_conflict_do_update(
        index_elements=[FEEDBACKS.c["id"]],
        set_={
            "value": statement.excluded["value"],
            "comment": statement.excluded["comment"],
        },
    )


def cursors_for(rows: Sequence[Any]) -> Tuple[Optional[str], Optional[str]]:
    """First and last thread id of a page, as the wire cursors."""
    if not rows:
        return None, None
    return str(rows[0].id), str(rows[-1].id)
