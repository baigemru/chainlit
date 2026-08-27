"""Wire-shaped records exchanged with the persistence services.

These msgspec Structs are the contract between the persistence package and
its callers. ``rename="camel"`` keeps the JSON shape byte-identical to the
``TypedDict``s the socket and REST layers already emit (``threadId``,
``waitForAnswer``, ...), so nothing downstream has to change.

Write-side records use ``msgspec.UNSET`` rather than ``None`` for fields the
caller did not provide. "Absent" and "explicitly empty" are different
instructions to the database: absent means "keep whatever is stored", empty
means "store the empty value". Collapsing the two is what forced the legacy
data layer into its ``COALESCE(NULLIF(...))`` guesswork.
"""

from typing import Annotated, Any, Dict, List, Optional, Union

from msgspec import UNSET, Meta, Struct, UnsetType

# A page the caller cannot use is not worth serving: ``first=0`` used to mean
# "an empty page, and there is more", which a client looping on hasNextPage
# never escapes. The ceiling keeps one request from scanning the whole history.
MIN_PAGE_SIZE = 1
MAX_PAGE_SIZE = 100


class FeedbackRecord(
    Struct, rename="camel", omit_defaults=True, kw_only=True, frozen=True
):
    """A thumbs up/down left on a step."""

    for_id: str
    value: int
    id: Optional[str] = None
    thread_id: Optional[str] = None
    comment: Optional[str] = None


class UserRecord(Struct, rename="camel", omit_defaults=True, kw_only=True, frozen=True):
    """A persisted user, as returned to the auth layer."""

    id: str
    identifier: str
    created_at: str
    metadata: Dict[str, Any] = {}


class ElementRecord(
    Struct, rename="camel", omit_defaults=True, kw_only=True, frozen=True
):
    """An element attached to a step.

    ``auto_play`` and ``player_config`` are part of the wire contract but were
    missing from the deployed schema; migration 0002 adds their columns.

    Every field but the three the INSERT half of the upsert cannot be built
    without defaults to ``UNSET``, exactly as ``StepRecord`` does: an element
    is written incrementally — the blob is uploaded, then the url is attached
    — and a ``None`` default would make each of those writes null every column
    the caller did not happen to mention, because ``_column_values`` skips
    ``UNSET`` and nothing else.

    ``id``, ``name`` and ``type`` stay required: ``name`` is NOT NULL in the
    schema, ``id`` is the conflict target, and every caller knows what kind of
    element it is writing.
    """

    id: str
    name: str
    type: str
    thread_id: Union[str, UnsetType, None] = UNSET
    chainlit_key: Union[str, UnsetType, None] = UNSET
    url: Union[str, UnsetType, None] = UNSET
    object_key: Union[str, UnsetType, None] = UNSET
    display: Union[str, UnsetType, None] = UNSET
    size: Union[str, UnsetType, None] = UNSET
    language: Union[str, UnsetType, None] = UNSET
    page: Union[int, UnsetType, None] = UNSET
    props: Union[Dict[str, Any], UnsetType, None] = UNSET
    auto_play: Union[bool, UnsetType, None] = UNSET
    player_config: Union[Dict[str, Any], UnsetType, None] = UNSET
    for_id: Union[str, UnsetType, None] = UNSET
    mime: Union[str, UnsetType, None] = UNSET


