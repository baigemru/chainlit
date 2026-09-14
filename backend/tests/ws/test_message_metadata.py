"""What a client puts in a message's metadata is what the app reads back.

The composer stamps ``location`` there and a custom element may add a
``payload`` -- structured intent, so the application can branch on a key
instead of string-matching the prose a button happened to send. Nothing on
the way in is supposed to touch that dict: ``Step.metadata`` is already on
the wire, ``Message.from_dict`` copies it, and ``steps.metadata`` is the
column it lands in. These tests pin that end to end, because the day one
of those three starts normalising the dict the only symptom is an
application that stops recognising its own buttons.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from litestar.testing import create_test_client

import chainlit as cl
from chainlit.plugin import ChainlitPlugin
from chainlit.security import ChainlitAuth
from tests.test_runner import (  # noqa: F401 - fixture re-export
    frontend_dir,
    open_session,
    read_until,
)
from tests.test_runner_persistence import (  # noqa: F401 - fixture re-export
    ALICE,
    auth,
    database_url,
    db_url,
    login,
    make_plugin,
    seed_user,
    wait_for_thread,
)

pytestmark = pytest.mark.usefixtures("test_config")

PAYLOAD: Dict[str, Any] = {
    "intent": "analyze_results",
    "shortlistId": 7,
    "skus": ["a", "b"],
}
LOCATION = "https://app.test/chat"


def message(
    text: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    id: Optional[str] = None,
) -> str:
    frame: Dict[str, Any] = {
        "id": id or str(uuid.uuid4()),
        "type": "user_message",
        "output": text,
        "name": "User",
        "createdAt": "2026-09-14T00:00:00.000000Z",
    }
    if metadata is not None:
        frame["metadata"] = metadata
    return json.dumps({"t": "message.send", "message": frame})


def send_and_read_reply(ws: Any, text: str, **kw: Any) -> List[dict]:
    """Send a message and read past the echo up to the assistant's reply."""
    ws.send_text(message(text, **kw))
    frames = read_until(ws, "step.upsert")
    while frames[-1]["step"].get("type") == "user_message":
        frames.extend(read_until(ws, "step.upsert"))
    return frames


@pytest.fixture
def plain_plugin(test_config: Any, frontend_dir: Path) -> ChainlitPlugin:  # noqa: F811
    """No database behind it: the seam alone."""
    return ChainlitPlugin(test_config, frontend_dir=frontend_dir, auth=None)


def test_the_metadata_payload_reaches_on_message(
    plain_plugin: ChainlitPlugin, test_config: Any
) -> None:
    seen: List[Any] = []

    async def on_message(msg: cl.Message) -> None:
        seen.append(msg.metadata)
        await cl.Message(content="ok").send()

    test_config.code.on_message = on_message

    with (
        create_test_client(plugins=[plain_plugin]) as client,
        client.websocket_connect("/ws") as ws,
    ):
        open_session(ws)
        send_and_read_reply(
            ws, "разбери выдачу", metadata={"location": LOCATION, "payload": PAYLOAD}
        )
        # The payload is optional: a message sent without one is not a
        # message with an empty one, and neither is an error.
        send_and_read_reply(ws, "плитка")

    assert seen[0] == {"location": LOCATION, "payload": PAYLOAD}
    assert "payload" not in seen[1]


def test_the_metadata_payload_is_written_to_the_step_row(
    test_config: Any,
    make_plugin: Any,  # noqa: F811
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    seed_user(db_url, ALICE)

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo: {msg.content}").send()

    test_config.code.on_message = on_message

    with create_test_client(plugins=[make_plugin()]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            send_and_read_reply(
                ws, "with payload", metadata={"location": LOCATION, "payload": PAYLOAD}
            )
            send_and_read_reply(ws, "plain")
        thread_id = handshake[0]["threadId"]
        detail = wait_for_thread(db_url, thread_id, lambda d: len(d.steps) == 4)

    stored = {step.output: (step.metadata or {}) for step in detail.steps}
    assert stored["with payload"]["payload"] == PAYLOAD
    assert stored["with payload"]["location"] == LOCATION
    assert "payload" not in stored["plain"]
