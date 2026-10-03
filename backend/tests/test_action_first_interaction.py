"""A click is an interaction: the first one opens the thread.

The writer holds every row of a fresh conversation until its first
interaction, and a session that closes with the gate still shut forgets
them (``SessionWriter.aclose``). Until this, only a message, an ask's
file reply and a profile handover opened the gate -- so a conversation
started from a button the application drew (a launch form that uploads
its file and calls an action instead of posting a user bubble) ran its
whole course in memory and never reached the history.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

import pytest
from litestar.testing import create_test_client

import chainlit as cl
from chainlit.plugin import ChainlitPlugin
from tests.test_runner import (  # noqa: F401 - fixture re-export
    frontend_dir,
    open_session,
    read_until,
    ready_of,
)

pytestmark = pytest.mark.usefixtures("test_config")


@pytest.fixture
def plugin(test_config: Any, frontend_dir: Path) -> ChainlitPlugin:  # noqa: F811
    return ChainlitPlugin(test_config, frontend_dir=frontend_dir, auth=None)


async def greet() -> None:
    await cl.Message(content="hello").send()


def click(client: Any, session_id: str, **action: Any) -> Any:
    return client.post(
        "/project/action", json={"sessionId": session_id, "action": action}
    )


def test_the_first_click_opens_the_thread_under_the_buttons_label(
    plugin: ChainlitPlugin, test_config: Any
) -> None:
    seen: List[str] = []

    async def launch(action: cl.Action) -> str:
        # The gate is open by the time the callback runs, so what it sends
        # is written, not held.
        seen.append(cl.context.session.first_interaction or "")
        return "ok"

    test_config.code.on_chat_start = greet
    test_config.code.action_callbacks["launch"] = launch

    with create_test_client(plugins=[plugin]) as client:
        with client.websocket_connect("/ws") as ws:
            ready = ready_of(open_session(ws))
            response = click(
                client, ready["sessionId"], name="launch", id="a1", label="Spoons x24"
            )
            frames = read_until(ws, "thread.first_interaction")

    assert response.status_code == 200
    assert frames[-1]["interaction"] == "Spoons x24"
    assert frames[-1]["threadId"] == ready["threadId"]
    assert seen == ["Spoons x24"]


def test_a_click_without_a_label_names_the_thread_by_the_action(
    plugin: ChainlitPlugin, test_config: Any
) -> None:
    async def launch(action: cl.Action) -> None:
        return None

    test_config.code.on_chat_start = greet
    test_config.code.action_callbacks["launch"] = launch

    with create_test_client(plugins=[plugin]) as client:
        with client.websocket_connect("/ws") as ws:
            ready = ready_of(open_session(ws))
            click(client, ready["sessionId"], name="launch", id="a1")
            frames = read_until(ws, "thread.first_interaction")

    assert frames[-1]["interaction"] == "launch"


def test_a_click_after_a_message_does_not_rename_the_thread(
    plugin: ChainlitPlugin, test_config: Any
) -> None:
    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content="echo").send()

    async def launch(action: cl.Action) -> None:
        await cl.Message(content="clicked").send()

    test_config.code.on_message = on_message
    test_config.code.action_callbacks["launch"] = launch

    with create_test_client(plugins=[plugin]) as client:
        with client.websocket_connect("/ws") as ws:
            ready = ready_of(open_session(ws))
            ws.send_text(
                json.dumps(
                    {
                        "t": "message.send",
                        "message": {
                            "id": "m1",
                            "type": "user_message",
                            "output": "first words",
                            "name": "User",
                            "createdAt": "2026-08-28T00:00:00.000000Z",
                        },
                    }
                )
            )
            before = read_until(ws, "step.upsert")
            while before[-1]["step"].get("output") != "echo":
                before.extend(read_until(ws, "step.upsert"))
            click(client, ready["sessionId"], name="launch", id="a1", label="Other")
            after = read_until(ws, "step.upsert")
            while after[-1]["step"].get("output") != "clicked":
                after.extend(read_until(ws, "step.upsert"))

    interactions = [
        f["interaction"] for f in before + after if f["t"] == "thread.first_interaction"
    ]
    assert interactions == ["first words"]
