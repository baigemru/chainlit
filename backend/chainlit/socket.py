import asyncio
import json
import uuid
from typing import Any, Dict, Literal, Optional, Tuple, TypedDict, Union
from urllib.parse import unquote

from starlette.requests import cookie_parser
from typing_extensions import NotRequired, TypeAlias

import chainlit.transit as transit
from chainlit.auth import (
    get_current_user,
    get_token_from_cookies,
    require_login,
)
from chainlit.chat_context import chat_context
from chainlit.config import ChainlitConfig, config
from chainlit.context import init_ws_context
from chainlit.data import get_data_layer
from chainlit.emitter import _make_legacy_ask_ack
from chainlit.logger import logger
from chainlit.message import ErrorMessage, Message
from chainlit.resume_policy import split_resume_delete
from chainlit.server import sio
from chainlit.session import ClientType, WebsocketSession
from chainlit.types import (
    InputAudioChunk,
    InputAudioChunkPayload,
    MessagePayload,
)
from chainlit.user import PersistedUser, User
from chainlit.user_session import user_sessions

WSGIEnvironment: TypeAlias = dict[str, Any]


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


async def resume_thread(session: WebsocketSession):
    data_layer = get_data_layer()
    if not data_layer or not session.user or not session.thread_id_to_resume:
        return
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
            has_live_ask = (
                existing.pending_ask is not None and existing.pending_ask.is_live
            )
            has_live_task = (
                existing.current_task is not None and not existing.current_task.done()
            )
            drop_stale_session = not has_live_ask and not has_live_task

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

    WebsocketSession(
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


async def cleanup_resume_delete_steps(
    context, thread_id: str, doomed_steps: list, doomed_elements: list
):
    """Delete the doomed steps from the data layer and from the client.

    Must run after ``has_first_interaction`` is set (``delete_step`` /
    ``delete_element`` are wrapped in ``queue_until_user_message`` and would
    hang in the queue otherwise) and after the ``resume_thread`` emit (the
    client rebuilds the whole feed on ``resume_thread``, so an earlier
    ``delete_message`` would be lost; the client already drew the history
    from REST, which makes the emit a mandatory safety net). Every
    operation is individually guarded: a double resume, a race between two
    tabs or an already-deleted step must not crash the resume.
    """
    if not doomed_steps:
        return

    data_layer = get_data_layer()

    elements_by_step: Dict[Any, list] = {}
    for element in doomed_elements:
        elements_by_step.setdefault(element.get("forId"), []).append(element)

    for step in doomed_steps:
        step_id = step.get("id")
        if data_layer:
            # Elements first and explicitly: not every data layer cascades
            # element deletion from delete_step (DynamoDB does not); where
            # it does, the second delete is idempotent. If any element
            # deletion fails, the step is kept — once the step is gone its
            # elements would be orphaned forever (their forId never enters
            # the doomed set again); keeping it leaves the state retryable,
            # the next resume finishes the job.
            elements_deleted = True
            for element in elements_by_step.get(step_id, []):
                try:
                    await data_layer.delete_element(element["id"], thread_id)
                except Exception as e:
                    elements_deleted = False
                    logger.warning(
                        f"resume=delete: failed to delete element "
                        f"{element.get('id')} of step {step_id}: {e}"
                    )
            if elements_deleted:
                try:
                    await data_layer.delete_step(step_id)
                except Exception as e:
                    logger.warning(
                        f"resume=delete: failed to delete step {step_id}: {e}"
                    )
            else:
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


async def restore_pending_ask(context, client_has_ui_state: bool):
    """Rebuild a live pending ask in the UI, or clear the ask state.

    Must run after the resume/transit branches of ``connection_successful``:
    ``resume_thread`` replaces the client's message/element state wholesale
    and would wipe a form re-emitted before it.
    """
    session = context.session

    if not client_has_ui_state and session.restored:
        # The page was reloaded and the client lost everything: replay the
        # transcript (a paid flow's results live above the form) together
        # with the elements attached to its messages. On a plain transport
        # reconnect the client still has all of it — skip. This also runs
        # when the session was kept for a still-running task without a
        # pending ask.
        try:
            transcript = chat_context.get()
        except Exception:
            transcript = []
        for message in transcript:
            try:
                await context.emitter.send_step(message.to_dict())
                for element in getattr(message, "elements", None) or []:
                    await context.emitter.send_element(element.to_dict())
            except Exception:
                logger.debug(
                    "Failed to replay a transcript message on reconnect",
                    exc_info=True,
                )

    pending_ask = session.pending_ask
    if pending_ask is None or not pending_ask.is_live:
        await context.emitter.clear("clear_ask")
        return

    if not client_has_ui_state:
        # The form's own actions/element; a live element may hold newer
        # props than any snapshot, so it is serialized only now.
        for action_dict in pending_ask.restore_actions:
            await context.emitter.emit("action", action_dict)
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
        await emit_ask(ask_payload, _make_legacy_ask_ack(pending_ask.future))
    else:
        await context.emitter.emit("ask", ask_payload)


@sio.on("connection_successful")  # pyright: ignore [reportOptionalCall]
async def connection_successful(sid):
    context = init_ws_context(sid)

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

    try:
        if context.session.restored and not context.session.has_first_interaction:
            await apply_transit_message(context)
            await send_parent_thread(context)
            if config.code.on_chat_start and not context.session.chat_started:
                context.session.chat_started = True
                task = asyncio.create_task(config.code.on_chat_start())
                context.session.current_task = task
            return

        if context.session.thread_id_to_resume and config.code.on_chat_resume:
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
                # Only a true resume of a dead session qualifies — a
                # restored session re-enters this branch on F5 (its
                # thread_id_to_resume is never cleared), and deleting then
                # would kill live flagged messages of a running task.
                doomed_steps: list = []
                doomed_elements: list = []
                if not context.session.restored:
                    thread, doomed_steps, doomed_elements = split_resume_delete(thread)

                context.session.has_first_interaction = True
                await context.emitter.emit(
                    "first_interaction",
                    {"interaction": "resume", "thread_id": thread.get("id")},
                )
                await config.code.on_chat_resume(thread)

                for step in thread.get("steps", []):
                    if "message" in step["type"]:
                        chat_context.add(Message.from_dict(step))

                await context.emitter.resume_thread(thread)

                await cleanup_resume_delete_steps(
                    context, thread.get("id"), doomed_steps, doomed_elements
                )
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
        await restore_pending_ask(context, client_has_ui_state)


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

        if config.code.on_stop:
            await config.code.on_stop()


@sio.on("ask_reply")  # pyright: ignore [reportOptionalCall]
async def ask_reply(sid, payload):
    """Resolve the pending ask with the user's reply.

    Replies are plain events (not socket.io acks) so the client can buffer
    them across reconnections. Stale or duplicate replies are ignored: the
    send buffer may redeliver a reply, and a user may click again after a
    re-emitted ask.
    """
    session = WebsocketSession.get(sid)
    if session is None:
        logger.warning("ask_reply received for an unknown session; ignoring")
        return

    pending_ask = session.pending_ask
    if pending_ask is None:
        logger.warning("ask_reply received but no ask is pending; ignoring")
        return

    step_id = (payload or {}).get("stepId")
    if step_id != pending_ask.spec.step_id:
        logger.warning(
            "ask_reply received for step %s but step %s is pending; ignoring",
            step_id,
            pending_ask.spec.step_id,
        )
        return

    if pending_ask.future.done():
        logger.warning("ask_reply received for an already answered ask; ignoring")
        return

    pending_ask.future.set_result((payload or {}).get("value"))


async def process_message(session: WebsocketSession, payload: MessagePayload):
    """Process a message from the user."""
    try:
        context = init_ws_context(session)
        await context.emitter.task_start()
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
        await context.emitter.task_end()


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

    await context.emitter.task_start()

    if config.code.on_message:
        try:
            await config.code.on_message(orig_message)
        except asyncio.CancelledError:
            pass
        finally:
            await context.emitter.task_end()


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


@sio.on("client_message")  # pyright: ignore [reportOptionalCall]
async def message(sid, payload: MessagePayload):
    """Handle a message sent by the User."""
    session = WebsocketSession.require(sid)

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
        await context.emitter.task_start()

        if not session.has_first_interaction:
            session.has_first_interaction = True
            asyncio.create_task(context.emitter.init_thread("audio"))

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
        await context.emitter.task_end()


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
