import asyncio
import json
import uuid
from typing import (
    Any,
    Dict,
    Literal,
    NotRequired,
    Optional,
    Tuple,
    TypedDict,
    Union,
    cast,
)
from urllib.parse import unquote

from starlette.requests import cookie_parser

import chainlit.transit as transit
from chainlit.auth import (
    get_current_user,
    get_token_from_cookies,
    require_login,
)
from chainlit.chat_context import chat_context
from chainlit.config import ChainlitConfig, config, config as global_config
from chainlit.context import context, init_ws_context
from chainlit.data import get_data_layer
from chainlit.emitter import _make_legacy_ask_ack
from chainlit.logger import logger
from chainlit.message import ErrorMessage, Message
from chainlit.persist_barrier import create_persist_task, wait_for_persist
from chainlit.resume_policy import split_resume_delete
from chainlit.server import sio
from chainlit.session import ClientType, WebsocketSession
from chainlit.step import StepDict
from chainlit.types import (
    InputAudioChunk,
    InputAudioChunkPayload,
    MessagePayload,
    ProfileStartInfo,
)
from chainlit.user import PersistedUser, User
from chainlit.user_session import user_sessions

type WSGIEnvironment = dict[str, Any]


class WebSocketSessionAuth(TypedDict):
    sessionId: str
    userEnv: str | None
    clientType: ClientType
    chatProfile: str | None
    threadId: str | None
    pageLoad: NotRequired[bool]


def _session_owner_matches_user(
    session: WebsocketSession, user: User | PersistedUser | None
) -> bool:
    if session.user is None and user is None:
        return True

    if session.user is None or user is None:
        return False

    return session.user.identifier == user.identifier


def restore_existing_session(
    sid,
    session_id,
    emit_fn,
    emit_call_fn,
    environ,
    user: User | PersistedUser | None = None,
    *,
    emit_ask_fn,
    page_load=False,
):
    """Restore a session from the sessionId provided by the client."""
    if session := WebsocketSession.get_by_id(session_id):
        if not _session_owner_matches_user(session, user):
            logger.error("Authorization for the session failed.")
            raise ConnectionRefusedError("session authorization failed")

        session.restore(new_socket_id=sid)
        session.emit = emit_fn
        session.emit_call = emit_call_fn
        session.emit_ask = emit_ask_fn
        session.environ = environ
        session.fresh_page_load = page_load
        return True
    return False


async def persist_user_session(thread_id: str, metadata: Dict):
    if data_layer := get_data_layer():
        await data_layer.update_thread(thread_id=thread_id, metadata=metadata)


async def _resolve_profile_names(session: WebsocketSession) -> list:
    """Profile names this session's user is allowed to switch to.

    An app with no declared profiles has nothing to switch to, so the empty
    list refuses everything. Returning "anything goes" there would let a
    crafted socket event write an arbitrary string into session.chat_profile,
    from where it reaches thread metadata and — with auto_tag_thread — the
    thread's tags.
    """
    if not config.code.set_chat_profiles:
        return []
    profiles = await config.code.set_chat_profiles(session.user, session.language)
    return [p.name for p in profiles or []]


def _session_is_current(session: WebsocketSession) -> bool:
    """Whether the registry still holds this exact session object.

    A switch spans several awaits, and session.delete() takes no lock, so a
    reconnect can evict this session mid-procedure (drop_stale_session).
    Same re-check supersede_abandoned_ask_sessions performs right before it
    acts, and for the same reason: everything after it must run without an
    await in between.
    """
    from chainlit.session import ws_sessions_id

    return ws_sessions_id.get(session.id) is session


async def perform_profile_switch(
    session: WebsocketSession,
    name: str,
    payload: Any = None,
    source: str = "client",
) -> bool:
    """Switch the chat profile in place — same session, same thread.

    The single implementation behind both entry points (the server-side
    ``cl.switch_chat_profile`` and the ``switch_chat_profile`` socket
    event). Step order is load-bearing; see the task doc.

    Returns False when the feature is off, the name is empty or the name is
    not among the profiles this user may use — nothing is mutated and no
    event is emitted, so the client atom (which only follows
    ``chat_profile_changed``) can never diverge from the server.
    """
    if not config.features.hot_swap_chat_profile:
        return False
    if not name:
        return False

    # The slot carries a class-level None default so Mock(spec=...) can see
    # it; real sessions build the Lock in __init__. Heal a missing one here
    # rather than crash — a lock is per-session state, never shared.
    lock = session.profile_switch_lock
    if lock is None:
        lock = session.profile_switch_lock = asyncio.Lock()

    async with lock:
        # (1) Validation doubles as authorization: set_chat_profiles is
        # per-user, so a client asking for a profile it may not see is
        # refused here.
        allowed = await _resolve_profile_names(session)
        if name not in allowed:
            logger.warning("Unknown or forbidden chat profile: %s", name)
            return False

        # The validation above awaited into the app's callback; the session
        # may be gone by now.
        if not _session_is_current(session):
            return False

        previous = session.chat_profile

        # (2) No-op: the selector may echo the current profile. Do not
        # restart the hook on that.
        if previous == name:
            return True

        caller = asyncio.current_task()

        # (3) A previous hook instance would fight the new one over the
        # single pending_ask slot. Guarded by identity: switching from
        # inside the hook itself stays legal, and then the old instance is
        # the caller and finishes "outside the slot".
        prev_hook = session.profile_start_task
        if prev_hook is not None and prev_hook is not caller and not prev_hook.done():
            prev_hook.cancel()

        # (4) A resume offer parked in the old profile's context is stale.
        if (
            session.thread_ready_task is not None
            and not session.thread_ready_task.done()
        ):
            session.thread_ready_task.cancel()

        # (4a) Same guard for current_task. It holds on_message when the app
        # switches from inside one — cancelling the caller would kill the
        # switch mid-await — but at cold start it holds on_chat_start, and a
        # wizard left blocked on an ask would otherwise keep running under
        # the profile the user just left. Precedent: stop (below) cancels
        # this slot outright and calls it "deliberately coarse".
        live = session.current_task
        if live is not None and live is not caller and not live.done():
            live.cancel()

        # (5) Free the ask slot, exactly as stop does: send_ask_user refuses
        # a second concurrent ask and returns None, so without this the
        # hook's first question would silently no-op.
        if session.pending_ask is not None:
            session.pending_ask.cancel()
            session.pending_ask = None
            await context.emitter.clear("clear_ask")

        if not _session_is_current(session):
            return False

        # Everything from here is committed: steps 9-11 run in the finally
        # below even on cancellation. Without that, a stop() landing between
        # the mutation and the emit — and stop() cancels current_task, which
        # IS the caller when an app switches from inside on_message — left
        # the server in the new profile, the client in the old one and no
        # hook running at all. A dead chat, and the "cannot diverge" claim
        # in this docstring held only by luck.
        try:
            # (6) The profile itself.
            session.chat_profile = name
            user_sessions.setdefault(session.id, {})["chat_profile"] = name

            # (6a) The old profile's settings form must not be read by the
            # new profile; to_persistable carries this into thread metadata.
            session.chat_settings = {}

            # (7) Reset before re-resolving: resolve_config returns the
            # cached value otherwise, and the new profile's config_overrides
            # would silently not apply. global_config by binding, not the
            # module-level name: resolve_config compares by identity against
            # the object it imports at call time, and tests patch
            # chainlit.socket.config.
            session.config = global_config
            await session.resolve_config()

            # (8) Persist now, not on disconnect. Guarded like the
            # disconnect handler: update_thread upserts, so persisting
            # before the first interaction would create an empty thread row.
            # A data layer failure must not abort the switch — the profile
            # has already changed in memory.
            if session.thread_id and session.has_first_interaction:
                try:
                    await persist_user_session(
                        session.thread_id, session.to_persistable()
                    )
                except Exception as e:
                    logger.exception("Failed to persist switched chat profile: %s", e)
        finally:
            # (9) Only now may the client move its atom. Before create_task
            # below, never after: anything that rewrites client state has to
            # land before the hook can emit.
            await context.emitter.emit(
                "chat_profile_changed",
                {"chatProfile": name, "previous": previous, "sync": False},
            )

            # (10) The hook, in its own slot.
            if config.code.on_profile_start:
                info = ProfileStartInfo(
                    profile=name, previous=previous, payload=payload, source=source
                )
                session.profile_start_task = asyncio.create_task(
                    config.code.on_profile_start(info)
                )

            # (11) Level-triggered indicator resync, same self-healing shape
            # as the connection_successful tail: a just-launched hook still
            # counts 0 and its wrapper corrects this on the first tick.
            pending = session.pending_ask
            has_live_ask = pending is not None and pending.is_live
            if session.task_counter > 0 and not has_live_ask:
                await context.emitter.task_start()
            else:
                await context.emitter.task_end()

        return True


