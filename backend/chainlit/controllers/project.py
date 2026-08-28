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

**Authorization is per-resource.** A thread is readable by its author.
``GET /project/share/{thread_id}`` is the deliberate exception — it serves a
thread whose owner published it, to somebody who is not the owner — and it is
gated on the thread's own ``is_shared`` metadata instead. Both refusals are
``404``: a ``403`` on somebody else's thread confirms the thread exists.

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
from litestar import Controller, delete, get, post, put
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
    "doomed_step_ids",
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
# environment.
PRIVATE_METADATA_KEYS = ("chat_profile", "chat_settings", "env")

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
    filtering only — nothing is deleted here. The thread routes also serve
    the F5 of a live session, and a thread with a running task is not dead
    at all: its flagged messages are legitimately live, and a second tab
    reading the thread must not make them disappear from the first.
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
        return thread

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
        """
        # The session is checked for authorization only; the element itself
        # is written to the database, not into the session.
        self._session_of(sessions, data.session_id, request)
        if data.element.get("type") != WRITABLE_ELEMENT_TYPE:
            return Ok(success=False)

        await authorize_element(elements, threads, data.element, request)
        await elements.save(custom_element_record(data.element))
        return Ok()

    @delete("/project/element", status_code=200)
    async def remove_element(
        self,
        request: AuthedRequest,
        data: JSONBody[ElementPayload],
        sessions: NamedDependency[SessionRegistry],
        elements: NamedDependency[ElementService],
        threads: NamedDependency[ThreadService],
    ) -> Ok:
        """Remove a custom element the app rendered into the chat.

        The delete is scoped to the thread the *stored row* belongs to, not
        to the one the payload names: an unscoped delete by id alone is a
        delete of anybody's element.
        """
        self._session_of(sessions, data.session_id, request)
        if data.element.get("type") != WRITABLE_ELEMENT_TYPE:
            return Ok(success=False)

        element_id, thread_id = await authorize_element(
            elements, threads, data.element, request
        )
        await elements.remove(str(element_id), thread_id)
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
        threads: NamedDependency[ThreadService],
    ) -> Ok:
        """Delete a thread and its steps, elements and feedbacks."""
        await assert_thread_author(threads, data.thread_id, request)
        await threads.remove(str(data.thread_id))
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
