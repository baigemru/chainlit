"""The project data API: settings, threads, elements, feedback, actions.

Everything the chat UI reads or writes over HTTP that is not a file. Four
things changed shape on the way over from the FastAPI router, and all four
are decisions rather than translations:

**There is no ``get_data_layer()`` any more.** Every route that used to open
with ``if not data_layer: raise 400`` now names the one service it needs and
lets Litestar inject it, bound to the request's own session. The persistence
package's before-send handler commits on a 2xx and rolls back otherwise, so
nothing here commits by hand. "Persistence is switched off" is expressed by
the application not mounting these routes at all, not by every handler
carrying a branch for it.

**The thread history is a keyset cursor.** ``POST /project/threads`` takes a
:class:`~chainlit.persistence.records.ThreadQuery` as its whole body — flat,
with ``first`` and an opaque ``cursor`` — rather than the old
``{pagination, filter}`` envelope over limit/offset. The ``userId`` in that
body is overwritten unconditionally with the caller's own: it used to be a
filter the client set, and a filter a client sets is not an authorization.

**Authorization is per-resource.** A thread is readable by its author. The
two ``/project/share/`` routes are the deliberate exception — they serve a
thread whose owner published it, and the blobs hanging off it, to somebody
who is not the owner — and they are gated on the thread's own ``is_shared``
metadata instead. Both refusals are ``404``: a ``403`` on somebody else's
thread confirms the thread exists.

**Live sessions are behind a seam.** ``POST /project/action`` and the element
routes act on an in-memory websocket session, and the resume filter asks the
live registry which steps a running ask is still holding. That registry
belongs to the websocket package; this module declares
:class:`SessionRegistry` and :class:`LiveSession` for what it needs and takes
it as the ``sessions`` dependency. A session that is not the caller's gets the
same ``404`` an unknown one does, for the same reason as the thread rule --
see :func:`chainlit.controllers.caller.assert_session_owner`.

**The resume-delete filter has one owner, and it is this module.**
:func:`hide_resume_deleted` (with :func:`doomed_step_ids` and
:func:`is_resume_delete` under it) is what both readers of a stored thread
apply: the HTTP routes below, and ``ApplicationRunner._resume`` when a socket
reopens a thread. The websocket handshake used to carry its own copy; it does
not any more, so a change to what "a resume would delete" means is made here
and nowhere else.
"""

from __future__ import annotations

import hashlib
from typing import (
    AbstractSet,
    Annotated,
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)
from uuid import UUID

import msgspec
from litestar import Controller, Request, Response, delete, get, post, put
from litestar.di import NamedDependency
from litestar.exceptions import ClientException, NotFoundException
from litestar.params import FromPath, FromQuery, JSONBody, QueryParameter

import chainlit.config
from chainlit.controllers.caller import (
    assert_session_owner,
    caller,
    caller_identifier,
)
from chainlit.controllers.sessions import LiveSession, SessionRegistry
from chainlit.logger import logger
from chainlit.markdown import get_markdown_str
from chainlit.persistence.records import (
    ElementRecord,
    FeedbackRecord,
    ThreadDetail,
    ThreadPage,
    ThreadPatch,
    ThreadQuery,
)
from chainlit.persistence.services import (
    ElementService,
    FeedbackService,
    StepService,
    ThreadService,
    UserService,
    from_datetime,
    now,
)
from chainlit.persistence.storage.base import BaseStorageClient, discard_blobs

# Re-exported: the upload path settles the same header at upload time, so the
# rule lives where neither side of it has to import the other.
from chainlit.persistence.storage.disposition import content_disposition, element_mime
from chainlit.persistence.writer import SessionWriter
from chainlit.protocol.payloads import Element
from chainlit.security import AuthedRequest

__all__ = (
    "RESUME_POLICY_DELETE",
    "RESUME_POLICY_KEY",
    "ElementPayload",
    "FeedbackDelete",
    "FeedbackUpdate",
    "ProjectController",
    "ThreadDelete",
    "ThreadRename",
    "ThreadShare",
    "content_disposition",
    "doomed_step_ids",
    "element_mime",
    "hide_resume_deleted",
    "is_resume_delete",
)

# The metadata flag ``cl.Message(resume="delete")`` writes. The writer is
# ``chainlit.message``; the literals are repeated here rather than imported
# because that module drags the whole runtime (context, elements, literalai)
# in behind it, and the HTTP half must stay importable without the transport.
RESUME_POLICY_KEY = "resume_policy"
RESUME_POLICY_DELETE = "delete"