async def resume_thread(session: WebsocketSession):
    data_layer = get_data_layer()
    if not data_layer or not session.user or not session.thread_id_to_resume:
        return
    # Step persistence is fire-and-forget; a wholesale resume_thread
    # snapshot read before those tasks land would drop the freshest steps
    # from the client's feed until the next reload.
    await wait_for_persist(session.thread_id_to_resume)
    thread = await data_layer.get_thread(thread_id=session.thread_id_to_resume)
    if not thread:
        return

    author = thread.get("userIdentifier")
    user_is_author = author == session.user.identifier

    if user_is_author:
        metadata = thread.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        user_sessions[session.id] = metadata.copy()
        if chat_profile := metadata.get("chat_profile"):
            session.chat_profile = chat_profile
        if chat_settings := metadata.get("chat_settings"):
            session.chat_settings = chat_settings

        return thread


def load_user_env(user_env):
    user_env_dict = {}
    if user_env:
        user_env_dict = json.loads(user_env)
    # Check user env
    if config.project.user_env:
        if not user_env_dict:
            raise ConnectionRefusedError("Missing user environment variables")
        # Check if requested user environment variables are provided
        for key in config.project.user_env:
            if key not in user_env_dict:
                raise ConnectionRefusedError(
                    "Missing user environment variable: " + key
                )
    return user_env_dict


def _get_token_from_cookie(environ: WSGIEnvironment) -> Optional[str]:
    if cookie_header := environ.get("HTTP_COOKIE", None):
        cookies = cookie_parser(cookie_header)
        return get_token_from_cookies(cookies)

    return None


def _get_token(environ: WSGIEnvironment) -> Optional[str]:
    """Take WSGI environ, return access token."""
    return _get_token_from_cookie(environ)


async def _authenticate_connection(
    environ: WSGIEnvironment,
) -> Union[Tuple[Union[User, PersistedUser], str], Tuple[None, None]]:
    if token := _get_token(environ):
        user = await get_current_user(token=token)
        if user:
            return user, token

    return None, None


def _session_has_live_work(session) -> bool:
    """Whether the session still has a live ask or a live task.

    The F5 keep-alive check: a page load reusing the session id keeps the
    session only while something is actually running. All three task slots
    count — ``current_task``, the on_thread_ready hook's
    ``thread_ready_task`` and the on_profile_start hook's
    ``profile_start_task`` — and so do ask_reply conversions still parked
    on the handshake gate: dropping the session cancels them, and
    ``delete()`` calls that the one path where rescued user input is
    genuinely lost.
    """
    pending_ask = session.pending_ask
    if pending_ask is not None and pending_ask.is_live:
        return True
    for task in (
        session.current_task,
        session.thread_ready_task,
        session.profile_start_task,
    ):
        if task is not None and not task.done():
            return True
    for task in getattr(session, "deferred_ask_reply_tasks", None) or []:
        if not task.done():
            return True
    return False


@sio.on("connect")  # pyright: ignore [reportOptionalCall]
async def connect(sid: str, environ: WSGIEnvironment, auth: WebSocketSessionAuth):
    user: User | PersistedUser | None = None
    token: str | None = None
    thread_id = auth.get("threadId", None)

    if require_login():
        try:
            user, token = await _authenticate_connection(environ)
        except Exception as e:
            logger.exception("Exception authenticating connection: %s", e)

        if not user:
            logger.error("Authentication failed in websocket connect.")
            raise ConnectionRefusedError("authentication failed")

        if thread_id:
            if data_layer := get_data_layer():
                thread = await data_layer.get_thread(thread_id)
                if thread and not (thread["userIdentifier"] == user.identifier):
                    logger.error("Authorization for the thread failed.")
                    raise ConnectionRefusedError("thread authorization failed")

    # Session scoped function to emit to the client
    def emit_fn(event, data):
        return sio.emit(event, data, to=sid)

    # Session scoped function to emit to the client and wait for a response
    def emit_call_fn(event: Literal["ask", "call_fn"], data, timeout):
        return sio.call(event, data, timeout=timeout, to=sid)

    # Session scoped function to emit an ask with a socket.io ack attached.
    # The ack is a legacy path: clients running a stale cached bundle answer
    # asks through it instead of the ask_reply event.
    def emit_ask_fn(data, callback):
        return sio.emit("ask", data, to=sid, callback=callback)

    session_id = auth["sessionId"]

    # A reloaded page (pageLoad: the client sets it only on the first
    # connect after a full page load) reconnects to its old session solely
    # to rescue live work — a pending ask or a still-running task (e.g. a
    # paid pipeline between two asks). An idle session means F5 keeps its
    # historical meaning, a fresh chat: the stale session is dropped below
    # — after the new connection is fully validated — and a new one is
    # created under the same id. Transport reconnects and old clients don't
    # set the flag and restore as before.
    page_load = bool(auth.get("pageLoad"))
    drop_stale_session = False
    if page_load:
        if existing := WebsocketSession.get_by_id(session_id):
            if not _session_owner_matches_user(existing, user):
                logger.error("Authorization for the session failed.")
                raise ConnectionRefusedError("session authorization failed")
            drop_stale_session = not _session_has_live_work(existing)

    if not drop_stale_session and restore_existing_session(
        sid,
        session_id,
        emit_fn,
        emit_call_fn,
        environ,
        user=user,
        emit_ask_fn=emit_ask_fn,
        page_load=page_load,
    ):
        return True

    user_env_string = auth.get("userEnv", None)
    user_env = load_user_env(user_env_string)

    if drop_stale_session:
        if stale := WebsocketSession.get_by_id(session_id):
            user_sessions.pop(stale.id, None)
            # Deleted BEFORE the new session is created under the same id:
            # a deferred cleanup would wipe the successor's registry entry,
            # chat context and files directory (they are all keyed by id).
            await stale.delete()

    client_type = auth["clientType"]
    url_encoded_chat_profile = auth.get("chatProfile", None)
    chat_profile = (
        unquote(url_encoded_chat_profile) if url_encoded_chat_profile else None
    )

    session = WebsocketSession(
        id=session_id,
        socket_id=sid,
        emit=emit_fn,
        emit_call=emit_call_fn,
        client_type=client_type,
        user_env=user_env,
        user=user,
        token=token,
        chat_profile=chat_profile,
        thread_id=thread_id,
        environ=environ,
        emit_ask=emit_ask_fn,
    )

    # Resolve chat-profile config overrides asynchronously. Awaited here,
    # in the async connect handler, instead of the old
    # run_until_complete-inside-the-running-loop in get_config() — that
    # re-entry only ever worked through nest_asyncio's patch.
    await session.resolve_config()

    return True


