"""``cl.deliver_to_thread``: a message that arrives from outside the chat.

The two roads it picks between are the whole subject. A job that finishes
while the person is still looking at the conversation has a socket to talk
on; the same job finishing after they closed the tab has a row and nothing
else. Which one it is changes between the moment the job starts and the
moment it ends, so the application must not be the one deciding -- that was
the shape of the consumer's copy, and it carried a piece of the transport
with it.

Driven from inside the application, because that is where an application's
own route runs: the same loop as the socket, the same registry, the same
persistence. The delivery is made from ``on_message`` here only because it
is the cheapest place to stand; nothing in the entry reads the context.
"""

from __future__ import annotations

import json
from typing import Any, List

import pytest
from litestar.testing import create_test_client

import chainlit as cl
from chainlit.plugin import ChainlitPlugin
from chainlit.security import ChainlitAuth
from tests.persistence.conftest import database_url  # noqa: F401 - fixture re-export
from tests.test_runner import (  # noqa: F401 - fixture re-export
    frontend_dir,
    open_session,
    read_until,
)
from tests.test_runner_persistence import (  # noqa: F401 - fixture re-export
    ALICE,
    auth,
    db_url,
    login,
    make_plugin,
    message,
    plugin,
    seed_user,
    thread_detail,
    wait_for_thread,
)

pytestmark = pytest.mark.usefixtures("test_config")

ORPHAN = "11111111-1111-4111-8111-111111111111"
STEP = "22222222-2222-4222-8222-222222222222"
DONE = "the turn is over"


def run_turn(ws: Any, text: str) -> List[dict]:
    """Send a message and read to the marker the handler ends on.

    Not ``send_and_read_reply``: the first ``step.upsert`` of a turn is the
    user's own message coming back, and what these cases are waiting for is
    something the handler did afterwards -- sometimes in another thread
    entirely, where this socket sees nothing at all.
    """
    ws.send_text(message(text))
    frames: List[dict] = []
    for _ in range(50):
        frame = json.loads(ws.receive_text(timeout=5.0))
        frames.append(frame)
        if frame["t"] == "step.upsert" and frame["step"].get("output") == DONE:
            return frames
    raise AssertionError(f"the turn never ended: {[f['t'] for f in frames]}")


def test_a_live_session_is_told_on_its_socket(
    plugin: ChainlitPlugin,  # noqa: F811 - the imported fixture
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """The person is in the chat, so the message lands on the screen.

    Not a row quietly written beside them: the frame goes out, and through
    the session's own writer as well, which is what keeps it in the
    transcript a reload replays.
    """
    seed_user(db_url, ALICE)
    outcomes: List[str] = []

    async def on_message(msg: cl.Message) -> None:
        here = cl.context.session.thread_id
        assert here is not None
        outcomes.append(
            await cl.deliver_to_thread(
                here, "the run finished", metadata={"anchor": "none"}
            )
        )
        await cl.Message(content=DONE).send()

    test_config.code.on_message = on_message

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            thread_id = open_session(ws)[0]["threadId"]
            frames = run_turn(ws, "go")

        # Inside the client: the session writer commits on its own schedule,
        # and the poll has to happen while it is still running.
        detail = wait_for_thread(
            db_url,
            thread_id,
            lambda d: "the run finished" in [step.output for step in d.steps],
        )

    assert outcomes == ["live"]
    delivered = [
        f["step"]
        for f in frames
        if f["t"] == "step.upsert" and f["step"].get("output") == "the run finished"
    ]
    assert delivered, [f["t"] for f in frames]
    assert delivered[0]["metadata"] == {"anchor": "none"}
    assert detail.user_identifier == ALICE


def test_a_thread_nobody_is_in_gets_a_row_with_its_owner(
    plugin: ChainlitPlugin,  # noqa: F811 - the imported fixture
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """The tab is closed. The message still has to reach the history.

    Owner before step, and that ordering is the engine's rather than the
    caller's: ``steps.save`` creates the thread row behind a step whose
    thread does not exist yet, and a row created that way carries no user --
    so the conversation would never appear in anybody's list.
    """
    seed_user(db_url, ALICE)
    outcomes: List[str] = []

    async def on_message(msg: cl.Message) -> None:
        outcomes.append(
            await cl.deliver_to_thread(
                ORPHAN,
                "the run finished",
                author="Reporter",
                id=STEP,
                user_identifier=ALICE,
            )
        )
        await cl.Message(content=DONE).send()

    test_config.code.on_message = on_message

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
            run_turn(ws, "go")

    assert outcomes == ["stored"]
    detail = thread_detail(db_url, ORPHAN)
    assert detail is not None
    assert detail.user_identifier == ALICE
    assert detail.updated_at is not None, "a thread that was written to sorts as of now"
    assert [step.output for step in detail.steps] == ["the run finished"]
    assert detail.steps[0].name == "Reporter"
    assert detail.steps[0].type == "assistant_message"


def test_a_delivery_that_repeats_replaces_rather_than_doubles(
    plugin: ChainlitPlugin,  # noqa: F811 - the imported fixture
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """The handle a caller has on a webhook that may arrive twice.

    The engine does not deduplicate: whether a second delivery is the same
    message is the caller's question. What it does is honour the answer --
    a stable ``id`` upserts.
    """
    seed_user(db_url, ALICE)

    async def on_message(msg: cl.Message) -> None:
        await cl.deliver_to_thread(ORPHAN, msg.content, id=STEP, user_identifier=ALICE)
        await cl.Message(content=DONE).send()

    test_config.code.on_message = on_message

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
            run_turn(ws, "first try")
            run_turn(ws, "second try")

    detail = thread_detail(db_url, ORPHAN)
    assert detail is not None
    assert [step.output for step in detail.steps] == ["second try"]


def test_without_a_session_or_a_database_it_refuses_rather_than_drops(
    test_config: Any,
    frontend_dir: Any,  # noqa: F811
) -> None:
    """Answering "delivered" about a message nobody will ever see is worse
    than raising: the caller has somebody waiting for that result."""
    bare = ChainlitPlugin(test_config, persistence=None, frontend_dir=frontend_dir)
    raised: List[str] = []

    async def on_message(msg: cl.Message) -> None:
        try:
            await cl.deliver_to_thread(ORPHAN, "nowhere to go")
        except RuntimeError as error:
            raised.append(str(error))
        await cl.Message(content=DONE).send()

    test_config.code.on_message = on_message

    with create_test_client(plugins=[bare]) as client:
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
            run_turn(ws, "go")

    assert raised, "the refusal never happened"
    assert "no live session" in raised[0]