# The language a translation file may be named after. It is interpolated into
# a filesystem path by ``ChainlitConfig.load_translation``, so it is
# constrained at the signature and Litestar refuses anything else before the
# handler runs.
LANGUAGE_PATTERN = (
    "^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,4})?(-[a-zA-Z0-9]{2,8})?(-x-[a-zA-Z0-9]{1,8})?$"
)

#: A ``?language=`` the routes below will hand to ``load_translation``.
Language = Annotated[
    str, QueryParameter(pattern=LANGUAGE_PATTERN, description="Language code")
]

# Metadata keys that belong to the running session and must never travel out
# on a shared thread: they carry the app's own configuration and the user's
# environment. ``__sidebar`` is the element panel's own record (see
# ``chainlit.ws.sidebar``): ids only, but it describes a screen the reader
# of a shared thread is not shown.
PRIVATE_METADATA_KEYS = ("chat_profile", "chat_settings", "env", "__sidebar")

# The only element type a client is allowed to write. Everything else is
# written by the app itself, over the socket.
WRITABLE_ELEMENT_TYPE = "custom"


class FeedbackUpdate(msgspec.Struct, rename="camel", omit_defaults=True):
    """``PUT /feedback``: the thumbs the client is setting."""

    feedback: FeedbackRecord
    session_id: Optional[str] = None


class FeedbackDelete(msgspec.Struct, rename="camel", omit_defaults=True):
    """``DELETE /feedback``: which feedback to drop."""

    feedback_id: str


class ThreadRename(msgspec.Struct, rename="camel", omit_defaults=True):
    """``PUT /project/thread``: the thread's new name."""

    thread_id: UUID
    name: str


class ThreadShare(msgspec.Struct, rename="camel", omit_defaults=True):
    """``PUT /project/thread/share``: publish this thread, or withdraw it."""

    thread_id: UUID
    is_shared: bool


class ThreadDelete(msgspec.Struct, rename="camel", omit_defaults=True):
    """``DELETE /project/thread``: which thread to drop."""

    thread_id: UUID


class ElementPayload(msgspec.Struct, rename="camel", omit_defaults=True):
    """``PUT``/``DELETE /project/element``: a custom element and its session."""

    session_id: str
    element: Dict[str, Any]


class ActionCall(msgspec.Struct, rename="camel", omit_defaults=True):
    """``POST /project/action``: which action, in which session."""

    session_id: str
    action: Dict[str, Any]


# The three response envelopes below are the one place ``omit_defaults`` is
# off. ``{"success": true}`` *is* the default, and omitting it would hand the
# client an empty object to read ``success`` out of.


class Ok(msgspec.Struct, rename="camel"):
    """The ``{"success": ...}`` envelope every write route has always sent."""

    success: bool = True


class FeedbackSaved(Ok, rename="camel"):
    """``PUT /feedback``'s answer, carrying the id that survived the upsert."""

    feedback_id: str = ""


class ActionRan(Ok, rename="camel"):
    """``POST /project/action``'s answer, carrying the callback's return."""

    response: Any = None


async def assert_thread_author(
    threads: ThreadService, thread_id: UUID, request: AuthedRequest
) -> None:
    """Refuse a thread that is not the caller's.

    ``404`` for both "no such thread" and "not yours". A ``403`` would be an
    oracle: it tells whoever asks that the thread exists and whose it is not,
    which is exactly the fact this check exists to keep.
    """
    identifier = caller_identifier(request)
    author = await threads.get_author(str(thread_id))
    if author is not None:
        if identifier is not None and author != identifier:
            raise NotFoundException("Thread not found")
        return
    # No author on the row. Either the thread does not exist, or the
    # deployment runs without authentication and never wrote one -- and the
    # second is only a legitimate read when there is nobody to refuse.
    if identifier is not None or await threads.fetch(str(thread_id)) is None:
        raise NotFoundException("Thread not found")


def is_resume_delete(step: Any) -> bool:
    """Whether this step is flagged as not surviving a resume.

    The one reader of :data:`RESUME_POLICY_KEY`; the filters above it are
    built on this and nothing else looks at the flag.
    """
    metadata = getattr(step, "metadata", None)
    if not isinstance(metadata, Mapping):
        return False
    return metadata.get(RESUME_POLICY_KEY) == RESUME_POLICY_DELETE


def doomed_step_ids(steps: Sequence[Any], protected: AbstractSet[str]) -> Set[str]:
    """The flagged steps nothing is holding, plus everything nested under one.

    A child left behind would keep a ``parentId`` pointing at a step the
    client never received, and would render as a top-level message.
    """
    doomed = {
        step.id for step in steps if is_resume_delete(step) and step.id not in protected
    }
    if not doomed:
        return doomed

    # Fixed point rather than one pass: a grandchild is only reachable once
    # its parent has been added.
    while True:
        grown = doomed | {
            step.id
            for step in steps
            if step.parent_id in doomed and step.id not in protected
        }
        if grown == doomed:
            return doomed
        doomed = grown