async def apply_transit_message(context):
    """Move a claimed transit record into the user session.

    Runs before `on_chat_start` is scheduled, so the callback can read
    `cl.user_session.get("transit_message")`. Claiming a message counts as
    the first interaction: the thread is created and named immediately
    (steps produced by `on_chat_start` then persist right away instead of
    queueing). A record always stashes the previous thread's id on the
    session, so the new thread is linked to its parent even when the switch
    carried no message.

    Only a session that has not interacted yet may take the record — a flap
    of the previous socket between `store` and the claim re-enters
    `connection_successful` on the old session, which must not swallow the
    record it just parked for its successor.
    """
    session = context.session
    if session.has_first_interaction:
        return

    owner = session.user.identifier if session.user else None
    record = transit.pop(session.id, owner)
    if record is transit.NO_TRANSIT:
        return

    # The previous thread's id rides along even when no message was parked;
    # flush_thread_queues records it as the new thread's parentThreadId.
    session.parent_thread_id = record.parent

    if record.value is None:
        # Parent-only record: nothing to deliver, the thread is still
        # created lazily on the first real message.
        return

    user_sessions.setdefault(session.id, {})["transit_message"] = record.value

    session.has_first_interaction = True
    name = (
        record.value
        if isinstance(record.value, str) and record.value
        else session.chat_profile
    )
    # Awaited on purpose: on_chat_start steps persist immediately now, and
    # must not reach the data layer before the thread row exists.
    await context.emitter.init_thread(name or "transit")


async def send_parent_thread(context):
    """Tell the client which thread the current thread descends from.

    Only a fresh transit thread needs this: its parent lives solely on the
    session until the first interaction persists it. Sent on every
    (re)connect — the client's copy does not survive a socket rebuild. A
    resumed thread needs nothing here; its parent reaches the client inside
    the thread metadata of the `resume_thread` payload.
    """
    if context.session.parent_thread_id is None:
        return
    await context.emitter.emit(
        "parent_thread", {"parentThreadId": context.session.parent_thread_id}
    )


@sio.on("claim_transit_message")  # pyright: ignore [reportOptionalCall]
async def claim_transit_message(sid, payload):
    """Re-park this session's transit message for the session about to open."""
    session = WebsocketSession.require(sid)

    next_id = (payload or {}).get("sessionId")
    if not isinstance(next_id, str):
        return
    try:
        if uuid.UUID(next_id).version != 4:
            return
    except ValueError:
        return
    # A live session under that id means the client did not mint a fresh
    # uuid — with auth disabled this would let one session plant a value
    # into another existing one.
    if WebsocketSession.get_by_id(next_id):
        logger.warning(
            "claim_transit_message: target session already exists, ignoring."
        )
        return

    transit.reassign(session.id, next_id)


async def supersede_abandoned_ask_sessions(current_session, thread_id) -> None:
    """Evict abandoned ask-parked sessions of this thread.

    Runs on the FIRST entry of a new session into the resume branch,
    right before the resume="delete" cleanup decision. Since 2.11.33 an
    on_thread_ready hook parked on a long (e.g. 48h) offer ask keeps its
    session's ``thread_ready_task`` live, so every F5/tab-close left a
    session that made ``thread_has_live_task`` true forever — the thread
    was never "genuinely idle" again and flagged offer steps piled up one
    per entry. Abandoned means ALL of: the socket is disconnected (the
    explicit flag set by the disconnect handler), a live ``pending_ask``
    is waiting for a user who is now HERE in the new session, and the
    session belongs to this thread. A connected second tab (its form is
    really on screen) and a disconnected pipeline running BETWEEN asks
    (``pending_ask is None`` — it will finish and post its results) are
    deliberately untouched.

    Eviction is ``session.delete()``: its synchronous prefix pops the
    session from the registry, so ``thread_has_live_task`` /
    ``protected_step_ids`` recompute correctly on this same entry (a mere
    task cancel would not do — a just-cancelled task is not ``done()``
    yet). The cancelled hook re-offers on the next resume by contract;
    nothing is written on the cancel path ("Timed out" is unreachable,
    ``action.remove()`` is skipped). ``delete()`` awaits (MCP close, up
    to 10s per session), so ALL criteria are re-checked synchronously
    right before each delete — a candidate that reconnected while a
    previous delete was in flight survives; there is no await between the
    re-check and the delete.
    """
    from chainlit.session import ws_sessions_id

    for candidate in list(ws_sessions_id.values()):
        if candidate is current_session:
            continue
        if getattr(candidate, "thread_id", None) != thread_id:
            continue
        # Tolerant read on purpose (same convention as resume_processed):
        # a session object without the flag counts as connected.
        if not getattr(candidate, "socket_disconnected", False):
            continue
        pending_ask = getattr(candidate, "pending_ask", None)
        if pending_ask is None or not pending_ask.is_live:
            continue
        if ws_sessions_id.get(candidate.id) is not candidate:
            continue
        logger.info(
            "Superseding abandoned ask session %s of thread %s",
            candidate.id,
            thread_id,
        )
        # Parity with the drop_stale path in connect: delete() removes the
        # sid mapping, so the disconnect GC's clear() would no-op and leak
        # the user_sessions entry forever.
        user_sessions.pop(candidate.id, None)
        try:
            await candidate.delete()
        except Exception:
            # A zombie's dirty state (e.g. a files-dir race under rmtree)
            # must not break the incoming user's handshake. The candidate
            # may then still sit in the registry, but its ask and tasks
            # were already cancelled by delete()'s prefix — the cleanup
            # gate unblocks by the next entry, and the GC remains the
            # backstop.
            logger.exception("Failed to delete superseded session %s", candidate.id)


