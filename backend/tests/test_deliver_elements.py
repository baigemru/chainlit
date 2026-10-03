"""``cl.deliver_to_thread(elements=...)``: a result object for a closed tab.

The consumer's run finishes in a webhook, and what it has to leave in the
conversation is not only prose but the object the person acts on -- a
shortlist drawn by a custom element. With the tab closed there is no socket
to send an ``element.upsert`` on, so the element has to become the row an
element sent from ``on_message`` would have left: under the delivered step,
by its own id, where the resume finds it and where a later ``update()`` of
the same id lands instead of beside it.
"""

from __future__ import annotations

from typing import Any, List

import pytest
from litestar.testing import create_test_client

import chainlit as cl
from chainlit.plugin import ChainlitPlugin
from chainlit.security import ChainlitAuth
from tests.persistence.conftest import database_url  # noqa: F401 - fixture re-export
from tests.test_deliver_to_thread import DONE, ORPHAN, STEP, run_turn
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
    plugin,
    seed_user,
    thread_detail,
    wait_for_thread,
)

pytestmark = pytest.mark.usefixtures("test_config")

SHORTLIST = "33333333-3333-4333-8333-333333333333"


def shortlist(props: dict) -> cl.CustomElement:
    return cl.CustomElement(id=SHORTLIST, name="ResearchShortlist", props=props)


