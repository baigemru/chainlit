"""Core statements that carry the persistence rules.

PostgreSQL only. Everything here is built with the SQLAlchemy expression
language rather than raw SQL, but the statements lean on what the production
dialect actually has -- ``INSERT ... ON CONFLICT``, ``LEAST``, the ``jsonb``
operators, ``NULLS LAST`` -- and no other dialect is compiled for. The SQLite
arms that used to sit beside these existed for a test-suite that no longer
runs on SQLite; carrying them meant every rule had two implementations to
keep meaning the same thing.

The interesting one is the step upsert. Its legacy ancestor could not tell an
omitted column from an empty one, so it defended itself with a wall of
``COALESCE(NULLIF(EXCLUDED.x, ''), "Step".x)``, which also meant a caller
could never deliberately clear a field. Here the caller says what it is
writing — ``StepRecord`` leaves everything else ``UNSET`` — and the statement
only mentions the columns it was given.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from uuid import UUID

import msgspec
from sqlalchemy import (
    ARRAY,
    Select,
    Text,
    Update,
    and_,
    case,
    cast,
    exists,
    false,
    func,
    literal,
    or_,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql.dml import Insert
from sqlalchemy.sql.dml import ReturningInsert

from chainlit.persistence.models import (
    ELEMENTS,
    FEEDBACKS,
    STEPS,
    THREADS,
    USERS,
    iso_datetime,
    iso_text,
)
from chainlit.persistence.records import (
    MAX_PAGE_SIZE,
    MIN_PAGE_SIZE,
    PageCursor,
    ThreadQuery,
)

# The type a step gets when it is created as a stand-in for a parent that has
# not arrived yet. A later write carrying the real type must win, and a late
# placeholder must not clobber a type that is already there.
PLACEHOLDER_STEP_TYPE = "run"

# LIKE's own metacharacters. Escaped explicitly rather than left to the
# dialect default, which PostgreSQL ties to ``standard_conforming_strings``.
LIKE_ESCAPE = "\\"


def upsert_step(values: Mapping[str, Any]) -> Insert:
    """INSERT ... ON CONFLICT (id) DO UPDATE over the provided columns only.

    Two columns are not written straight through:

    * ``start`` keeps the earliest of the two, so a late-arriving update
      cannot move a step's beginning forward. ``LEAST`` rather than ``min``
      because it skips NULLs: the first write of a step often has no stored
      ``start`` to compare against, and the incoming value must not be
      thrown away for it;
    * ``type`` refuses a placeholder over a real type.

    Both stay even though the writer serialises one session's writes: two
    tabs on one thread are two writers, and their steps still race.
    """
    statement = insert(STEPS).values(**values)
    excluded = statement.excluded

    assignments: Dict[str, Any] = {}
    for column in values:
        if column == "id":
            continue
        if column == "start":
            assignments[column] = func.least(excluded[column], STEPS.c["start"])
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


def ensure_thread(thread_id: UUID, created_at: datetime) -> Insert:
    """Create the thread row if it is missing, and leave it alone if it is not.

    ``steps."threadId"`` is a real foreign key in production. Steps arrive out
    of order — a child before its parent, a message before the thread patch
    that names it — so the write has to bring its own thread or lose the row
    to a ForeignKeyViolationError. The legacy layer opened ``create_step``
    with ``update_thread()`` for the same reason.

    DO NOTHING on conflict, deliberately: an existing thread keeps its
    ``updatedAt``. Marking a thread active is ``ThreadService.touch``'s job,
    and doing it here would reshuffle the history on every streaming token.
    """
    statement = insert(THREADS).values(
        id=thread_id, createdAt=created_at, updatedAt=created_at
    )
    return statement.on_conflict_do_nothing(index_elements=[THREADS.c["id"]])


def upsert_user(
    user_id: UUID,
    identifier: str,
    metadata: Dict[str, Any],
    created_at: datetime,
) -> ReturningInsert[Any]:
    """Create or update a user in one statement.

    The legacy layer did SELECT-then-INSERT-or-UPDATE, which two logins racing
    each other turn into a duplicate-key error on the unique identifier. The
    conflict target is ``identifier``, not ``id``: the caller's generated id
    loses to whatever is already stored.
    """
    statement = insert(USERS).values(
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
) -> Update:
    """Merge a metadata patch into the stored object, in the database.

    Read-modify-write in Python loses whichever concurrent write finishes
    first, and metadata is where ``user_session`` lives — two tabs of the same
    chat are enough to hit it. A key mapped to ``None`` is deleted; every
    other key is written over.
    """
    column = THREADS.c["metadata"]
    values: Dict[str, Any] = {"metadata": _merged_metadata(column, patch)}
    if updated_at is not None:
        values["updatedAt"] = updated_at

    return THREADS.update().where(THREADS.c["id"] == thread_id).values(**values)


def _merged_metadata(column: Any, patch: Mapping[str, Any]) -> Any:
    """The merged-metadata expression: shallow, as the docstring above says.

    A top-level key is replaced whole, a top-level ``None`` deletes, and
    anything nested is an opaque value. PostgreSQL has no merge-patch before
    17, so deletion and addition are two operators: subtract the null-valued
    keys, concatenate the rest. Not ``jsonb_set``-per-key either -- one
    expression, one write, and nested nulls stay stored rather than stripped.
    """
    deleted = [key for key, value in patch.items() if value is None]
    incoming = {key: value for key, value in patch.items() if value is not None}
    current = func.coalesce(column, cast(literal("{}"), JSONB))
    kept = current.op("-")(cast(literal(deleted), ARRAY(Text())))
    return kept.op("||")(cast(literal(json.dumps(incoming)), JSONB))


def page_size(query: ThreadQuery) -> int:
    """The page size actually served.

    ``ThreadQuery`` constrains this on decode, but the struct can also be
    built in Python, and ``first=0`` is a trap rather than an empty page:
    ``LIMIT 0 + 1`` returns a row, so hasNextPage says True, the row is then
    trimmed off, so endCursor is None — and a client that loops until
    hasNextPage goes False asks for page one forever.
    """
    return max(MIN_PAGE_SIZE, min(query.first, MAX_PAGE_SIZE))


def like_pattern(term: str) -> str:
    """A user's search term as a LIKE pattern, metacharacters and all.

    Unescaped, a search for ``%`` matches every thread and ``a_c`` matches
    ``abc``. The escape character itself has to go first.
    """
    escaped = (
        term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def encode_cursor(thread_id: str, updated_at: Optional[str]) -> str:
    """A page position, as one opaque wire token."""
    return base64.urlsafe_b64encode(
        msgspec.json.encode(PageCursor(id=thread_id, updated_at=updated_at))
    ).decode("ascii")


def decode_cursor(raw: str) -> Optional[PageCursor]:
    """Read one back, or None if it did not come from ``encode_cursor``.

    A cursor the client did not get from us is noise, not an error: the caller
    serves the first page rather than failing the request.
    """
    try:
        return msgspec.json.decode(base64.urlsafe_b64decode(raw), type=PageCursor)
    except msgspec.DecodeError, msgspec.ValidationError, binascii.Error, ValueError:
        return None


def _as_uuid(value: str) -> Optional[UUID]:
    """Parse an id that arrived off the wire, without trusting it."""
    try:
        return UUID(value)
    except AttributeError, TypeError, ValueError:
        return None


def _after_cursor(cursor: PageCursor) -> Optional[Any]:
    """The keyset predicate for "strictly after this position".

    Not a plain row comparison: ``(updatedAt, id) < (ts, id)`` is NULL, not
    false, whenever either side has a NULL timestamp, and a NULL predicate
    drops the row. Under ``updatedAt DESC NULLS LAST`` the threads with no
    timestamp are the tail of the history, so they are added back explicitly
    — and a cursor sitting *in* that tail is ordered by id alone.
    """
    cursor_id = _as_uuid(cursor.id)
    if cursor_id is None:
        return None
    bound_id = literal(cursor_id, THREADS.c["id"].type)
    cursor_updated_at = iso_datetime(cursor.updated_at)

    if cursor_updated_at is None:
        return and_(THREADS.c["updatedAt"].is_(None), THREADS.c["id"] < bound_id)

    return or_(
        tuple_(THREADS.c["updatedAt"], THREADS.c["id"])
        < tuple_(
            literal(cursor_updated_at, THREADS.c["updatedAt"].type),
            bound_id,
        ),
        THREADS.c["updatedAt"].is_(None),
    )


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
        user_id = _as_uuid(query.user_id)
        if user_id is None:
            # Not a uuid, so it is nobody's id and nothing can match. Dropping
            # the filter instead would hand the caller every user's history.
            conditions.append(false())
        else:
            conditions.append(THREADS.c["userId"] == user_id)
    if query.search:
        pattern = like_pattern(query.search)
        conditions.append(
            or_(
                THREADS.c["name"].ilike(pattern, escape=LIKE_ESCAPE),
                exists(
                    select(STEPS.c["id"]).where(
                        and_(
                            STEPS.c["threadId"] == THREADS.c["id"],
                            STEPS.c["output"].ilike(pattern, escape=LIKE_ESCAPE),
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
        cursor = decode_cursor(query.cursor)
        after = None if cursor is None else _after_cursor(cursor)
        if after is not None:
            conditions.append(after)

    if conditions:
        statement = statement.where(and_(*conditions))

    # NULLS LAST: PostgreSQL puts NULLs *first* under DESC, which would head
    # the history with a thread that has no activity at all.
    #
    # first + 1: the extra row is what answers hasNextPage without a count.
    return statement.order_by(
        THREADS.c["updatedAt"].desc().nulls_last(), THREADS.c["id"].desc()
    ).limit(page_size(query) + 1)


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


def upsert_element(values: Mapping[str, Any]) -> Insert:
    """Store an element, overwriting the columns the caller provided."""
    statement = insert(ELEMENTS).values(**values)
    assignments = {
        column: statement.excluded[column] for column in values if column != "id"
    }
    if not assignments:
        return statement.on_conflict_do_nothing(index_elements=[ELEMENTS.c["id"]])
    return statement.on_conflict_do_update(
        index_elements=[ELEMENTS.c["id"]], set_=assignments
    )


def upsert_feedback(values: Mapping[str, Any]) -> ReturningInsert[Any]:
    """Store a feedback, keyed on the step it is about.

    Not on the feedback's own id: a client that has lost the id would then
    write a second row for the same step, and every reader joins on ``forId``
    expecting one. The returned id is the row that actually survived, which
    is not the id the caller proposed when a feedback was already there.
    """
    statement = insert(FEEDBACKS).values(**values)
    return statement.on_conflict_do_update(
        index_elements=[FEEDBACKS.c["forId"]],
        set_={
            "value": statement.excluded["value"],
            "comment": statement.excluded["comment"],
        },
    ).returning(FEEDBACKS.c["id"])


def cursor_for(row: Any) -> str:
    """One row's position in the history, as an opaque cursor."""
    return encode_cursor(str(row.id), iso_text(row.updatedAt))


def cursors_for(rows: Sequence[Any]) -> Tuple[Optional[str], Optional[str]]:
    """First and last position of a page, as the wire cursors.

    The position, not the thread id: a cursor naming a row forced the next
    page to read that row's timestamp back out of the table, so deleting the
    thread from another tab made the comparison NULL and everything below it
    unreachable.
    """
    if not rows:
        return None, None
    return cursor_for(rows[0]), cursor_for(rows[-1])