class StepRecord(Struct, rename="camel", omit_defaults=True, kw_only=True):
    """A step, on the way in as well as on the way out.

    Every field but the three NOT NULL columns defaults to ``UNSET``: a
    streaming update touches one column and must leave the rest alone, which
    the upsert can only honour if it can tell "not provided" from "provided
    as None".

    ``id``, ``type`` and ``thread_id`` are required because the INSERT half
    of that upsert cannot be built without them, and every caller has all
    three — a step is created inside a thread and knows its own kind.
    """

    id: str
    type: str
    thread_id: str
    name: Union[str, UnsetType, None] = UNSET
    parent_id: Union[str, UnsetType, None] = UNSET
    command: Union[str, UnsetType, None] = UNSET
    modes: Union[Dict[str, str], UnsetType, None] = UNSET
    streaming: Union[bool, UnsetType] = UNSET
    wait_for_answer: Union[bool, UnsetType, None] = UNSET
    is_error: Union[bool, UnsetType, None] = UNSET
    metadata: Union[Dict[str, Any], UnsetType] = UNSET
    tags: Union[List[str], UnsetType, None] = UNSET
    input: Union[str, UnsetType, None] = UNSET
    output: Union[str, UnsetType, None] = UNSET
    created_at: Union[str, UnsetType, None] = UNSET
    start: Union[str, UnsetType, None] = UNSET
    end: Union[str, UnsetType, None] = UNSET
    generation: Union[Dict[str, Any], UnsetType, None] = UNSET
    show_input: Union[str, bool, UnsetType, None] = UNSET
    default_open: Union[bool, UnsetType, None] = UNSET
    auto_collapse: Union[bool, UnsetType, None] = UNSET
    language: Union[str, UnsetType, None] = UNSET
    indent: Union[int, UnsetType, None] = UNSET
    feedback: Union[FeedbackRecord, UnsetType, None] = UNSET


class ThreadRecord(Struct, rename="camel", omit_defaults=True, kw_only=True):
    """A thread without its steps — what the history list renders."""

    id: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    name: Optional[str] = None
    user_id: Optional[str] = None
    user_identifier: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Dict[str, Any] = {}
    parent_thread_id: Optional[str] = None


class ThreadDetail(ThreadRecord, rename="camel", omit_defaults=True, kw_only=True):
    """A thread with everything needed to resume it."""

    steps: List[StepRecord] = []
    elements: List[ElementRecord] = []


class ThreadPatch(Struct, rename="camel", omit_defaults=True, kw_only=True):
    """A partial thread write.

    ``metadata`` is merged, not replaced: a key mapped to ``None`` deletes it,
    every other key is written over the stored value. ``UNSET`` leaves the
    stored metadata untouched.
    """

    name: Union[str, UnsetType, None] = UNSET
    user_id: Union[str, UnsetType, None] = UNSET
    user_identifier: Union[str, UnsetType, None] = UNSET
    metadata: Union[Dict[str, Any], UnsetType] = UNSET
    tags: Union[List[str], UnsetType, None] = UNSET
    parent_thread_id: Union[str, UnsetType, None] = UNSET


class ThreadQuery(Struct, rename="camel", omit_defaults=True, kw_only=True):
    """One page request against the thread history."""

    user_id: Optional[str] = None
    search: Optional[str] = None
    feedback: Optional[int] = None
    # Constrained on the record rather than only in the service, so the bound
    # reaches the generated OpenAPI schema and a request carrying a nonsense
    # page size is rejected at the edge instead of being quietly clamped.
    first: Annotated[int, Meta(ge=MIN_PAGE_SIZE, le=MAX_PAGE_SIZE)] = 20
    # Opaque. It encodes the ``(updatedAt, id)`` position itself rather than
    # naming a thread, so it keeps working after that thread is deleted.
    cursor: Optional[str] = None


class PageInfoRecord(
    Struct, rename="camel", omit_defaults=True, kw_only=True, frozen=True
):
    """Relay-style page info, unchanged from the legacy ``PageInfo``."""

    has_next_page: bool
    start_cursor: Optional[str] = None
    end_cursor: Optional[str] = None


class ThreadPage(Struct, rename="camel", omit_defaults=True, kw_only=True):
    """One page of threads plus its cursors."""

    page_info: PageInfoRecord
    data: List[ThreadRecord] = []


class PageCursor(Struct, rename="camel", kw_only=True, frozen=True):
    """The position a page cursor encodes, before it is base64'd.

    Naming a row and reading its timestamp back out of the table was the bug:
    delete that row and the scalar subquery yields NULL, the keyset comparison
    yields NULL, and everything below the cursor becomes unreachable. Carrying
    the timestamp in the cursor removes the read entirely.

    ``updated_at`` is optional because the column is: a thread the legacy layer
    wrote, or one migration 0002 could not backfill, sorts at the end of the
    history with no timestamp at all.
    """

    id: str
    updated_at: Optional[str] = None
