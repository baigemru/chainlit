"""The route, and the ways two loops on one socket go wrong.

These cover the connection's own behaviour: what the first frame has to
be, which failures close and which merely report, and that an ordinary
disconnect is not an error. The conversation-level behaviour the handshake
performs is stated in ``tests/socketspec`` and covered directly in
``test_handshake.py``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest
from litestar.enums import ScopeType
from litestar.exceptions import WebSocketDisconnect
from litestar.middleware import ASGIMiddleware
from litestar.testing import create_test_client
from litestar.types import ASGIApp, Receive, Scope, Send

from chainlit.protocol.codec import MAX_FRAME_BYTES, CloseCode
from chainlit.protocol.server import Heartbeat
from chainlit.ws.connection import make_websocket_handler
from chainlit.ws.registry import SessionRegistry
from chainlit.ws.session import Session


class _Identity:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier


class _PutUser(ASGIMiddleware):
    """Leave a user in the scope, the way an auth middleware does.

    Its scopes include the websocket one, which is the whole reason the
    real ``JWTCookieAuth`` works on an upgrade: a browser cannot put an
    Authorization header on one, and a cookie it can.
    """

    scopes = (ScopeType.WEBSOCKET,)

    def __init__(self, user: Any) -> None:
        self.user = user

    async def handle(
        self, scope: Scope, receive: Receive, send: Send, next_app: ASGIApp
    ) -> None:
        scope["user"] = self.user
        await next_app(scope, receive, send)


def build(
    registry: Optional[SessionRegistry] = None,
    *,
    user: Optional[_Identity] = None,
    heartbeat_ms: int = 20_000,
) -> Any:
    """The route and its registry, plus whatever middleware the case needs.

    Authentication is not exercised here on purpose: it runs before
    ``accept()``, so a refusal from it is a failed upgrade rather than a
    close code, and none of the cases below is about that.
    """
    registry = registry if registry is not None else SessionRegistry()
    handler = make_websocket_handler(
        registry=registry,
        make_session=lambda sid, hello_frame, u: Session(
            id=sid,
            thread_id=hello_frame.thread_id,
            chat_profile=hello_frame.chat_profile,
            client_type=hello_frame.client_type,
            user=u,
        ),
        heartbeat_ms=heartbeat_ms,
    )
    middleware = [_PutUser(user)] if user is not None else []
    return handler, middleware, registry


def hello(**overrides: Any) -> str:
    frame: Dict[str, Any] = {"t": "hello", "sessionId": "s1"}
    frame.update(overrides)
    return json.dumps(frame)


def close_code_of(ws: Any, *, limit: int = 200, timeout: float = 5.0) -> int:
    """Read frames until the socket closes, and return the code it closed on."""
    for _ in range(limit):
        try:
            ws.receive(timeout=timeout)
        except WebSocketDisconnect as disconnect:
            return disconnect.code
    raise AssertionError("the connection never closed")


def open_session(ws: Any, *, timeout: float = 5.0) -> List[str]:
    """Say hello and read the whole handshake, returning its tags.

    The replay runs *concurrently* with the reader -- that is what lets an
    answer typed before a reload arrive during it -- so frames keep coming
    after ``session.ready``. It always ends with the spinner, because a
    level-triggered boolean is only honest once everything else has been
    said, and that is what makes this loop terminate.
    """
    ws.send_text(hello())
    tags: List[str] = []
    for _ in range(50):
        tags.append(json.loads(ws.receive_text(timeout=timeout))["t"])
        if tags[-1] == "task.indicator":
            return tags
    raise AssertionError(f"the handshake never finished: {tags}")


# ------------------------------------------------------------- the opening


def test_a_hello_opens_the_session() -> None:
    handler, middleware, registry = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_text(hello(threadId="t1"))
        ready = json.loads(ws.receive_text(timeout=5))

    assert ready["t"] == "session.ready"
    assert ready["sessionId"] == "s1"
    # On every branch, not only the first interaction: a reload into a
    # session that already had one used to come back with no thread id, and
    # the feedback buttons stayed dead for the rest of the conversation.
    assert ready["threadId"] == "t1"
    assert registry.get("s1") is not None


def test_a_first_frame_that_is_not_hello_closes_the_connection() -> None:
    """There is no session yet to report an error against."""
    handler, middleware, _ = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_text(json.dumps({"t": "stop"}))
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive(timeout=5)

    assert excinfo.value.code == CloseCode.BAD_HANDSHAKE


def test_a_malformed_first_frame_closes_the_connection() -> None:
    handler, middleware, _ = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_text("{not json")
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive(timeout=5)

    assert excinfo.value.code == CloseCode.BAD_HANDSHAKE


def test_a_session_belonging_to_another_user_is_refused() -> None:
    registry = SessionRegistry()
    registry.register(Session(id="s1"), user_identifier="ada", thread_id="t1")
    handler, middleware, _ = build(registry, user=_Identity("grace"))

    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_text(hello(threadId="t1"))
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive(timeout=5)

    assert excinfo.value.code == CloseCode.SESSION_FORBIDDEN
    # And nothing else: "that session exists but is not yours" says it exists.
    assert registry.get("s1") is not None


# --------------------------------------------------------- once it is open


def test_an_unknown_tag_is_reported_and_the_socket_stays_open() -> None:
    """A frame this release does not understand is not the user's problem.

    The error is addressed to the client's next version; taking away the
    conversation to deliver it would be a strange way to say so.
    """
    handler, middleware, _ = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        assert open_session(ws)[0] == "session.ready"
        ws.send_text(json.dumps({"t": "no.such.tag"}))
        error = json.loads(ws.receive_text(timeout=5))
        # Still usable afterwards.
        ws.send_text(json.dumps({"t": "hb.ack", "seq": 1}))

    assert error["t"] == "error"
    assert error["code"] == "unknown_tag"


def test_a_malformed_frame_is_reported_and_the_socket_stays_open() -> None:
    handler, middleware, _ = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        assert open_session(ws)[0] == "session.ready"
        ws.send_text("{not json")
        error = json.loads(ws.receive_text(timeout=5))

    assert error["t"] == "error"
    assert error["code"] == "bad_message"


def test_an_oversized_inbound_frame_closes_the_connection() -> None:
    """Reported rather than truncated: half a message is not a message."""
    handler, middleware, _ = build()
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        assert open_session(ws)[0] == "session.ready"
        ws.send_text(json.dumps({"t": "stop", "pad": "x" * (MAX_FRAME_BYTES + 16)}))
        code = close_code_of(ws)

    assert code == CloseCode.FRAME_TOO_LARGE


def test_closing_a_tab_is_not_an_internal_server_error() -> None:
    """The ordinary case.

    A reader exception escaping the task group would be reported to the
    user as a 4500, and anyio wraps even one child exception in a group --
    so the naive ``except WebSocketDisconnect`` never fires and this is the
    test that notices.
    """
    handler, middleware, registry = build()
    with create_test_client(route_handlers=[handler], middleware=middleware) as client:
        with client.websocket_connect("/ws") as ws:
            assert open_session(ws)[0] == "session.ready"

    entry = registry.get("s1")
    assert entry is not None, "the session did not outlive its socket"
    assert entry.connected is False


def test_the_session_outlives_the_socket_with_its_queue_intact() -> None:
    """A dead socket is not a closed queue: the conversation is still there."""
    handler, middleware, registry = build()
    with create_test_client(route_handlers=[handler], middleware=middleware) as client:
        with client.websocket_connect("/ws") as ws:
            open_session(ws)

    entry = registry.get("s1")
    assert entry is not None
    session = entry.session
    assert isinstance(session, Session)
    assert session.outbound.closed is False


def test_a_socket_that_stops_answering_the_probe_is_closed() -> None:
    """A silent socket is indistinguishable from a healthy one.

    Nothing is written to a session parked on a question, so without a
    probe it can sit against a peer that vanished hours ago -- holding its
    place in the registry, and shielding its own steps from a resume that
    should have reclaimed them.
    """
    handler, middleware, _ = build(heartbeat_ms=40)
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        open_session(ws)
        code = close_code_of(ws)

    assert code == CloseCode.HEARTBEAT_TIMEOUT


def test_answering_the_probe_keeps_the_connection() -> None:
    handler, middleware, _ = build(heartbeat_ms=40)
    with (
        create_test_client(route_handlers=[handler], middleware=middleware) as client,
        client.websocket_connect("/ws") as ws,
    ):
        open_session(ws)
        for _ in range(4):
            frame = json.loads(ws.receive_text(timeout=5))
            assert frame["t"] == "hb", frame
            ws.send_text(json.dumps({"t": "hb.ack", "seq": frame.get("seq", 0)}))


def test_a_kept_sessions_backlog_follows_the_ready_frame() -> None:
    """What the last socket never took is delivered -- after ``session.ready``.

    A session kept across a gap may hold frames produced while nobody was
    listening. They are a continuation, not the opening: the client starts
    the conversation on ``session.ready`` and would otherwise see them as
    noise before it, or -- for a level frame like the spinner -- as a stale
    truth ahead of the real one.
    """
    handler, middleware, registry = build()
    with create_test_client(route_handlers=[handler], middleware=middleware) as client:
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
        entry = registry.get("s1")
        assert entry is not None
        entry.session.send(Heartbeat(seq=99))  # queued while disconnected
        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(pageLoad=False))
            tags = [json.loads(ws.receive_text(timeout=5))["t"] for _ in range(2)]

    assert tags[0] == "session.ready"
    assert "hb" in tags