async def cleanup_resume_delete_steps(
    context, thread_id: str, doomed_steps: list, doomed_elements: list
) -> Tuple[list, list]:
    """Delete the doomed steps from the data layer and from the client.

    Must run after ``has_first_interaction`` is set (``delete_step`` /
    ``delete_element`` are wrapped in ``queue_until_user_message`` and would
    hang in the queue otherwise) and after the ``resume_thread`` emit (the
    client rebuilds the whole feed on ``resume_thread``, so an earlier
    ``delete_message`` would be lost; the client already drew the history
    from REST, which makes the emit a mandatory safety net). It also runs
    before ``on_chat_resume`` — the handler may block for a long time and
    the doomed steps must not linger on screen meanwhile. Every operation
    is individually guarded: a double resume, a race between two tabs or an
    already-deleted step must not crash the resume.

    Returns ``(failed_steps, failed_elements)``: the doomed steps whose
    data-layer deletion did not fully succeed (kept rows), with their
    still-undeleted elements. The caller stores them on the session and
    retries them on its next entry into the resume branch.
    """
    if not doomed_steps:
        return [], []

    data_layer = get_data_layer()

    elements_by_step: Dict[Any, list] = {}
    for element in doomed_elements:
        elements_by_step.setdefault(element.get("forId"), []).append(element)

    failed_steps: list = []
    failed_elements: list = []

    for step in doomed_steps:
        step_id = step.get("id")
        if data_layer:
            # Elements first and explicitly: not every data layer cascades
            # element deletion from delete_step (DynamoDB does not); where
            # it does, the second delete is idempotent. If any element
            # deletion fails, the step is kept — once the step is gone its
            # elements would be orphaned forever (their forId never enters
            # the doomed set again); keeping it leaves the state retryable,
            # this session retries on its next resume entry.
            elements_deleted = True
            remaining_elements: list = []
            for element in elements_by_step.get(step_id, []):
                try:
                    await data_layer.delete_element(element["id"], thread_id)
                except Exception as e:
                    elements_deleted = False
                    remaining_elements.append(element)
                    logger.warning(
                        f"resume=delete: failed to delete element "
                        f"{element.get('id')} of step {step_id}: {e}"
                    )
            if elements_deleted:
                try:
                    await data_layer.delete_step(step_id)
                except Exception as e:
                    failed_steps.append(step)
                    logger.warning(
                        f"resume=delete: failed to delete step {step_id}: {e}"
                    )
            else:
                failed_steps.append(step)
                failed_elements.extend(remaining_elements)
                logger.warning(
                    f"resume=delete: keeping step {step_id} until its "
                    f"elements can be deleted; will retry on the next resume"
                )
        try:
            await context.emitter.delete_step(step)
        except Exception as e:
            logger.warning(
                f"resume=delete: failed to emit delete_message for step {step_id}: {e}"
            )

    return failed_steps, failed_elements


def repopulate_chat_context(session, thread) -> None:
    """Fill the in-memory transcript from a thread payload.

    Message steps are added to ``chat_context`` deduplicated by id (the
    resume branch runs again on every reconnect, and Message objects
    compare by identity — without the id check every re-entry would append
    duplicates). The thread's element dicts are recorded per step id in
    ``session.transcript_element_dicts`` (replaced wholesale):
    ``Message.from_dict`` carries no elements, so the in-memory transcript
    replay reads attachments from that map.
    """
    element_dicts: Dict[str, list] = {}
    for element in thread.get("elements") or []:
        for_id = element.get("forId")
        if for_id:
            element_dicts.setdefault(for_id, []).append(element)
    try:
        session.transcript_element_dicts = element_dicts
    except Exception:
        logger.debug("Failed to store transcript element dicts", exc_info=True)

    try:
        existing_ids = {m.id for m in chat_context.get()}
    except Exception:
        existing_ids = set()
    for step in thread.get("steps") or []:
        if "message" not in (step.get("type") or ""):
            continue
        if step.get("id") in existing_ids:
            continue
        try:
            chat_context.add(Message.from_dict(step))
        except Exception:
            logger.debug(
                "Failed to restore a persisted step into the chat context",
                exc_info=True,
            )


async def replay_transcript_from_data_layer(context, session):
    """Re-emit the thread's message steps read back from the data layer.

    Fallback for the reconnect resync when the in-memory transcript is
    empty — typically the finishing task's session context was lost (or the
    server recreated the session), while the steps already went to the data
    layer. Waits for pending background persists first, so the read does
    not outrun in-flight ``create_step`` tasks. Emits ``new_message`` /
    ``element`` — both upserts by id on the client, so re-emission is
    idempotent. Deliberately does NOT filter resume="delete" steps: a live
    restored session's flagged steps are legitimately alive, deletion is
    first-entry-only. The replayed steps are also added to the in-memory
    transcript so the next reconnect replays from memory.
    """
    if not getattr(session, "has_first_interaction", False):
        return
    data_layer = get_data_layer()
    if not data_layer or not session.thread_id:
        return
    try:
        await wait_for_persist(session.thread_id)
        thread = await data_layer.get_thread(thread_id=session.thread_id)
    except Exception:
        logger.debug(
            "Failed to read the thread for a reconnect transcript resync",
            exc_info=True,
        )
        return
    if not thread:
        return

    steps = [
        step
        for step in thread.get("steps") or []
        if "message" in (step.get("type") or "")
    ]
    if not steps:
        return
    step_ids = {step.get("id") for step in steps}
    elements_by_step: Dict[Any, list] = {}
    for element in thread.get("elements") or []:
        if element.get("forId") in step_ids:
            elements_by_step.setdefault(element.get("forId"), []).append(element)

    for step in steps:
        try:
            await context.emitter.send_step(step)
            for element in elements_by_step.get(step.get("id"), []):
                await context.emitter.send_element(element)
        except Exception:
            logger.debug(
                "Failed to replay a persisted step on reconnect", exc_info=True
            )

    # The NEXT reconnect replays from memory (with attachments from the
    # element-dict map the helper records).
    repopulate_chat_context(session, thread)


async def restore_pending_ask(
    context, client_has_ui_state: bool, skip_transcript_replay: bool = False
):
    """Rebuild a live pending ask in the UI, or clear the ask state.

    Must run after the resume/transit branches of ``connection_successful``:
    ``resume_thread`` replaces the client's message/element state wholesale
    and would wipe a form re-emitted before it. ``skip_transcript_replay``
    is set by the resume branch when a fresh (barrier'd) ``resume_thread``
    snapshot was emitted in this same cycle — re-emitting the transcript on
    top of it would be redundant wire volume; the ask restore below is
    unaffected.
    """
    session = context.session

    if session.restored and not skip_transcript_replay:
        # Replay the transcript (a paid flow's results live above the form)
        # together with the elements attached to its messages — on EVERY
        # reconnect of a restored session, not just a page reload: emits
        # into a dead/reconnecting socket are dropped by the server, so a
        # plain transport reconnect must converge too. The client upserts
        # new_message/element by id, which makes the re-emission idempotent.
        # This also runs when the session was kept for a still-running task
        # without a pending ask.
        try:
            transcript = chat_context.get()
        except Exception:
            transcript = []
        if transcript:
            stored_elements = getattr(session, "transcript_element_dicts", None) or {}
            for message in transcript:
                try:
                    step_dict = message.to_dict()
                    wait_payload = getattr(message, "_active_wait_payload", None)
                    if wait_payload is not None:
                        # Same transient field the original emit carried:
                        # the client force-overwrites `wait` on every
                        # new_message, so a replay without it would kill a
                        # still-running shimmer.
                        step_dict = {**step_dict, "wait": wait_payload}
                    await context.emitter.send_step(step_dict)
                    # Attachments come from the live objects AND from the
                    # element dicts recorded at repopulation time (a
                    # Message rebuilt from a thread payload carries no
                    # element objects), deduplicated by element id.
                    emitted_element_ids = set()
                    for element in getattr(message, "elements", None) or []:
                        element_dict = element.to_dict()
                        emitted_element_ids.add(element_dict.get("id"))
                        await context.emitter.send_element(element_dict)
                    for element_dict in stored_elements.get(message.id, []):
                        if element_dict.get("id") in emitted_element_ids:
                            continue
                        await context.emitter.send_element(element_dict)
                except Exception:
                    logger.debug(
                        "Failed to replay a transcript message on reconnect",
                        exc_info=True,
                    )
        else:
            await replay_transcript_from_data_layer(context, session)

    pending_ask = session.pending_ask
    if pending_ask is None or not pending_ask.is_live:
        await context.emitter.clear("clear_ask")
        return

    # The form's own buttons are re-emitted UNCONDITIONALLY: "no page load"
    # never proves they arrived — the emits may have been dropped in a
    # dying socket (the prod repro: connect → closed → reconnect with the
    # same session id within ~1s), and the client upserts actions by id so
    # a button it still holds is not duplicated. Known window, parity with
    # the fresh-page-load path (accepted, not fixed here): if the ask dies
    # by cancel or raise_on_timeout between these emits and the slot
    # re-check below, AskActionMessage.send never reaches its
    # remove_action loop and the re-emitted buttons linger client-side as
    # plain action buttons.
    for action_dict in pending_ask.restore_actions:
        await context.emitter.emit("action", action_dict)
    if not client_has_ui_state:
        # The element stays gated: a live custom element may hold client
        # state (a client-driven updateElement builds a NEW server-side
        # object — this snapshot never sees those updates), and a re-emit
        # replaces the atom object and remounts the JSX, wiping in-progress
        # input. Re-emitted only after a genuine page load, when there is
        # nothing left to lose; serialized only now so server-side updates
        # of the live object are still picked up.
        if pending_ask.restore_element is not None:
            await context.emitter.emit("element", pending_ask.restore_element.to_dict())

    # The awaits above may have yielded — re-check that the ask is still
    # the pending one before re-emitting a form for it.
    current = session.pending_ask
    if current is not pending_ask:
        # The slot changed hands. Clear the UI only when nothing live took
        # it over — a successor ask has emitted its own form, and a late
        # clear_ask would wipe it while the server keeps waiting.
        if current is None or current.future.done() or current.expired:
            await context.emitter.clear("clear_ask")
        return
    if pending_ask.future.done():
        await context.emitter.clear("clear_ask")
        return

    spec_dict = dict(pending_ask.spec.to_dict())
    # Remaining time from the original deadline (informational: the server
    # deadline is authoritative, but third-party clients may show a timer).
    spec_dict["timeout"] = max(1, int(pending_ask.remaining))
    ask_payload = {"msg": pending_ask.step_dict, "spec": spec_dict}
    emit_ask = getattr(session, "emit_ask", None)
    if emit_ask is not None:
        await emit_ask(
            ask_payload,
            _make_legacy_ask_ack(session, pending_ask.future, pending_ask.spec.step_id),
        )
    else:
        await context.emitter.emit("ask", ask_payload)


