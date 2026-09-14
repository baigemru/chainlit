"""F5 while a question is open, and the two ways a conversation ends.

The scenario is the consumer's paid-action confirmation, reproduced in the
shape its own code builds -- a ``run`` step opened by ``@cl.on_message``
(``callbacks.py`` wraps every handler in one), and inside it a report
message carrying a file, a ``resume="delete"`` loader and a
``resume="delete"`` action question.

There is one reload now and it always means the same thing: the tab names
the thread it is in and gets the session that is in it, question and all.
What used to be the second kind of reload -- a fresh session resuming the
thread from storage -- is now the *cold* path, and reaching it means the
session is gone: the reaper came after the grace period, or the user
pressed "New chat", which gives the conversation up outright.

What is pinned is the part the user pays for: the report and the file it
carries survive every arrival. The flagged loader and the question are
allowed to disappear on the cold path -- that is what the flag is for --
but nothing unflagged may go with them.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest
from litestar.testing import create_test_client

import chainlit as cl
from chainlit.plugin import ChainlitPlugin
from chainlit.runner import DEFAULT_INTERRUPTED_ASK_MESSAGE as TRACE
from chainlit.security import ChainlitAuth
from chainlit.ws.session import Session
from tests.persistence.conftest import database_url  # noqa: F401 - fixture re-export
from tests.test_runner import (  # noqa: F401 - fixture re-export
    frontend_dir,
    open_session,
    read_until,
)
from tests.test_runner_persistence import (  # noqa: F401 - fixture re-export
    ALICE,
    BOB,
    FakeStorage,
    auth,
    db_url,
    first,
    login,
    make_plugin,
    message,
    reap_soon,
    reaped,
    seed_user,
    tags,
    thread_detail,
    wait_for_thread,
    wait_until,
)

pytestmark = pytest.mark.usefixtures("test_config")

PDF = b"%PDF-1.4 a free report"

ASK_TIMEOUT = 600

ASK_AUTHOR = "Concierge"


def hello(**overrides: Any) -> str:
    frame: Dict[str, Any] = {"t": "hello", "pageLoad": True}
    frame.update(overrides)
    return json.dumps(frame)


CLEAR = json.dumps({"t": "session.clear"})
"""What "New chat" sends. The client detaches first, so the abort the
server answers with closes a socket nobody is listening on."""


def ask_reply(step_id: str, action: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "t": "ask.reply",
            "stepId": step_id,
            "value": {"kind": "action", "action": action},
        }
    )


def frames_of(frames: List[dict], tag: str) -> List[dict]:
    return [frame for frame in frames if frame["t"] == tag]


def drain(ws: Any, *, limit: int = 80, timeout: float = 0.4) -> List[dict]:
    """Everything this socket has to say, until it goes quiet.

    ``read_until`` stops at a tag, which is no use when the assertion is
    about what is *absent*: a kept session may still hold a level frame the
    previous socket never took, and stopping on that one would read none of
    the replay behind it.
    """
    frames: List[dict] = []
    for _ in range(limit):
        try:
            frames.append(json.loads(ws.receive_text(timeout=timeout)))
        except Exception:
            break
    return frames


async def _noop_resume(thread: Dict[str, Any]) -> None:
    return None


def build_app(test_config: Any, pdf: Path, answered: List[Any]) -> Dict[str, Any]:
    """The consumer's turn: a report with a file, a loader, then the question.

    ``cl.Step(type="run")`` is not decoration. ``@cl.on_message`` opens one
    around every handler (``callbacks.py:134``), so every message a turn
    sends carries its id as ``parentId`` -- and a filter that walked the tree
    down from a flagged step would reach all of them through it. A repro
    without the run step cannot see that.
    """
    ids: Dict[str, Any] = {}

    async def on_message(msg: cl.Message) -> None:
        async with cl.Step(name="on_message", type="run", parent_id=msg.id) as run:
            run.input = msg.content
            ids["run"] = run.id

            report = cl.Message(
                content="**Shortlist ready.**",
                elements=[cl.Pdf(path=str(pdf), name="report", display="inline")],
            )
            await report.send()
            ids["report"] = report.id

            loader = cl.Message(content="working", wait=True, resume="delete")
            await loader.send()
            ids["loader"] = loader.id

            answer = await cl.AskActionMessage(
                content="Buy the full report?",
                author=ASK_AUTHOR,
                actions=[
                    cl.Action(name="yes", label="Yes", payload={"value": "yes"}),
                    cl.Action(name="no", label="No", payload={"value": "no"}),
                ],
                timeout=ASK_TIMEOUT,
                raise_on_timeout=False,
                resume="delete",
            ).send()
            answered.append(answer)
            await cl.Message(content="paid report").send()

    test_config.code.on_message = on_message
    # A resume only replays a stored thread when the application has
    # something to resume *into*: ``_resume`` returns early without a hook
    # (runner.py:270) and the client gets no ``thread.resume`` at all.
    test_config.code.on_chat_resume = _noop_resume
    return ids


def run_to_the_question(ws: Any) -> List[dict]:
    ws.send_text(message("find me something"))
    return read_until(ws, "ask.start", limit=80)


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "report.pdf"
    path.write_bytes(PDF)
    return path


# ---------------------------------------------- 1. the same thread comes back


def test_a_reload_of_the_same_thread_keeps_the_turn_and_the_open_question(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
    pdf: Path,
) -> None:
    """F5 as the client is built to do it: the thread in the URL, ``pageLoad``.

    Everything the turn put on screen comes back from the session's own
    transcript -- the flagged loader and the question included, which are
    not leftovers at all while the coroutine that asked is still waiting.
    """
    seed_user(db_url, ALICE)
    answered: List[Any] = []
    build_app(test_config, pdf, answered)
    plugin = make_plugin(storage=FakeStorage())

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            thread_id = handshake[0]["threadId"]
            run_to_the_question(ws)

        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(threadId=thread_id))
            replay = read_until(ws, "ask.start", limit=80)

            ready = replay[0]
            assert ready["t"] == "session.ready"
            assert ready["restored"] is True, tags(replay)

            outputs = [
                f["step"].get("output") for f in frames_of(replay, "step.upsert")
            ]
            assert "**Shortlist ready.**" in outputs, outputs
            assert "working" in outputs, outputs

            elements = [f["element"] for f in frames_of(replay, "element.upsert")]
            assert [e["name"] for e in elements] == ["report"], elements

            actions = [f["action"]["name"] for f in frames_of(replay, "action.add")]
            assert actions == ["yes", "no"], tags(replay)

            ask = replay[-1]
            assert ask["t"] == "ask.start"
            # What is left of the deadline, never a fresh one.
            assert 0 < ask["spec"]["timeout"] <= ASK_TIMEOUT

            # And the coroutine that asked is still there to be answered.
            action = frames_of(replay, "action.add")[0]["action"]
            ws.send_text(ask_reply(ask["spec"]["stepId"], action))
            after = read_until(ws, "step.upsert", limit=40)
            while after[-1]["step"].get("output") != "paid report":
                after.extend(read_until(ws, "step.upsert", limit=40))

    assert len(answered) == 1
    assert answered[0]["name"] == "yes"


# -------------------------------------------- 2. the cold path, after the reaper


def test_reopening_the_thread_after_the_reaper_keeps_what_the_user_was_shown(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
    pdf: Path,
) -> None:
    """The abandonment path: the user walked away, and came back later.

    Nothing about the reload does this any more -- the reaper does, after
    the grace period, and it is the only thing that can. The question is
    then genuinely gone, its coroutine cancelled, and the flag on it and on
    the loader says so. Everything else is the user's: the report and the
    file it carries were produced and shown, and a resume that loses them
    loses work somebody paid for.
    """
    seed_user(db_url, ALICE)
    answered: List[Any] = []
    build_app(test_config, pdf, answered)
    plugin = make_plugin(storage=FakeStorage())

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            thread_id = handshake[0]["threadId"]
            run_to_the_question(ws)
            # The rows have to be down before the resume reads them: the
            # writer batches, and the element's row waits on its upload.
            wait_for_thread(
                db_url,
                thread_id,
                lambda d: len(d.elements) == 1 and len(d.steps) >= 4,
            )
            reap_soon(plugin)

        # The grace period, compressed. Until it is over the thread is still
        # this user's live conversation and reopening it would simply hand
        # the question back -- which is the sibling test above.
        reaped(plugin, thread_id)

        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(threadId=thread_id))
            replay = read_until(ws, "thread.resume", limit=80)

    # The reaper really ran: the coroutine was cancelled without an answer
    # and no form was put back. Inferring either from the filter below would
    # pass just as well against a session nobody reaped.
    assert answered == []
    assert frames_of(replay, "ask.start") == [], tags(replay)

    snapshot = replay[-1]["thread"]
    outputs = [step.get("output") for step in snapshot["steps"]]
    element_names = [element["name"] for element in snapshot.get("elements", [])]

    # The flag did its job.
    assert "working" not in outputs, outputs
    assert "Buy the full report?" not in outputs, outputs
    # And took nothing else with it.
    assert "**Shortlist ready.**" in outputs, outputs
    assert element_names == ["report"], snapshot.get("elements")

    # The turn does not just stop. The question is gone and so is the
    # coroutine behind it; the feed says so rather than ending mid-sentence.
    trace = [step for step in snapshot["steps"] if step.get("output") == TRACE]
    assert len(trace) == 1, outputs
    assert trace[0]["name"] == ASK_AUTHOR
    assert trace[0].get("parentId") is None
    assert (trace[0].get("metadata") or {}).get("resume_policy") is None

    # The rows were never deleted, only hidden: the filter is a read-time
    # rule, and a bug that turned it into a delete would be invisible above.
    detail = thread_detail(db_url, thread_id)
    assert detail is not None
    assert "working" in [step.output for step in detail.steps]


def _disconnected(plugin: ChainlitPlugin, thread_id: str) -> bool:
    """The socket is gone and the session is not.

    Keyed by thread, because that is the registry's key: the handle the
    server minted for the session is not something a test can know before
    ``session.ready`` names it.
    """
    entry = plugin.runner.registry.entry_of_thread(thread_id)
    return entry is not None and not entry.connected


# ------------------------------------- 3. a session torn down owing nothing


def test_a_reload_onto_a_finished_turn_leaves_no_interruption_trace(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
    pdf: Path,
) -> None:
    """The guard on the trace: nothing was interrupted, so nothing is said.

    The question was answered and the turn ran to the end; the reaper then
    tears the session down like any other. A trace written on every teardown
    would put "start it again" under a conversation that finished.
    """
    seed_user(db_url, ALICE)
    answered: List[Any] = []
    build_app(test_config, pdf, answered)
    plugin = make_plugin(storage=FakeStorage())

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            thread_id = handshake[0]["threadId"]
            opened = run_to_the_question(ws)
            action = frames_of(opened, "action.add")[0]["action"]
            ws.send_text(ask_reply(opened[-1]["spec"]["stepId"], action))
            done = read_until(ws, "step.upsert", limit=40)
            while done[-1]["step"].get("output") != "paid report":
                done.extend(read_until(ws, "step.upsert", limit=40))
            wait_for_thread(db_url, thread_id, lambda d: len(d.steps) >= 5)
            reap_soon(plugin)

        reaped(plugin, thread_id)

        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(threadId=thread_id))
            replay = read_until(ws, "thread.resume", limit=80)

    assert len(answered) == 1
    assert answered[0]["name"] == "yes"
    outputs = [step.get("output") for step in replay[-1]["thread"]["steps"]]
    assert TRACE not in outputs, outputs
    assert "paid report" in outputs, outputs


# ------------------------- 4. the thread restore in apps that cannot resume


def test_an_app_without_resume_hooks_gets_a_fresh_thread_not_the_old_one(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """A requested thread the app cannot resume must be disowned, not kept.

    The client offers the thread in its address bar on every connect. An
    application with no ``on_chat_resume``/``on_thread_ready`` bails out of
    ``_resume`` -- and used to bail with the session still *bound* to the
    requested thread, so the blank chat the user saw was quietly appending
    its greeting and their next message to the old conversation's rows.
    """
    seed_user(db_url, ALICE)

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo {msg.content}").send()

    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = None
    test_config.code.on_thread_ready = None
    plugin = make_plugin()

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            thread_id = handshake[0]["threadId"]
            ws.send_text(message("first"))
            read_until(ws, "step.upsert", limit=40)
            reap_soon(plugin)
        detail = wait_for_thread(db_url, thread_id, lambda d: len(d.steps) >= 2)
        before = len(detail.steps)

        reaped(plugin, thread_id)

        with client.websocket_connect("/ws") as ws:
            frames = open_session(ws, threadId=thread_id)
            ready = frames[0]
            assert ready["t"] == "session.ready"
            # Not the thread it asked for, and no snapshot pretending it is.
            assert ready["threadId"] != thread_id, ready
            assert frames_of(frames, "thread.resume") == [], tags(frames)

            ws.send_text(message("second, in what must be a new chat"))
            read_until(ws, "step.upsert", limit=40)
            new_thread = ready["threadId"]
            wait_for_thread(db_url, new_thread, lambda d: len(d.steps) >= 2)

    # The old conversation gained nothing: the new chat is where the new
    # turn went, not into the rows of the thread the reload happened to name.
    after = thread_detail(db_url, thread_id)
    assert after is not None
    assert len(after.steps) == before, [step.output for step in after.steps]


def test_a_reload_on_a_greeting_keeps_the_session_and_does_not_greet_again(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """F5 on a greeting: the hardest case for the old rule, trivial for this one.

    The writer holds a thread's rows until somebody speaks, so a chat that
    has only been greeted is nowhere in the database. Under the old rule the
    reload started a new session, the lookup for the thread missed, and only
    a record of what the replaced session had been holding kept the tab from
    being handed a new address for the chat it was looking at.

    There is no lookup now and nothing to miss: the thread is held, so the
    session comes back whole. Which is also the change the release is
    about -- ``on_chat_start`` does **not** run a second time, and whatever
    the application put in ``user_session`` is still there.
    """
    seed_user(db_url, ALICE)
    started: List[Optional[str]] = []

    async def on_chat_start() -> None:
        started.append(cl.context.session.thread_id)
        await cl.Message(content="hello there").send()

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo {msg.content}").send()

    test_config.code.on_chat_start = on_chat_start
    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = _noop_resume
    plugin = make_plugin()

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            frames = open_session(ws)
            thread_id = frames[0]["threadId"]
            frames += read_until(ws, "step.upsert", limit=20)
        wait_until(lambda: _disconnected(plugin, thread_id))
        assert thread_detail(db_url, thread_id) is None, "the premise: no rows"

        with client.websocket_connect("/ws") as ws:
            replay = open_session(ws, threadId=thread_id)
            ready = replay[0]
            assert ready["t"] == "session.ready"
            assert ready["restored"] is True, "the session was thrown away"
            assert ready["threadId"] == thread_id, ready

            # The address is only half of it: the writer behind the kept
            # thread has to be the live one, and the greeting has to come
            # back from the session's own transcript rather than be said
            # again.
            ws.send_text(message("hi again"))
            replay += read_until(ws, "step.upsert", limit=40)
        detail = wait_for_thread(
            db_url,
            thread_id,
            lambda d: "echo hi again" in [step.output for step in d.steps],
        )
        assert detail.id == thread_id

    # Once. The conversation never ended, so it was never begun again --
    # which is what reload now means, and what the release notes say.
    assert started == [thread_id]
    # The greeting is still on screen because the replay re-sent it, not
    # because the hook ran.
    outputs = [f["step"].get("output") for f in frames_of(replay, "step.upsert")]
    assert outputs.count("hello there") == 1, outputs
    assert frames_of(replay, "error") == [], tags(replay)


def _gone(plugin: ChainlitPlugin, thread_id: str) -> bool:
    """Nobody is in this conversation any more: the cold path is reachable."""
    return plugin.runner.registry.entry_of_thread(thread_id) is None


def test_a_reload_that_asks_for_a_different_missing_thread_is_not_kept_on_it(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """A missing id the session was never on is not the user's to keep.

    An address bar edited by hand, a history entry from a chat that was
    deleted, a link from somebody else. Nobody is in that conversation and
    there are no rows under it, so the arrival begins a session -- and the
    lookup that misses disowns it onto a thread of its own rather than
    leaving it bound to a name it may not have. The tab the user left
    behind is untouched: it is in a different conversation, and the two do
    not meet.
    """
    seed_user(db_url, ALICE)
    started: List[Optional[str]] = []

    async def on_chat_start() -> None:
        started.append(cl.context.session.thread_id)
        await cl.Message(content="hello there").send()

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo {msg.content}").send()

    test_config.code.on_chat_start = on_chat_start
    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = _noop_resume
    plugin = make_plugin()

    stranger = str(uuid.uuid4())

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            frames = open_session(ws)
            thread_id = frames[0]["threadId"]
            frames += read_until(ws, "step.upsert", limit=20)
        wait_until(lambda: _disconnected(plugin, thread_id))
        assert thread_detail(db_url, stranger) is None, "the premise: not a row"

        with client.websocket_connect("/ws") as ws:
            replay = open_session(ws, threadId=stranger)
            ready = replay[0]
            assert ready["t"] == "session.ready"
            assert not ready.get("restored"), "created, not kept"
            # Neither the id it asked for nor the one it left behind: the
            # session is disowned onto a thread of its own, and the only
            # way the client learns that is this frame.
            assert ready["threadId"] not in {stranger, thread_id}, ready
            replay += read_until(ws, "step.upsert", limit=20)

            ws.send_text(message("hi again"))
            replay += read_until(ws, "step.upsert", limit=40)
        detail = wait_for_thread(
            db_url,
            ready["threadId"],
            lambda d: "echo hi again" in [step.output for step in d.steps],
        )
        assert detail.id == ready["threadId"]
        assert (
            first(replay, "thread.first_interaction")["threadId"] == ready["threadId"]
        )

    assert started == [thread_id, ready["threadId"]]
    # Still nothing reported: a miss is answered by the thread in
    # ``session.ready``, whichever branch produced it.
    assert frames_of(replay, "error") == [], tags(replay)
    # And neither of the two ids the tab knew about gained a row.
    assert thread_detail(db_url, stranger) is None
    assert thread_detail(db_url, thread_id) is None


def test_a_reload_that_asks_for_another_users_thread_is_not_kept_on_it(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """Bob reloads his own session onto Alice's id, and is moved off it.

    Alice's conversation is still live when Bob asks for it, which is the
    case the registry answers on its own: a thread held by somebody else is
    answered exactly as a thread that never existed -- with a conversation
    of Bob's own and not a word about hers. Nothing of Alice's is read,
    written or disturbed, and her session goes on holding her thread.
    """
    seed_user(db_url, ALICE)
    seed_user(db_url, BOB)
    resumed: List[str] = []

    async def on_chat_start() -> None:
        await cl.Message(content="hello there").send()

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo {msg.content}").send()

    async def on_chat_resume(thread: Dict[str, Any]) -> None:
        resumed.append(str(thread.get("id")))

    test_config.code.on_chat_start = on_chat_start
    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = on_chat_resume
    plugin = make_plugin()

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            hers = open_session(ws)
            alice_thread = hers[0]["threadId"]
            ws.send_text(message("alice's secret"))
            hers += read_until(ws, "step.upsert", limit=40)
        # Written, and so findable: this is the branch where the row exists
        # and the identifier on it is not the one asking.
        before = wait_for_thread(
            db_url,
            alice_thread,
            lambda d: "echo alice's secret" in [step.output for step in d.steps],
        )

        login(client, auth, BOB)
        with client.websocket_connect("/ws") as ws:
            his = open_session(ws)
            bob_thread = his[0]["threadId"]
            his += read_until(ws, "step.upsert", limit=20)
        wait_until(lambda: _disconnected(plugin, bob_thread))

        with client.websocket_connect("/ws") as ws:
            replay = open_session(ws, threadId=alice_thread)
            ready = replay[0]
            assert ready["t"] == "session.ready"
            assert ready["threadId"] not in {alice_thread, bob_thread}, ready
            replay += read_until(ws, "step.upsert", limit=20)

            ws.send_text(message("hi again"))
            replay += read_until(ws, "step.upsert", limit=40)
        mine = wait_for_thread(
            db_url,
            ready["threadId"],
            lambda d: "echo hi again" in [step.output for step in d.steps],
        )
        assert mine.user_identifier == BOB

    # Nothing of hers was resumed, said, or written into -- and her own
    # session is still sitting in her thread, undisturbed by the guess.
    assert resumed == []
    assert plugin.runner.registry.entry_of_thread(alice_thread) is not None
    assert frames_of(replay, "error") == [], tags(replay)
    outputs = [f["step"].get("output") for f in frames_of(replay, "step.upsert")]
    assert "alice's secret" not in outputs, outputs
    after = thread_detail(db_url, alice_thread)
    assert after is not None
    assert len(after.steps) == len(before.steps), [s.output for s in after.steps]


def test_an_idle_reload_gets_its_conversation_back_without_a_snapshot(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """The behaviour this release changes, pinned from the other side.

    An idle session used to be thrown away on a page load and the thread
    read back from the database, so the tab got a ``thread.resume``
    snapshot and the hooks ran again. Now the session is simply handed
    over: the feed comes back from its own transcript, step by step, and
    the database is not touched at all -- which is the point. What is in
    memory is what the user was actually shown, including anything the app
    sent that never became a row.
    """
    seed_user(db_url, ALICE)
    resumed: List[str] = []

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo {msg.content}").send()

    async def on_chat_resume(thread: Dict[str, Any]) -> None:
        resumed.append(str(thread.get("id")))

    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = on_chat_resume
    plugin = make_plugin()

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            thread_id = handshake[0]["threadId"]
            ws.send_text(message("keep this"))
            read_until(ws, "step.upsert", limit=40)
        wait_for_thread(db_url, thread_id, lambda d: len(d.steps) >= 2)
        wait_until(lambda: _disconnected(plugin, thread_id))

        # The same tab reloads: the thread in its address bar, nothing
        # running, and a page load.
        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(threadId=thread_id))
            frames = drain(ws)

    assert frames[0]["t"] == "session.ready"
    assert frames[0]["restored"] is True, tags(frames)
    assert frames[0]["threadId"] == thread_id
    outputs = [f["step"].get("output") for f in frames_of(frames, "step.upsert")]
    assert "echo keep this" in outputs, outputs
    # Not a resume: nothing was read back, and the hook that answers for a
    # resume was never called.
    assert frames_of(frames, "thread.resume") == [], tags(frames)
    assert resumed == []


# ------------------------------------- 5. "New chat" gives the thread up


def test_new_chat_frees_the_thread_so_reopening_it_resumes_from_storage(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """The consequence of keeping sessions across reloads, and its answer.

    With the session surviving everything, "New chat" is the only way a
    user ends one -- and it has to really end it. A ``session.clear`` that
    merely cancelled the work would leave an empty live session sitting on
    the thread, and opening that thread from the history a second later
    would be handed the blank screen instead of the conversation.
    """
    seed_user(db_url, ALICE)
    resumed: List[str] = []

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo {msg.content}").send()

    async def on_chat_resume(thread: Dict[str, Any]) -> None:
        resumed.append(str(thread.get("id")))

    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = on_chat_resume
    plugin = make_plugin()

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            thread_id = handshake[0]["threadId"]
            ws.send_text(message("keep this"))
            read_until(ws, "step.upsert", limit=40)
            wait_for_thread(db_url, thread_id, lambda d: len(d.steps) >= 2)
            entry = plugin.runner.registry.entry_of_thread(thread_id)
            assert entry is not None
            released = entry.session
            assert isinstance(released, Session)
            # "New chat". The real client has detached by now, so nothing
            # is read off this socket afterwards.
            ws.send_text(CLEAR)

        # Within the same turn, and long before any reaper could run: the
        # thread is free the moment the session gives it up.
        wait_until(lambda: _gone(plugin, thread_id) and not released.connected)
        # And the socket unwinding behind it schedules nothing. The session
        # is already gone; a reaper here would sit for five minutes and then
        # tear down a session that stopped existing before it started.
        assert released.reaper is None, "a released session was left a reaper"

        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(threadId=thread_id))
            frames = drain(ws)

    assert frames[0]["t"] == "session.ready"
    assert not frames[0].get("restored"), tags(frames)
    assert frames[0]["threadId"] == thread_id
    snapshot = frames_of(frames, "thread.resume")[-1]["thread"]
    assert "echo keep this" in [step.get("output") for step in snapshot["steps"]]
    assert resumed == [thread_id]


def test_new_chat_on_an_open_question_leaves_the_interruption_trace(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
    pdf: Path,
) -> None:
    """Pressing "New chat" on a form is an interruption like any other.

    ``session.clear`` used to call ``cancel_work``, which writes nothing:
    the turn simply stopped mid-sentence and the resume showed a
    conversation that trails off. It is a teardown now, so it leaves the
    same line in the thread that the reaper does -- in the voice the
    question was asked in.
    """
    seed_user(db_url, ALICE)
    answered: List[Any] = []
    build_app(test_config, pdf, answered)
    plugin = make_plugin(storage=FakeStorage())

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws)
            thread_id = handshake[0]["threadId"]
            run_to_the_question(ws)
            ws.send_text(CLEAR)

        wait_until(lambda: _gone(plugin, thread_id))
        detail = wait_for_thread(
            db_url,
            thread_id,
            lambda d: TRACE in [step.output for step in d.steps],
        )

    # Nobody answered, and the feed says so rather than ending mid-turn.
    assert answered == []
    trace = [step for step in detail.steps if step.output == TRACE]
    assert len(trace) == 1
    assert trace[0].name == ASK_AUTHOR