def hide_resume_deleted(
    thread: ThreadDetail, sessions: SessionRegistry
) -> ThreadDetail:
    """Drop the steps a resume would delete from a read of the thread.

    The one implementation of the rule, applied by every reader: the two
    thread routes here and the runner's resume of a reopened socket. It is
    filtering only — nothing is deleted here. The thread routes are the
    ones that matter: a thread with a running task is not dead at all, its
    flagged messages are legitimately live, and the history list reading it
    must not make them disappear from the window they are on screen in.

    On the resume path this can only ever pass the thread through — a
    conversation somebody is live in is never resumed, it is handed over —
    and the call is kept because the rule is about a *reader*, not about a
    caller, and the next reader to appear must not have to rediscover it.
    """
    if not thread.steps:
        return thread
    if sessions.has_live_task(thread.id):
        return thread

    doomed = doomed_step_ids(thread.steps, sessions.protected_step_ids(thread.id))
    if not doomed:
        return thread

    thread.steps = [step for step in thread.steps if step.id not in doomed]
    thread.elements = [
        element for element in thread.elements if element.for_id not in doomed
    ]
    return thread


def public_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    """A shared thread's metadata with the session's own keys removed."""
    return {
        key: value
        for key, value in metadata.items()
        if key not in PRIVATE_METADATA_KEYS
    }


def author_file_url(thread_id: str, element_id: str) -> str:
    """The blob url ``row_to_element`` substitutes for a row with an object.

    A second copy of a literal ``row_to_element`` writes inline
    (``persistence/services.py``), because there is nothing there to import:
    the url is built in the middle of a row mapping, not by a function. The
    two have to agree, and what makes them —
    ``test_a_shared_thread_hands_out_share_urls_for_its_blobs`` reads a real
    thread through the real ``row_to_element`` and asserts the rewrite
    happened, so a drift in either string is a red test rather than a shared
    page of broken images.
    """
    return f"/project/thread/{thread_id}/element/{element_id}/file"


def share_file_url(thread_id: str, element_id: str) -> str:
    """The same blob, on the route a reader with no cookie is allowed."""
    return f"/project/share/{thread_id}/element/{element_id}/file"


def shared_element_urls(thread: ThreadDetail) -> ThreadDetail:
    """Point a shared thread's own blobs at the public route before it leaves.

    ``row_to_element`` substitutes the *author's* file url onto every row
    that has an object behind it, because everywhere else the reader is the
    author. On a share the reader is by definition not, and that url answers
    them with the 404 it answers every stranger with -- so a shared thread
    used to arrive with every persisted image broken.

    Recognised by exact comparison against :func:`author_file_url` rather
    than by re-deriving the rule: an element that owns its url
    (``cl.Image(url="https://…")``) keeps it, and so does anything a later
    change to the substitution decides not to rewrite.
    """
    thread.elements = [
        msgspec.structs.replace(element, url=share_file_url(thread.id, element.id))
        if element.url == author_file_url(thread.id, element.id)
        else element
        for element in thread.elements
    ]
    return thread


# An hour, and ``private`` is not decoration: the author route serves one
# user's file off a url a shared cache could otherwise hand to the next
# person asking for it. The share route carries the same header for one rule
# rather than two -- its blob is public, so `private` costs it nothing.
ELEMENT_CACHE_CONTROL = "private, max-age=3600"


def element_etag(data: bytes) -> str:
    """A strong entity tag for the bytes being served.

    Of the content, never of the ``objectKey``: the upload path writes with
    ``overwrite=True``, so one key holds different bytes over a row's life
    and a key-derived tag would pin every browser to whatever it saw first.
    md5 because this is a cache key and not a signature;
    ``usedforsecurity=False`` so it is still available on a FIPS build.
    """
    return f'"{hashlib.md5(data, usedforsecurity=False).hexdigest()}"'


def etag_matches(header: Optional[str], etag: str) -> bool:
    """Whether an ``If-None-Match`` names the tag we were about to send.

    Exact string comparison, which is RFC 9110 §8.8.3.2's *strong*
    comparison -- the one a ``GET`` with a range or a cache revalidation on a
    strong tag requires. A weak validator ``W/"abc"`` therefore never matches
    ``"abc"``, which is the right answer: nothing here is ever tagged weakly,
    so a weak tag in the request came from somewhere else and the bytes are
    the honest reply. ``*`` matches whatever the origin holds, and the origin
    holds this.
    """
    if not header:
        return False
    candidates = [candidate.strip() for candidate in header.split(",")]
    return "*" in candidates or etag in candidates