@sio.on("connection_successful")  # pyright: ignore [reportOptionalCall]
async def connection_successful(sid):
    context = init_ws_context(sid)

    # Raw emit: darken the handshake indicator early. The final word on
    # the indicator is the level-triggered resync in the finally below.
    await context.emitter.task_end()
    # call_fn is bound to the old sid and cannot be restored — clear it
    # before any branch may schedule on_chat_start, whose own call_fn a
    # late clear would kill.
    await context.emitter.clear("clear_call_fn")
    # The connect handler records whether this connection is the first one
    # after a full page load (UI state lost, full restore needed) or a
    # plain reconnect of a loaded page. Old clients never set the flag and
    # get the full restore.
    client_has_ui_state = not getattr(context.session, "fresh_page_load", True)
    # Set by the resume branch after it emits a fresh resume_thread
    # snapshot: the transcript replay in restore_pending_ask would then be
    # redundant wire volume on top of the snapshot.
    resume_snapshot_emitted = False
    # The (filtered) thread of a resume that genuinely happened in THIS
    # handshake — the launch argument for on_thread_ready in the finally.
    resumed_thread: Optional[Dict] = None

    try:
        if context.session.restored and not context.session.has_first_interaction:
            await apply_transit_message(context)
            await send_parent_thread(context)
            if config.code.on_chat_start and not context.session.chat_started:
                context.session.chat_started = True
                task = asyncio.create_task(config.code.on_chat_start())
                context.session.current_task = task
            return

        if context.session.thread_id_to_resume and (
            config.code.on_chat_resume or config.code.on_thread_ready
        ):
            thread = await resume_thread(context.session)
            if thread:
                # A transit record parked for this session would outlive the
                # resume (which never reads it) and leak into a later
                # reconnect — drop it instead.
                owner = (
                    context.session.user.identifier if context.session.user else None
                )
                record = transit.pop(context.session.id, owner)
                if record is not transit.NO_TRANSIT and record.value is not None:
                    logger.warning(
                        "Dropping a transit message on thread resume; it would "
                        "otherwise be delivered late into an unrelated chat."
                    )

                # Filtered BEFORE on_chat_resume: the app and the client
                # both get a payload already free of resume="delete" steps.
                # The actual deletion happens after resume_thread below.
                # Only the FIRST entry into the resume branch qualifies — a
                # genuine resume of a dead session. The session re-enters
                # this branch on every reconnect (its thread_id_to_resume is
                # never cleared), and deleting then would kill live flagged
                # messages of a running task; resume_processed gates it.
                # Note: a first entry may already run on a restored
                # transport (the connection blipped between connect and
                # connection_successful) — it is still a genuine resume of
                # a dead session and must filter and delete.
                doomed_steps: list = []
                doomed_elements: list = []
                if not getattr(context.session, "resume_processed", False):
                    # The user is provably back: evict this thread's
                    # abandoned ask-parked sessions BEFORE the cleanup
                    # decision, so the gate below can find the thread
                    # genuinely idle again.
                    await supersede_abandoned_ask_sessions(
                        context.session, thread.get("id")
                    )
                    thread, doomed_steps, doomed_elements = split_resume_delete(thread)
                else:
                    # Re-entry: never re-run split_resume_delete here — it
                    # would doom fresh live flagged messages of a running
                    # task. But steps kept in the DB only because their
                    # deletion failed on a previous entry must not resurface
                    # in the payload: hide exactly them (plain id filter)
                    # and retry the deletion below.
                    retry_steps, retry_elements = getattr(
                        context.session, "resume_delete_retry", ([], [])
                    )
                    if retry_steps:
                        doomed_steps = list(retry_steps)
                        doomed_elements = list(retry_elements)
                        retry_ids = {step.get("id") for step in doomed_steps}
                        thread = {
                            **thread,
                            "steps": [
                                step
                                for step in thread.get("steps") or []
                                if step.get("id") not in retry_ids
                            ],
                        }
                        if "elements" in thread:
                            thread["elements"] = [
                                el
                                for el in thread.get("elements") or []
                                if el.get("forId") not in retry_ids
                            ]

                context.session.has_first_interaction = True
                await context.emitter.emit(
                    "first_interaction",
                    {"interaction": "resume", "thread_id": thread.get("id")},
                )

                # In-memory transcript + attachment map for later memory
                # replays; deduplicated by id (this branch runs again on
                # every reconnect).
                repopulate_chat_context(context.session, thread)

                await context.emitter.resume_thread(thread)
                resume_snapshot_emitted = True
                resumed_thread = thread

                failed_steps, failed_elements = await cleanup_resume_delete_steps(
                    context, thread.get("id"), doomed_steps, doomed_elements
                )
                # Whatever could not be deleted is hidden and retried on the
                # next entry; resume_processed stays True from here on.
                context.session.resume_delete_retry = (failed_steps, failed_elements)
                context.session.resume_processed = True

                # AFTER the resume_thread emit on purpose: the handler may
                # block for a long time and send messages of its own — they
                # must land on top of the already-rebuilt feed instead of
                # being wiped by a stale snapshot emitted afterwards.
                # Optional since on_thread_ready: an app may register only
                # the hook and skip the inline stage.
                if config.code.on_chat_resume:
                    await config.code.on_chat_resume(thread)
                return
            else:
                await context.emitter.send_resume_thread_error("Thread not found.")

        await apply_transit_message(context)
        await send_parent_thread(context)

        if config.code.on_chat_start and not context.session.chat_started:
            context.session.chat_started = True
            task = asyncio.create_task(config.code.on_chat_start())
            context.session.current_task = task
    finally:
        # Invariant order of the handshake tail (do not reorder):
        # resume_thread emit → cleanup + resume_processed → on_chat_resume
        # inline → profile resync → restore_pending_ask → on_thread_ready
        # launch → indicator resync → connection_inited.set(). Gate opens last
        # so a parked orphan-reply conversion always finds the hook slot
        # already occupied.
        #
        # Profile resync goes first, before restore_pending_ask and before
        # the hook launch: the client atom is not persisted, so after F5
        # App.tsx falls back to the config default while the server kept the
        # real one. sync=True marks this "adopt this value", not "a switch
        # happened" — the client only updates its atom. Anything that
        # rewrites client state has to land before anything that emits, or
        # it lands on top of the restored ask and the hook's first messages.
        # Clients that do not know the event ignore it.
        if config.features.hot_swap_chat_profile and context.session.chat_profile:
            await context.emitter.emit(
                "chat_profile_changed",
                {
                    "chatProfile": context.session.chat_profile,
                    "previous": context.session.chat_profile,
                    "sync": True,
                },
            )

        try:
            await restore_pending_ask(
                context,
                client_has_ui_state,
                skip_transcript_replay=resume_snapshot_emitted,
            )
        finally:
            try:
                # Launched even when on_chat_resume raised (symmetric with
                # start: a crash does not cancel restore_pending_ask
                # either), and in its own finally tier so a
                # restore_pending_ask crash cannot eat the launch. Guarded
                # by resumed_thread — a resume genuinely happened in THIS
                # handshake (thread_id_to_resume would be true even for a
                # failed lookup, and never resets) — AND by the
                # once-per-session flag: the resume branch re-enters on
                # every reconnect.
                if (
                    resumed_thread is not None
                    and config.code.on_thread_ready
                    and not context.session.resume_task_started
                ):
                    context.session.resume_task_started = True
                    task = asyncio.create_task(
                        config.code.on_thread_ready(resumed_thread)
                    )
                    # Its own slot, NOT current_task: that slot has two
                    # unconditional writers (client_message and the
                    # orphan-reply conversion) which would evict the
                    # long-lived hook almost immediately.
                    context.session.thread_ready_task = task

                # Level-triggered indicator resync — the final word after
                # the raw task_end at the top of this handler. With a live
                # restored ask the composer is in ask mode and owns the
                # client state. A just-launched hook still counts 0 here
                # (create_task has not ticked; its with_task wrapper
                # acquires on the first tick and edge-emits task_start), so
                # the resync emits task_end first and the wrapper corrects
                # it — self-healing, do not "fix".
                pending = context.session.pending_ask
                has_live_ask = pending is not None and pending.is_live
                if context.session.task_counter > 0 and not has_live_ask:
                    await context.emitter.task_start()
                else:
                    await context.emitter.task_end()

            finally:
                # Opened last, and unconditionally — even when a branch
                # above (on_chat_resume included: there is no try/except
                # around it) raised. A gate left closed would park orphaned
                # ask_reply conversions forever; see the ask_reply handler.
                context.session.connection_inited.set()