def test_a_stored_delivery_files_its_element_under_the_message(
    plugin: ChainlitPlugin,  # noqa: F811 - the imported fixture
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """Written as a row with the step's id, and drawn when the thread opens.

    The resume is the half that matters: a row the resume does not attach to
    its message is a result the person never sees.
    """
    seed_user(db_url, ALICE)
    delivered: List[cl.CustomElement] = []

    async def on_message(msg: cl.Message) -> None:
        element = shortlist({"skus": 7})
        delivered.append(element)
        await cl.deliver_to_thread(
            ORPHAN,
            "the run finished",
            id=STEP,
            user_identifier=ALICE,
            elements=[element],
        )
        await cl.Message(content=DONE).send()

    async def on_chat_resume(thread: Any) -> None:
        pass

    test_config.code.on_message = on_message
    # Without the hook a thread is not resumable at all.
    test_config.code.on_chat_resume = on_chat_resume

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
            run_turn(ws, "go")

        detail = thread_detail(db_url, ORPHAN)
        assert detail is not None
        [row] = detail.elements
        assert (row.id, row.for_id, row.thread_id) == (SHORTLIST, STEP, ORPHAN)
        assert (row.type, row.name, row.props) == (
            "custom",
            "ResearchShortlist",
            {"skus": 7},
        )
        # The caller's object knows where it landed, as an Action does.
        assert delivered[0].for_id == STEP

        with client.websocket_connect("/ws") as ws:
            replay = open_session(ws, threadId=ORPHAN)

    # A cold resume carries the thread's elements inside the snapshot, and
    # the client files each one under the step its ``forId`` names.
    [snapshot] = [f["thread"] for f in replay if f["t"] == "thread.resume"]
    assert [s["id"] for s in snapshot["steps"]] == [STEP]
    assert [
        (e["id"], e["forId"], e["type"], e["props"]) for e in snapshot["elements"]
    ] == [(SHORTLIST, STEP, "custom", {"skus": 7})]


def test_a_second_delivery_of_the_element_replaces_its_row(
    plugin: ChainlitPlugin,  # noqa: F811 - the imported fixture
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """ "Rebuild the shortlist" from a job is the same id with new props."""
    seed_user(db_url, ALICE)

    async def on_message(msg: cl.Message) -> None:
        await cl.deliver_to_thread(
            ORPHAN,
            "the run finished",
            id=STEP,
            user_identifier=ALICE,
            elements=[shortlist({"version": msg.content})],
        )
        await cl.Message(content=DONE).send()

    test_config.code.on_message = on_message

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
            run_turn(ws, "one")
            run_turn(ws, "two")

    detail = thread_detail(db_url, ORPHAN)
    assert detail is not None
    assert [(e.id, e.props) for e in detail.elements] == [
        (SHORTLIST, {"version": "two"})
    ]


def test_a_live_delivery_sends_and_stores_its_element(
    plugin: ChainlitPlugin,  # noqa: F811 - the imported fixture
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    seed_user(db_url, ALICE)

    async def on_message(msg: cl.Message) -> None:
        here = cl.context.session.thread_id
        assert here is not None
        await cl.deliver_to_thread(
            here, "the run finished", elements=[shortlist({"skus": 7})]
        )
        await cl.Message(content=DONE).send()

    test_config.code.on_message = on_message

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            thread_id = open_session(ws)[0]["threadId"]
            frames = run_turn(ws, "go")
        detail = wait_for_thread(
            db_url, thread_id, lambda d: any(e.id == SHORTLIST for e in d.elements)
        )

    [step_id] = [
        f["step"]["id"]
        for f in frames
        if f["t"] == "step.upsert" and f["step"].get("output") == "the run finished"
    ]
    assert [
        (f["element"]["id"], f["element"]["forId"])
        for f in frames
        if f["t"] == "element.upsert"
    ] == [(SHORTLIST, step_id)]
    [row] = [e for e in detail.elements if e.id == SHORTLIST]
    assert row.for_id == step_id


def test_an_element_with_a_blob_is_refused_before_anything_is_written(
    plugin: ChainlitPlugin,  # noqa: F811 - the imported fixture
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """No session, no spool: refused, not half-delivered without its picture."""
    seed_user(db_url, ALICE)
    raised: List[str] = []

    async def on_message(msg: cl.Message) -> None:
        try:
            await cl.deliver_to_thread(
                ORPHAN,
                "the run finished",
                id=STEP,
                user_identifier=ALICE,
                elements=[cl.Image(name="photo", content=b"\x89PNG")],
            )
        except ValueError as error:
            raised.append(str(error))
        await cl.Message(content=DONE).send()

    test_config.code.on_message = on_message

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
            run_turn(ws, "go")

    assert raised
    assert "photo" in raised[0]
    assert thread_detail(db_url, ORPHAN) is None


def test_an_update_after_the_resume_overwrites_the_delivered_row(
    plugin: ChainlitPlugin,  # noqa: F811 - the imported fixture
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """The consumer's next move: the person opens the thread, the app updates.

    ``for_id`` has to be passed again. ``update()`` re-sends the element as
    the object says it is, and an object built afresh without it would write
    ``forId`` NULL -- the next resume would read the shortlist as a panel row
    and take it away from its message.
    """
    seed_user(db_url, ALICE)

    async def on_message(msg: cl.Message) -> None:
        await cl.deliver_to_thread(
            ORPHAN,
            "the run finished",
            id=STEP,
            user_identifier=ALICE,
            elements=[shortlist({"skus": 7})],
        )
        await cl.Message(content=DONE).send()

    async def on_chat_resume(thread: Any) -> None:
        await cl.CustomElement(
            id=SHORTLIST,
            name="ResearchShortlist",
            props={"skus": 9},
            for_id=STEP,
        ).update()

    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = on_chat_resume

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
            run_turn(ws, "go")
        with client.websocket_connect("/ws") as ws:
            open_session(ws, threadId=ORPHAN)
            [upsert] = read_until(ws, "element.upsert")[-1:]
        detail = wait_for_thread(
            db_url,
            ORPHAN,
            lambda d: [e.props for e in d.elements] == [{"skus": 9}],
        )

    assert (upsert["element"]["id"], upsert["element"]["forId"]) == (SHORTLIST, STEP)
    [row] = detail.elements
    assert (row.id, row.for_id) == (SHORTLIST, STEP)


def test_an_element_given_a_url_is_stored_with_it(
    plugin: ChainlitPlugin,  # noqa: F811 - the imported fixture
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """The other kind the stored road accepts: nothing to upload, the url is it."""
    seed_user(db_url, ALICE)
    picture = "44444444-4444-4444-8444-444444444444"

    async def on_message(msg: cl.Message) -> None:
        await cl.deliver_to_thread(
            ORPHAN,
            "the run finished",
            id=STEP,
            user_identifier=ALICE,
            elements=[cl.Image(id=picture, name="cover", url="https://x.test/c.png")],
        )
        await cl.Message(content=DONE).send()

    test_config.code.on_message = on_message

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            open_session(ws)
            run_turn(ws, "go")

    detail = thread_detail(db_url, ORPHAN)
    assert detail is not None
    [row] = detail.elements
    assert (row.id, row.type, row.url, row.for_id) == (
        picture,
        "image",
        "https://x.test/c.png",
        STEP,
    )