async def serve_element_blob(
    elements: ElementService,
    storage: Optional[BaseStorageClient],
    thread_id: UUID,
    element_id: UUID,
    if_none_match: Optional[str],
) -> Response[bytes]:
    """The blob of an element of this thread, for a caller already allowed it.

    The two file routes below differ in one thing only -- how they decide the
    caller may read the thread, the author check or the thread's own
    ``is_shared`` flag -- and everything after that decision is this.

    It takes the ``If-None-Match`` *value* rather than the request on
    purpose. The share route runs under ``exclude_from_auth``, where
    ``connection.user`` raises rather than returns ``None``, and a helper
    that never receives the request cannot later grow a read of it.

    The conditional saves the client, not the bucket: the object is fetched
    either way, because the tag is computed from the bytes. A 304 that cost
    no bucket GET would need :meth:`BaseStorageClient.read_file` to hand back
    the store's own ETag, which it does not.
    """
    record = await elements.fetch(str(thread_id), str(element_id))
    # Everything that is not "here are the bytes" is the same 404. The caller
    # already proved they may read the thread; what is left to distinguish is
    # how the row is broken, and that is our problem.
    if record is None:
        raise NotFoundException("Element not found")
    object_key = record.object_key
    if not isinstance(object_key, str) or not object_key:
        raise NotFoundException("Element not found")
    if storage is None:
        logger.warning(
            "Element %s has a blob but no storage client is configured", element_id
        )
        raise NotFoundException("Element not found")

    data = await storage.read_file(object_key)
    if data is None:
        raise NotFoundException("Element not found")

    etag = element_etag(data)
    cache_headers = {"etag": etag, "cache-control": ELEMENT_CACHE_CONTROL}
    if etag_matches(if_none_match, etag):
        # ``b""`` rather than ``None``: Litestar renders content before it
        # notices the status forbids a body, and ``None`` under a non-JSON
        # media type is an unserializable 500 (``response/base.py:392``).
        # Empty bytes pass through ``render`` untouched, and 304 then skips
        # the content-type and content-length it would otherwise set
        # (``response/base.py:100-120``). Both headers are repeated on the
        # 304 because RFC 9110 §15.4.5 says a cache must be able to update
        # its stored response from it.
        return Response(b"", status_code=304, headers=cache_headers)

    mime = element_mime(record)
    return Response(
        data,
        media_type=mime,
        headers={
            "content-disposition": content_disposition(record.name, mime),
            **cache_headers,
        },
    )


def element_uuid(value: Any, field: str) -> UUID:
    """Read an id out of a client payload, or refuse the request."""
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as error:
        raise ClientException(f"The element has no usable {field}") from error


async def authorize_element(
    elements: ElementService,
    threads: ThreadService,
    payload: Mapping[str, Any],
    request: AuthedRequest,
) -> Tuple[UUID, Optional[str]]:
    """The element being written, once the caller is allowed to write it.

    Checking the *session* is not enough here. An element id is not a secret —
    ``/project/share`` hands them to strangers by design — so a caller holding
    a live session of their own could otherwise overwrite, or delete, an
    element of somebody else's thread. The thread is taken from the stored row
    rather than from the payload, because the payload is the thing under
    suspicion; only a genuinely new element falls back to the ``threadId`` it
    claims, and that claim is checked too.
    """
    if "id" not in payload:
        raise ClientException("The element has no usable id")
    element_id = element_uuid(payload["id"], "id")

    row = await elements.get_one_or_none(id=element_id)
    thread_id: Optional[UUID] = None if row is None else row.thread_id
    if thread_id is None and (claimed := payload.get("threadId")):
        thread_id = element_uuid(claimed, "threadId")

    if thread_id is not None:
        await assert_thread_author(threads, thread_id, request)
    return element_id, None if thread_id is None else str(thread_id)


def authorized_by_session(session: LiveSession, payload: Mapping[str, Any]) -> bool:
    """Whether the session the caller owns is itself the authority here.

    :func:`authorize_element` decides from the stored row, and for a panel
    element there may not be one yet: it is queued through the session's
    writer, which holds everything until the thread's first interaction. A
    card that saved its props in that window was answered with a 404.

    So a session showing the element right now vouches for it -- the caller
    has already been proven to own the session, and the session is what put
    the element on screen. The claimed thread still has to be the session's
    own, or this would be the hole :func:`authorize_element` closes, reopened
    from the other side: Alice's session holding an id, the payload naming
    Bob's thread, and the row written into it.
    """
    claimed = payload.get("threadId")
    if claimed and str(claimed) != (session.thread_id or ""):
        return False
    return session.holds_element(str(payload.get("id") or ""))