@sio.on("clear_session")  # pyright: ignore [reportOptionalCall]
async def clean_session(sid):
    session = WebsocketSession.get(sid)
    if session:
        session.to_clear = True


@sio.on("disconnect")  # pyright: ignore [reportOptionalCall]
async def disconnect(sid):
    session = WebsocketSession.get(sid)

    if not session:
        return

    # Set FIRST, before anything below can await (on_chat_end / persist
    # may hang for a long time): a new session resuming the same thread
    # reads this flag to tell an abandoned session from a connected one
    # (supersede_abandoned_ask_sessions). Cleared only in restore().
    session.socket_disconnected = True

    init_ws_context(session)

    if config.code.on_chat_end:
        await config.code.on_chat_end()

    if session.thread_id and session.has_first_interaction:
        await persist_user_session(session.thread_id, session.to_persistable())

    async def clear(_sid):
        if session := WebsocketSession.get(_sid):
            # Clean up the user session
            if session.id in user_sessions:
                user_sessions.pop(session.id)
            # Clean up the session
            await session.delete()

    if session.to_clear:
        await clear(sid)
    else:

        async def clear_on_timeout(_sid):
            await asyncio.sleep(config.project.session_timeout)
            await clear(_sid)

        asyncio.ensure_future(clear_on_timeout(sid))


@sio.on("switch_chat_profile")  # pyright: ignore [reportOptionalCall]
async def switch_chat_profile_event(sid, payload):
    """Manual switch from the profile selector.

    The flag is checked before init_ws_context so a disabled feature
    mutates nothing (upstream PR #3015 mutates the context regardless).
    Never blocks on the hook: perform_profile_switch launches it via
    create_task, because a handler parked on a multi-hour ask would starve
    this session's event loop — including the ask_reply that ask waits for.
    """
    if not config.features.hot_swap_chat_profile:
        return
    session = WebsocketSession.get(sid)
    if not session:
        return
    name = (payload or {}).get("chatProfile") or ""

    # Park until the handshake gate opens, the way _convert_orphan_ask_reply
    # does. The client flushes its send buffer BEFORE connection_successful
    # is emitted, so on a reconnect this event can arrive first — and the
    # resume branch of connection_successful replaces user_sessions[id]
    # wholesale and restores chat_profile from thread metadata, silently
    # rolling the switch back while the hook keeps running under the old
    # profile.
    try:
        await asyncio.wait_for(session.connection_inited.wait(), timeout=30)
    except TimeoutError, asyncio.CancelledError:
        logger.warning("switch_chat_profile: handshake gate never opened")
        return

    if WebsocketSession.get(sid) is not session:
        return

    init_ws_context(session)
    await perform_profile_switch(session, name, payload=None, source="client")


@sio.on("stop")  # pyright: ignore [reportOptionalCall]
async def stop(sid):
    if session := WebsocketSession.get(sid):
        context = init_ws_context(session)
        await Message(content="Task manually stopped.").send()

        if session.pending_ask is not None:
            session.pending_ask.cancel()
            # Free the slot right away so a follow-up ask (e.g. from
            # on_stop) isn't refused before the cancelled waiter's finally
            # block runs; the identity check there tolerates this.
            session.pending_ask = None
            await context.emitter.clear("clear_ask")

        if session.current_task:
            session.current_task.cancel()

        # Deliberately coarse: one button cancels BOTH the message task and
        # the on_thread_ready hook rather than guessing which one the user
        # meant. A cancelled hook re-offers on the next resume.
        if session.thread_ready_task:
            session.thread_ready_task.cancel()

        # Same for the on_profile_start hook: without this the stop button
        # leaves it running and its pending ask unquenched.
        if session.profile_start_task:
            session.profile_start_task.cancel()

        if config.code.on_stop:
            await config.code.on_stop()


def _is_convertible_text_reply(value) -> bool:
    """True when an orphaned ask_reply value is a text-ask user message.

    The gate must be strict — it runs BEFORE process_message: a file-ask
    reply is a list (``.get`` would raise), an element-ask reply is
    ``{**props, "submitted": True}`` with arbitrary app props that could
    smuggle a ``type``/``id`` past a weaker check and crash the conversion
    into an ErrorMessage.
    """
    if not isinstance(value, dict):
        return False
    if value.get("type") != "user_message":
        return False
    if "submitted" in value:
        return False
    if not isinstance(value.get("output"), str):
        return False
    if not value.get("createdAt"):
        return False
    try:
        if uuid.UUID(str(value.get("id"))).version != 4:
            return False
    except ValueError, TypeError, AttributeError:
        return False
    return True


async def _convert_orphan_ask_reply(session: WebsocketSession, step_dict, step_id):
    """Turn an orphaned text ask_reply into a regular incoming message.

    Parked on the session's handshake gate first: the client flushes its
    send buffer BEFORE emitting connection_successful, and converting into
    the half-initialized session would send the message into a fresh empty
    thread (or rename a resumed one via init_thread) and race
    on_chat_start/on_chat_resume.
    """
    try:
        await session.connection_inited.wait()

        context = init_ws_context(session)

        # Re-evaluate against the post-handshake state.
        if step_id is not None and step_id == session.last_resolved_ask_step_id:
            # Answered meanwhile through the legacy ack path.
            return
        pending = session.pending_ask
        if (
            pending is not None
            and step_id == pending.spec.step_id
            and not pending.future.done()
        ):
            # The very ask is (still) waiting after all — deliver the reply
            # instead of duplicating it as a message.
            session.last_resolved_ask_step_id = step_id
            pending.future.set_result(step_dict)
            return

        # A live successor ask (installed during the handshake by the
        # on_thread_ready task — or, in legacy apps, by a bare background
        # task) owns the UI: convert WITHOUT clear_ask and leave its slot
        # alone — the parked reply predates it and cannot be its
        # duplicate. Without a live successor, take the client out of the
        # stale ask mode.
        live_successor = pending is not None and pending.is_live
        if not live_successor:
            await context.emitter.clear("clear_ask")

        # Explicit new_message: a resume_thread snapshot has already wiped
        # the client's unpersisted local echo, and process_message emits
        # nothing itself — without this the rescued message would stay
        # invisible until the next reconnect. Clients upsert by id, so the
        # emit is idempotent where the echo is still alive.
        await context.emitter.send_step(step_dict)

        task = asyncio.create_task(
            process_message(session, {"message": step_dict, "fileReferences": None})
        )
        # Assigned only now, after the gate: an assignment at arrival time
        # would race the `session.current_task = task` in the on_chat_start
        # branches of connection_successful.
        session.current_task = task
        try:
            await task
        except asyncio.CancelledError:
            task.cancel()
            raise
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to convert an orphaned ask_reply into a message")


@sio.on("ask_reply")  # pyright: ignore [reportOptionalCall]
async def ask_reply(sid, payload):
    """Resolve the pending ask with the user's reply — or rescue the reply.

    Replies are plain events (not socket.io acks) so the client can buffer
    them across reconnections. A reply that cannot reach a live ask
    (server restart, ask timeout, stop) is NOT silently dropped: a text
    reply is converted into a regular incoming message (the same path a
    client_message takes), other payloads just clear the stale form. The
    user's input never silently disappears on a path where the server can
    still save it.
    """
    session = WebsocketSession.get(sid)
    if session is None:
        logger.warning("ask_reply received for an unknown session; ignoring")
        return

    payload = payload or {}
    step_id = payload.get("stepId")
    value = payload.get("value")

    pending_ask = session.pending_ask

    # 1) The pending ask can take this reply — deliver it, even when the
    # deadline has technically passed: the wait_for timer starts after the
    # deadline is set, so an "expired but still in the slot" reply used to
    # be accepted and converting it instead would hand the app BOTH a
    # timeout (None) and a duplicate on_message.
    if (
        pending_ask is not None
        and step_id == pending_ask.spec.step_id
        and not pending_ask.future.done()
    ):
        session.last_resolved_ask_step_id = step_id
        pending_ask.future.set_result(value)
        return

    # 2) A LIVE ask for another step owns the UI — a stale reply must
    # neither resolve it, nor clear its form, nor spawn a parallel
    # on_message while the server keeps waiting on it.
    if (
        pending_ask is not None
        and pending_ask.is_live
        and step_id != pending_ask.spec.step_id
    ):
        logger.warning(
            "ask_reply received for step %s but step %s is pending; ignoring",
            step_id,
            pending_ask.spec.step_id,
        )
        return

    # From here on the reply is orphaned: no ask, or an already
    # answered/cancelled one. The pending_ask slot empties milliseconds
    # after an answer is accepted, so redelivery of an accepted reply (the
    # send buffer after a blip, a second tab) lands here too — the recorded
    # last resolved step id is what tells the two cases apart.
    if step_id is not None and step_id == session.last_resolved_ask_step_id:
        logger.warning(
            "ask_reply for step %s was already answered; ignoring the redelivery",
            step_id,
        )
        await init_ws_context(session).emitter.clear("clear_ask")
        return

    if not _is_convertible_text_reply(value):
        # A click on a dead action/element/file form: nothing to rescue —
        # just take the client out of the stale ask mode.
        logger.warning(
            "Orphaned non-text ask_reply for step %s; clearing the stale form",
            step_id,
        )
        await init_ws_context(session).emitter.clear("clear_ask")
        return

    logger.warning(
        "Orphaned text ask_reply for step %s; rescuing it as a regular message",
        step_id,
    )
    step_dict = dict(value)
    # Inherited from the dead ask's step — must not leak into the thread.
    step_dict["parentId"] = None

    task = asyncio.create_task(_convert_orphan_ask_reply(session, step_dict, step_id))
    parked = session.deferred_ask_reply_tasks
    parked.append(task)
    task.add_done_callback(lambda t: t in parked and parked.remove(t))


async def process_message(session: WebsocketSession, payload: MessagePayload):
    """Process a message from the user."""
    try:
        context = init_ws_context(session)
        await context.emitter.task_acquire()
        message = await context.emitter.process_message(payload)

        if config.code.on_message:
            await asyncio.sleep(0.001)
            await config.code.on_message(message)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception(e)
        await ErrorMessage(
            author="Error", content=str(e) or e.__class__.__name__
        ).send()
    finally:
        await context.emitter.task_release()


@sio.on("edit_message")  # pyright: ignore [reportOptionalCall]
async def edit_message(sid, payload: MessagePayload):
    """Handle a message sent by the User."""
    session = WebsocketSession.require(sid)
    context = init_ws_context(session)

    messages = chat_context.get()

    orig_message = None

    for message in messages:
        if orig_message:
            await message.remove()

        if message.id == payload["message"]["id"]:
            message.content = payload["message"]["output"]
            await message.update()
            orig_message = message

    if orig_message is None:
        # The edited message is not in this session's context — e.g. the page
        # reconnected with the same session id after the server already timed
        # the session out. Ignore the edit instead of handing None to the
        # app's on_message. (The leak this fix removed used to mask the case:
        # the fresh session silently adopted the undeleted context.)
        return

    # Acquire/release must stay paired — an unpaired acquire poisons the
    # owner counter for the rest of the session; hence the acquire lives
    # inside the try, and the whole block is skipped without an on_message
    # to end it. (The old code emitted task_start here even without an
    # on_message: a stuck indicator, gone now.)
    if config.code.on_message:
        try:
            await context.emitter.task_acquire()
            await config.code.on_message(orig_message)
        except asyncio.CancelledError:
            pass
        finally:
            await context.emitter.task_release()