def as_element(payload: Mapping[str, Any]) -> Element:
    """The client's element in the shape a session holds one, or a 400.

    The conversion is the check: what the transcript and the panel replay
    are ``Element`` payloads, and a dict that is not one is the caller's
    mistake rather than a shape smuggled into the reconnect replay.
    """
    try:
        return msgspec.convert(payload, Element)
    except msgspec.ValidationError as error:
        raise ClientException(f"The element is malformed: {error}") from error


def custom_element_record(payload: Mapping[str, Any]) -> ElementRecord:
    """The subset of a client-supplied element that may be written.

    Field-by-field rather than ``ElementRecord(**payload)``: the client is
    writing straight into the elements table, and a spread would let it set
    ``url`` or ``objectKey`` — the columns that decide what bytes the UI
    fetches — on somebody's else's element.
    """
    if "name" not in payload:
        raise ClientException("The element has no name")
    return ElementRecord(
        id=str(payload["id"]),
        name=str(payload["name"]),
        type=WRITABLE_ELEMENT_TYPE,
        thread_id=payload.get("threadId") or None,
        for_id=payload.get("forId") or None,
        display=payload.get("display"),
        props=payload.get("props") or {},
    )


class ProjectController(Controller):
    """Settings, translations, threads, elements, feedback and actions."""

    path = "/"

    @get("/health", opt={"exclude_from_auth": True}, sync_to_thread=False)
    def health(self) -> Dict[str, str]:
        """Liveness, for container orchestration.

        Public: an orchestrator has no cookie, and a health check that 401s
        is a health check that fails the deployment it is watching.
        """
        return {"status": "ok"}

    @get("/project/translations", opt={"exclude_from_auth": True}, cache=True)
    async def translations(
        self,
        language: Language = "en-US",
    ) -> Dict[str, Any]:
        """The UI strings for a language.

        Public, because the login page is rendered in them: behind the
        authentication middleware the one screen that cannot have a cookie
        yet would be the one screen with no translations.

        Cached, because the answer is a function of the query alone -- the
        translation files are read off disk and do not change while the app
        runs -- and every page load asks for it. The default key is method
        + path + sorted query, so each ``?language=`` is its own entry.
        ``settings`` below is deliberately *not* cached: it depends on who is
        asking and on callbacks the app may answer differently each time.
        """
        config = chainlit.config.config
        effective = config.ui.language or language
        return {"translation": config.load_translation(effective)}

    @get("/project/settings")
    async def settings(
        self,
        request: AuthedRequest,
        persistence_enabled: NamedDependency[bool],
        language: Language = "en-US",
        chat_profile: FromQuery[Optional[str]] = None,
    ) -> Dict[str, Any]:
        """Everything the UI needs before it opens the websocket."""
        config = chainlit.config.config
        code = config.code
        effective = config.ui.language or language
        user = caller(request)

        chat_profiles: List[Any] = []
        profiles: List[Dict[str, Any]] = []
        if code.set_chat_profiles:
            chat_profiles = await code.set_chat_profiles(user, effective) or []
            for profile in chat_profiles:
                as_dict = profile.to_dict()
                as_dict.pop("config_overrides", None)
                profiles.append(as_dict)

        starters: List[Dict[str, Any]] = []
        if code.set_starters:
            starters = [
                it.to_dict() for it in (await code.set_starters(user, effective)) or []
            ]

        starter_categories: List[Dict[str, Any]] = []
        if code.set_starter_categories:
            found = await code.set_starter_categories(user, effective, chat_profile)
            starter_categories = [it.to_dict() for it in found or []]

        # A profile may override the config the UI is handed; the callbacks
        # above are still asked of the base config, because which profiles
        # exist cannot depend on which one is selected.
        effective_config = config
        if chat_profile and chat_profiles:
            selected = next((p for p in chat_profiles if p.name == chat_profile), None)
            if selected is not None and getattr(selected, "config_overrides", None):
                effective_config = config.with_overrides(selected.config_overrides)

        return {
            "ui": msgspec.to_builtins(effective_config.ui),
            "features": msgspec.to_builtins(effective_config.features),
            "userEnv": effective_config.project.user_env,
            "maskUserEnv": effective_config.project.mask_user_env,
            "dataPersistence": persistence_enabled,
            "threadResumable": bool(code.on_chat_resume or code.on_thread_ready),
            "threadSharing": bool(
                getattr(effective_config.features, "allow_thread_sharing", False)
            ),
            "markdown": get_markdown_str(config.root, effective),
            "chatProfiles": profiles,
            "starters": starters,
            "starterCategories": starter_categories,
        }

    @put("/feedback")
    async def save_feedback(
        self,
        request: AuthedRequest,
        data: JSONBody[FeedbackUpdate],
        steps: NamedDependency[StepService],
        threads: NamedDependency[ThreadService],
        feedbacks: NamedDependency[FeedbackService],
    ) -> FeedbackSaved:
        """Set the thumbs on a step.

        The step is read first for two reasons. ``feedbacks."threadId"`` is
        NOT NULL and the client does not send one, so the thread has to come
        from the step; and having the thread, the author check comes for
        free — without it any logged-in user could overwrite the feedback on
        anybody's message, because migration 0003 made ``forId`` unique and
        the upsert therefore *replaces* rather than adds.
        """
        step = await steps.fetch(data.feedback.for_id)
        if step is None:
            raise NotFoundException("Step not found")
        await assert_thread_author(threads, UUID(step.thread_id), request)

        surviving = await feedbacks.save(
            FeedbackRecord(
                id=data.feedback.id,
                for_id=data.feedback.for_id,
                thread_id=step.thread_id,
                value=data.feedback.value,
                comment=data.feedback.comment,
            )
        )
        return FeedbackSaved(feedback_id=surviving)

    @delete("/feedback", status_code=200)
    async def delete_feedback(
        self,
        request: AuthedRequest,
        data: JSONBody[FeedbackDelete],
        threads: NamedDependency[ThreadService],
        feedbacks: NamedDependency[FeedbackService],
    ) -> Ok:
        """Drop the thumbs on a step.

        ``200``, not the ``204`` Litestar gives a DELETE by default: the
        route answers with a body, and a 204 handler that returns one is
        refused at registration.
        """
        try:
            row = await feedbacks.get_one_or_none(id=UUID(data.feedback_id))
        except ValueError as error:
            raise ClientException("Invalid feedback id") from error
        if row is None:
            raise NotFoundException("Feedback not found")

        await assert_thread_author(threads, row.thread_id, request)
        await feedbacks.remove(data.feedback_id)
        return Ok()

    @post("/project/threads", status_code=200)
    async def list_threads(
        self,
        request: AuthedRequest,
        data: JSONBody[ThreadQuery],
        users: NamedDependency[UserService],
        threads: NamedDependency[ThreadService],
    ) -> ThreadPage:
        """One keyset page of the caller's own history.

        ``userId`` is overwritten, never merged: whatever the client put in
        the body is a request for somebody else's history, and the only
        answer to it is the caller's own.
        """
        identifier = caller_identifier(request)
        if identifier is not None:
            user = await users.get_by_identifier(identifier)
            if user is None:
                raise NotFoundException("User not found")
            data.user_id = user.id
        return await threads.page(data)

    @get("/project/thread/{thread_id:uuid}")
    async def get_thread(
        self,
        request: AuthedRequest,
        thread_id: FromPath[UUID],
        threads: NamedDependency[ThreadService],
        sessions: NamedDependency[SessionRegistry],
    ) -> ThreadDetail:
        """A thread and everything needed to resume it — for its author."""
        await assert_thread_author(threads, thread_id, request)
        thread = await threads.get_detail(str(thread_id))
        if thread is None:
            raise NotFoundException("Thread not found")
        return hide_resume_deleted(thread, sessions)

    @get("/project/share/{thread_id:uuid}", opt={"exclude_from_auth": True})
    async def get_shared_thread(
        self,
        thread_id: FromPath[UUID],
        threads: NamedDependency[ThreadService],
        sessions: NamedDependency[SessionRegistry],
    ) -> ThreadDetail:
        """A thread its author published, read-only, to anyone with the link.

        The deliberate opposite of the route above: no author check, because
        the whole point is that the reader is not the author. What replaces
        it is the thread's own ``is_shared`` flag, and a ``404`` — not a
        ``403`` — when it is not set, so a link to an unshared thread cannot
        be used to discover that the thread exists.

        Public, because a share link that requires a login is not a share
        link. Nothing here reads ``connection.user``: on a route that opts
        out of authentication it raises.
        """
        thread = await threads.get_detail(str(thread_id))
        if thread is None or not thread.metadata.get("is_shared"):
            raise NotFoundException("Thread not found")

        thread = hide_resume_deleted(thread, sessions)
        thread.metadata = public_metadata(thread.metadata)
        # After the filter, not before: an element about to be dropped is not
        # worth a rewrite, and the rewrite must not resurrect one.
        return shared_element_urls(thread)

    @get("/project/thread/{thread_id:uuid}/element/{element_id:uuid}")
    async def get_thread_element(
        self,
        request: AuthedRequest,
        thread_id: FromPath[UUID],
        element_id: FromPath[UUID],
        threads: NamedDependency[ThreadService],
        elements: NamedDependency[ElementService],
    ) -> ElementRecord:
        """One element of a thread.

        Scoped to the thread in the URL, not looked up by id alone: the
        author check is about the thread, so an element read that ignored it
        would authorise against one resource and read another.
        """
        await assert_thread_author(threads, thread_id, request)
        element = await elements.fetch(str(thread_id), str(element_id))
        if element is None:
            raise NotFoundException("Element not found")
        return element

    @get("/project/thread/{thread_id:uuid}/element/{element_id:uuid}/file")
    async def get_thread_element_file(
        self,
        request: AuthedRequest,
        thread_id: FromPath[UUID],
        element_id: FromPath[UUID],
        threads: NamedDependency[ThreadService],
        elements: NamedDependency[ElementService],
        storage: NamedDependency[Optional[BaseStorageClient]] = None,
    ) -> Response[bytes]:
        """The blob behind a stored element, served by the application.

        The bucket is private, so the ``url`` written next to the blob at
        upload time is not reachable by a browser and never was: until this
        route existed, every persisted image in a reloaded thread was a
        broken one. :func:`chainlit.persistence.services.row_to_element`
        points reloaded elements here instead.

        Read and forward, not redirect to a presigned url. Half the element
        components (text, dataframe, plotly, the pdf viewer) ``fetch`` their
        url, which against the bucket's origin is a CORS request it refuses;
        and a cross-origin ``<a download>`` ignores the filename, which for
        this fork's consumer is the whole point of the header below.

        Authorised by the thread in the url, like its neighbour: an element
        id is not a secret, and a read authorised by one would be a read of
        anybody's element.

        Conditional, and the conditional saves the client rather than the
        bucket -- see :func:`serve_element_blob`, which is the whole of the
        delivery and is shared with the share route below.
        """
        await assert_thread_author(threads, thread_id, request)
        return await serve_element_blob(
            elements,
            storage,
            thread_id,
            element_id,
            request.headers.get("if-none-match"),
        )

    @get(
        "/project/share/{thread_id:uuid}/element/{element_id:uuid}/file",
        opt={"exclude_from_auth": True},
    )
    async def get_shared_element_file(
        self,
        request: Request[Any, Any, Any],
        thread_id: FromPath[UUID],
        element_id: FromPath[UUID],
        threads: NamedDependency[ThreadService],
        elements: NamedDependency[ElementService],
        storage: NamedDependency[Optional[BaseStorageClient]] = None,
    ) -> Response[bytes]:
        """The blob of an element of a thread its author published.

        The file half of :meth:`get_shared_thread`, gated on exactly the same
        fact: the thread's own ``is_shared``, and a ``404`` -- never a
        ``403`` -- when it is not set, so the route cannot be used to
        discover which threads exist. Withdrawing the share takes the files
        with it on the next request; nothing is signed, so there is no link
        left outstanding that outlives the flag. The flag governs *reads*,
        not copies: a blob already in a viewer's browser cache stays theirs
        until :data:`ELEMENT_CACHE_CONTROL` expires, which is the price of
        the header and not a hole in this one.

        The thread is read with ``fetch`` rather than ``get_detail``: the
        answer is one boolean out of one row, and a page of a shared thread
        asks this route once per image. A plain :class:`Request`, not
        :data:`AuthedRequest` -- on an excluded route ``connection.user``
        raises, and the delivery helper is never handed the request at all.
        """
        thread = await threads.fetch(str(thread_id))
        if thread is None or not thread.metadata.get("is_shared"):
            raise NotFoundException("Thread not found")
        return await serve_element_blob(
            elements,
            storage,
            thread_id,
            element_id,
            request.headers.get("if-none-match"),
        )

    @put("/project/element")
    async def update_element(
        self,
        request: AuthedRequest,
        data: JSONBody[ElementPayload],
        sessions: NamedDependency[SessionRegistry],
        elements: NamedDependency[ElementService],
        threads: NamedDependency[ThreadService],
    ) -> Ok:
        """Write back a custom element the app rendered into the chat.

        Two checks, because there are two resources: the session the write
        claims to come from, and the element it claims to be about.

        The write lands in two places, and the second one is the point. The
        row is what a *cold* resume reads; the session's own copy is what a
        reload replays, and it used to be left holding whatever the element
        was first sent with -- so a card that saved its props came back
        showing the old ones until the thread was resumed from scratch.

        The row goes through the session's writer rather than straight to
        the service: a slot's element is queued there too, and a direct save
        can overtake it and then be overwritten by it -- fresh props, then
        stale. Without a writer there is no database to be ahead of, and the
        session copy is the whole of the write.
        """
        session = self._session_of(sessions, data.session_id, request)
        if data.element.get("type") != WRITABLE_ELEMENT_TYPE:
            return Ok(success=False)

        # Before anything else: ``elements.id`` is a native uuid column, and
        # a non-uuid id handed to the writer fails a whole batch of somebody
        # else's rows rather than this one request.
        element_uuid(data.element.get("id"), "id")
        if not authorized_by_session(session, data.element):
            await authorize_element(elements, threads, data.element, request)

        record = custom_element_record(data.element)
        session.remember_element(as_element(data.element))
        writer = session.writer
        if isinstance(writer, SessionWriter):
            writer.submit_element(record)
        return Ok()

    @delete("/project/element", status_code=200)
    async def remove_element(
        self,
        request: AuthedRequest,
        data: JSONBody[ElementPayload],
        sessions: NamedDependency[SessionRegistry],
        elements: NamedDependency[ElementService],
        threads: NamedDependency[ThreadService],
        storage: NamedDependency[Optional[BaseStorageClient]] = None,
    ) -> Ok:
        """Remove a custom element the app rendered into the chat.

        The delete is scoped to the thread the *stored row* belongs to, not
        to the one the payload names: an unscoped delete by id alone is a
        delete of anybody's element.

        The blob goes with the row. It runs before the before-send handler
        commits, so a commit that then fails leaves a row whose bytes are
        gone -- which the file route already answers with a 404, and which is
        the direction to fail in: the other one keeps a user's file in the
        bucket with nothing left in the database that knows it is there.
        """
        self._session_of(sessions, data.session_id, request)
        if data.element.get("type") != WRITABLE_ELEMENT_TYPE:
            return Ok(success=False)

        element_id, thread_id = await authorize_element(
            elements, threads, data.element, request
        )
        await discard_blobs(storage, await elements.remove(str(element_id), thread_id))
        return Ok()

    @put("/project/thread")
    async def rename_thread(
        self,
        request: AuthedRequest,
        data: JSONBody[ThreadRename],
        threads: NamedDependency[ThreadService],
    ) -> Ok:
        """Rename a thread."""
        await assert_thread_author(threads, data.thread_id, request)
        await threads.patch(str(data.thread_id), ThreadPatch(name=data.name))
        return Ok()

    @put("/project/thread/share")
    async def share_thread(
        self,
        request: AuthedRequest,
        data: JSONBody[ThreadShare],
        threads: NamedDependency[ThreadService],
    ) -> Ok:
        """Publish a thread, or withdraw it.

        The metadata patch is merged in the database rather than read and
        written back here: two tabs toggling different keys used to be a
        lost update. A key mapped to ``None`` is deleted, which is how
        ``shared_at`` goes away again.
        """
        await assert_thread_author(threads, data.thread_id, request)
        metadata: Dict[str, Any] = {
            "is_shared": data.is_shared,
            "shared_at": from_datetime(now()) if data.is_shared else None,
        }
        await threads.patch(str(data.thread_id), ThreadPatch(metadata=metadata))
        return Ok()

    @delete("/project/thread", status_code=200)
    async def delete_thread(
        self,
        request: AuthedRequest,
        data: JSONBody[ThreadDelete],
        sessions: NamedDependency[SessionRegistry],
        threads: NamedDependency[ThreadService],
        storage: NamedDependency[Optional[BaseStorageClient]] = None,
    ) -> Ok:
        """Delete a thread, its steps, elements, feedbacks and their blobs.

        A live session on the thread is released first, in that order for
        two reasons. Its teardown drains a writer that may still have rows
        to file, and rows filed after the delete would be a conversation
        that came back from the dead. And a session left running would keep
        writing into an address nothing answers for: deleting the thread
        used to leave it alive and reachable, its next message re-creating
        the row the user had just thrown away.
        """
        await assert_thread_author(threads, data.thread_id, request)
        session = sessions.find_thread(str(data.thread_id))
        if session is not None:
            await session.release()
        await discard_blobs(storage, await threads.remove(str(data.thread_id)))
        return Ok()

    @post("/project/action", status_code=200)
    async def call_action(
        self,
        request: AuthedRequest,
        data: JSONBody[ActionCall],
        sessions: NamedDependency[SessionRegistry],
    ) -> ActionRan:
        """Run an action button against the session that rendered it."""
        session = self._session_of(sessions, data.session_id, request)
        try:
            response = await session.call_action(data.action)
        except LookupError as error:
            raise NotFoundException(
                f"No callback found for action {data.action.get('name')}"
            ) from error
        return ActionRan(response=response)

    @staticmethod
    def _session_of(
        sessions: SessionRegistry,
        session_id: str,
        request: AuthedRequest,
    ) -> LiveSession:
        """The live session with this id, if it is the caller's."""
        session = sessions.find(session_id)
        if session is None:
            raise NotFoundException("Session not found")
        assert_session_owner(session, request)
        return session