@sio.on("message_favorite")  # pyright: ignore [reportOptionalCall]
async def message_favorite(sid, payload: MessagePayload):
    """Handle a message favorite toggle."""
    session = WebsocketSession.require(sid)
    context = init_ws_context(session)
    data_layer = get_data_layer()

    if not config.features.favorites or not session.user:
        return

    payload_message = payload["message"]
    payload_metadata = payload_message.get("metadata") or {}
    favorite = bool(payload_metadata.get("favorite", False))

    step_dict = None

    if favorite:
        for message in chat_context.get():
            if message.id == payload_message["id"]:
                message.metadata = message.metadata or {}
                message.metadata["favorite"] = favorite
                step_dict = message.to_dict()
                break
    elif data_layer:
        favorites = await data_layer.get_favorite_steps(session.user.id)
        for fav in favorites:
            if fav["id"] == payload_message["id"]:
                step_dict = fav
                break

    if step_dict is None:
        logger.error("Could not find step to update favorite status.")
        return

    created_at = step_dict.get("createdAt")
    if created_at and not created_at.endswith("Z"):
        step_dict["createdAt"] = f"{created_at}Z"

    if data_layer:
        step_dict = await data_layer.set_step_favorite(step_dict, favorite)

    await context.emitter.update_step(step_dict)
    await fetch_favorites(sid)


@sio.on("fetch_favorites")  # pyright: ignore [reportOptionalCall]
async def fetch_favorites(sid):
    session = WebsocketSession.require(sid)
    context = init_ws_context(session)
    if session.user and config.features.favorites:
        if data_layer := get_data_layer():
            favorites = await data_layer.get_favorite_steps(session.user.id)
            await context.emitter.set_favorites(favorites)


async def _deliver_to_pending_text_ask(session, payload) -> bool:
    """Answer a live TEXT ask with an incoming message; True when it did.

    The mirror of the orphaned-ask_reply rescue above. Ask mode lives only
    in the client's `askUser` atom, and the composer picks the route from
    it (`if (askUser) onReply else onSubmit`) — for a text ask it is not
    even disabled. Every path that empties the atom while the server still
    waits (an `ask` emit dropped in a dying socket, `clear_ask`, the window
    after a page load before `restore_pending_ask`) sends the user's answer
    here instead, where it used to start a parallel `on_message` and leave
    the ask hanging for its full timeout — blocking every later ask of the
    session, HITL confirmations included.

    Deliberately narrow: only a text ask, and only a payload an ask reply
    can carry. `future.done()` rather than `is_live` mirrors branch 1 of
    the ask_reply handler — an expired ask still holding the slot must take
    the answer, or the app gets both a timeout and a duplicate message.
    """
    pending = session.pending_ask
    if pending is None or pending.future.done():
        return False

    if pending.spec.type != "text":
        # The composer is disabled for file/action/element asks, but text
        # still arrives from starters, custom elements and the copilot
        # bridge, none of which look at the ask state. A text value cannot
        # answer those specs, so it stays a regular message.
        logger.warning(
            "client_message arrived while a %s ask is pending for session %s; "
            "handling it as a regular message",
            pending.spec.type,
            session.id,
        )
        return False

    payload = payload or {}
    step_dict = payload.get("message")

    if payload.get("fileReferences"):
        logger.info("Message with attachments during a text ask; not an answer")
        return False
    if isinstance(step_dict, dict) and (
        step_dict.get("command") or step_dict.get("modes")
    ):
        # Only process_message reads these (Message.from_dict); delivered as
        # an ask reply they would be silently dropped.
        logger.info("Message with a command/modes during a text ask; not an answer")
        return False
    if not _is_convertible_text_reply(step_dict):
        return False

    # The client echoed this message locally WITHOUT a parentId (that is
    # added only on the replyMessage path), and send_ask_user emits nothing
    # of its own — so stamp the parent and re-send the step, the way the
    # orphan conversion does. Clients upsert by id.
    step_dict = cast(
        StepDict, {**step_dict, "parentId": pending.step_dict.get("parentId")}
    )
    await init_ws_context(session).emitter.send_step(step_dict)

    session.last_resolved_ask_step_id = pending.spec.step_id
    pending.future.set_result(step_dict)
    logger.info(
        "client_message delivered to the pending ask of step %s", pending.spec.step_id
    )
    return True


@sio.on("client_message")  # pyright: ignore [reportOptionalCall]
async def message(sid, payload: MessagePayload):
    """Handle a message sent by the User."""
    session = WebsocketSession.require(sid)

    if await _deliver_to_pending_text_ask(session, payload):
        return

    task = asyncio.create_task(process_message(session, payload))
    session.current_task = task


@sio.on("window_message")  # pyright: ignore [reportOptionalCall]
async def window_message(sid, data):
    """Handle a message send by the host window."""
    session = WebsocketSession.require(sid)
    init_ws_context(session)

    if config.code.on_window_message:
        try:
            await config.code.on_window_message(data)
        except asyncio.CancelledError:
            pass


@sio.on("audio_start")  # pyright: ignore [reportOptionalCall]
async def audio_start(sid):
    """Handle audio init."""
    session = WebsocketSession.require(sid)

    context = init_ws_context(session)
    config: ChainlitConfig = session.get_config()  # type: ignore

    if config.features.audio and config.features.audio.enabled:
        connected = bool(await config.code.on_audio_start())
        connection_state = "on" if connected else "off"
        await context.emitter.update_audio_connection(connection_state)


@sio.on("audio_chunk")
async def audio_chunk(sid, payload: InputAudioChunkPayload):
    """Handle an audio chunk sent by the user."""
    session = WebsocketSession.require(sid)

    init_ws_context(session)

    config: ChainlitConfig = session.get_config()

    if (
        config.features.audio
        and config.features.audio.enabled
        and config.code.on_audio_chunk
    ):
        asyncio.create_task(config.code.on_audio_chunk(InputAudioChunk(**payload)))


@sio.on("audio_end")
async def audio_end(sid):
    """Handle the end of the audio stream."""
    session = WebsocketSession.require(sid)

    try:
        context = init_ws_context(session)
        await context.emitter.task_acquire()

        if not session.has_first_interaction:
            session.has_first_interaction = True
            create_persist_task(
                context.emitter.init_thread("audio"), thread_id=session.thread_id
            )

        config: ChainlitConfig = session.get_config()  # type: ignore

        if config.features.audio and config.features.audio.enabled:
            await config.code.on_audio_end()

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception(e)
        await ErrorMessage(
            author="Error", content=str(e) or e.__class__.__name__
        ).send()
    finally:
        await context.emitter.task_release()


@sio.on("chat_settings_change")
async def change_settings(sid, settings: Dict[str, Any]):
    """Handle change settings submit from the UI."""
    context = init_ws_context(sid)

    for key, value in settings.items():
        context.session.chat_settings[key] = value

    if config.code.on_settings_update:
        await config.code.on_settings_update(settings)


@sio.on("chat_settings_edit")
async def edit_settings(sid, settings: Dict[str, Any]):
    """Handle change settings edit from the UI (on the fly)."""
    init_ws_context(sid)

    if config.code.on_settings_edit:
        await config.code.on_settings_edit(settings)
