"""F5 while a question is open: what the reload gets back.

The scenario is the consumer's paid-action confirmation, reproduced in the
shape its own code builds -- a ``run`` step opened by ``@cl.on_message``
(``callbacks.py`` wraps every handler in one), and inside it a report
message carrying a file, a ``resume="delete"`` loader and a
``resume="delete"`` action question. The reload then arrives in each of the
two ways a browser can arrive: with the id the tab stored (the same session)
and with a fresh one (a second tab, or a page that lost the id).

What is pinned is the part the user pays for: the report and the file it
carries survive both arrivals. The flagged loader and the question are
allowed to disappear on the second one -- that is what the flag is for --
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
from chainlit.ws.registry import has_live_work
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
    frame: Dict[str, Any] = {"t": "hello", "sessionId": "s1", "pageLoad": True}
    frame.update(overrides)
    return json.dumps(frame)


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


# ------------------------------------------------- 1. the same id comes back


def test_a_reload_with_the_stored_id_keeps_the_turn_and_the_open_question(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
    pdf: Path,
) -> None:
    """F5 as the client is built to do it: the stored id, ``pageLoad``.

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
            handshake = open_session(ws, sessionId="s1")
            thread_id = handshake[0]["threadId"]
            run_to_the_question(ws)

        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(sessionId="s1", threadId=thread_id))
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


# ------------------------------------------------------- 2. a fresh id lands


def test_a_reload_with_a_fresh_id_keeps_everything_the_user_was_shown(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
    pdf: Path,
) -> None:
    """The abandonment path: a new session resumes the thread from storage.

    The question is genuinely gone -- the session holding it is swept and
    its coroutine cancelled -- and the flag on it and on the loader says so.
    Everything else is the user's: the report and the file it carries were
    produced and shown, and a resume that loses them loses work somebody
    paid for.
    """
    seed_user(db_url, ALICE)
    answered: List[Any] = []
    build_app(test_config, pdf, answered)
    plugin = make_plugin(storage=FakeStorage())

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws, sessionId="s1")
            thread_id = handshake[0]["threadId"]
            run_to_the_question(ws)
            # The rows have to be down before the reload reads them: the
            # writer batches, and the element's row waits on its upload.
            wait_for_thread(
                db_url,
                thread_id,
                lambda d: len(d.elements) == 1 and len(d.steps) >= 4,
            )

        # The socket is closed, but the session is marked disconnected only
        # when its handler unwinds -- and ``is_abandoned_ask_session`` reads
        # exactly that flag (registry.py:229). A hello that overtakes the
        # unwind finds a connected session, skips the sweep, and exercises a
        # branch this test is not about.
        wait_until(lambda: _disconnected(plugin, "s1"))

        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(sessionId="s2", threadId=thread_id))
            replay = read_until(ws, "thread.resume", limit=80)

    # The sweep really ran: the session holding the question is out of the
    # registry, its coroutine was cancelled without an answer, and no form
    # was put back. Inferring any of that from the filter below would pass
    # just as well against a thread nobody swept.
    assert plugin.runner.registry.get("s1") is None
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


def _disconnected(plugin: ChainlitPlugin, session_id: str) -> bool:
    entry = plugin.runner.registry.get(session_id)
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

    The question was answered and the turn ran to the end; the reload finds
    an idle session and replaces it, which tears the old one down like any
    other. A trace written on every teardown would put "start it again" under
    a conversation that finished.
    """
    seed_user(db_url, ALICE)
    answered: List[Any] = []
    build_app(test_config, pdf, answered)
    plugin = make_plugin(storage=FakeStorage())

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws, sessionId="s1")
            thread_id = handshake[0]["threadId"]
            opened = run_to_the_question(ws)
            action = frames_of(opened, "action.add")[0]["action"]
            ws.send_text(ask_reply(opened[-1]["spec"]["stepId"], action))
            done = read_until(ws, "step.upsert", limit=40)
            while done[-1]["step"].get("output") != "paid report":
                done.extend(read_until(ws, "step.upsert", limit=40))
            wait_for_thread(db_url, thread_id, lambda d: len(d.steps) >= 5)

        wait_until(lambda: _disconnected(plugin, "s1"))

        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(sessionId="s1", threadId=thread_id))
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

    The client now offers the stored thread on every reload. An application
    with no ``on_chat_resume``/``on_thread_ready`` bails out of ``_resume``
    -- and used to bail with the session still *bound* to the requested
    thread, so the blank chat the user saw was quietly appending its
    greeting and their next message to the old conversation's rows.
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
            handshake = open_session(ws, sessionId="s1")
            thread_id = handshake[0]["threadId"]
            ws.send_text(message("first"))
            read_until(ws, "step.upsert", limit=40)
        detail = wait_for_thread(db_url, thread_id, lambda d: len(d.steps) >= 2)
        before = len(detail.steps)

        wait_until(lambda: _disconnected(plugin, "s1"))

        with client.websocket_connect("/ws") as ws:
            frames = open_session(ws, sessionId="s2", threadId=thread_id)
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


def test_a_reload_onto_an_unwritten_thread_keeps_its_address(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """F5 on a greeting: no rows yet, and the address must not move.

    The writer holds a thread's rows until somebody speaks, so a chat that
    has only been greeted is nowhere in the database. The reload asks for it
    by name, the lookup misses -- and a miss that disowns would hand the tab
    a new id for the conversation it is already looking at, turning a reload
    into a different chat. The one thing that says this thread is the user's
    own is that this same session id was holding it a moment ago:
    ``arrival.superseded``.
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
            frames = open_session(ws, sessionId="s1")
            thread_id = frames[0]["threadId"]
            frames += read_until(ws, "step.upsert", limit=20)
        # Idle *and* disconnected before the reload: a greeting still
        # running is live work, the registry would keep the session, and
        # this test would pass on the reconnect branch instead.
        wait_until(lambda: _idle(plugin, "s1"))
        assert thread_detail(db_url, thread_id) is None, "the premise: no rows"

        with client.websocket_connect("/ws") as ws:
            replay = open_session(ws, sessionId="s1", threadId=thread_id)
            ready = replay[0]
            assert ready["t"] == "session.ready"
            assert not ready.get("restored"), "replaced, not kept"
            assert ready["threadId"] == thread_id, ready
            replay += read_until(ws, "step.upsert", limit=20)

            # The address is only half of it: the writer behind the kept
            # thread has to be the live one. The session that was replaced
            # was torn down with a writer of its own on this very thread,
            # and closing that one must not take the successor's with it.
            ws.send_text(message("hi again"))
            replay += read_until(ws, "step.upsert", limit=40)
        detail = wait_for_thread(
            db_url,
            thread_id,
            lambda d: "echo hi again" in [step.output for step in d.steps],
        )
        assert detail.id == thread_id

    # The greeting ran again on the same thread -- a reload has no
    # conversation to resume into, only its own id to keep.
    assert started == [thread_id, thread_id]
    # Nothing was reported. The refusal used to go out from ``on_ready``,
    # ahead of the greeting below it, so a frame list holding the greeting
    # would be holding it too.
    assert frames_of(replay, "error") == [], tags(replay)


def _idle(plugin: ChainlitPlugin, session_id: str) -> bool:
    entry = plugin.runner.registry.get(session_id)
    return entry is not None and not entry.connected and not has_live_work(entry)


def test_a_reload_that_asks_for_a_different_missing_thread_is_not_kept_on_it(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """A missing id the session was never on is not the user's to keep.

    Same shape as the reload above -- same session id, same page load, same
    lookup that misses -- and one thing different: the id asked for is not
    the one this session was holding. An address bar edited by hand, a
    history entry from a chat that was deleted, a link from somebody else.
    The keep is granted on that one piece of evidence, ``superseded``
    carrying *this* thread, and nothing weaker: a rule that read
    ``arrival.superseded`` as a boolean would hand the session every
    stranger's id it ever asked for, on the grounds that some reload
    happened.
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
            frames = open_session(ws, sessionId="s1")
            thread_id = frames[0]["threadId"]
            frames += read_until(ws, "step.upsert", limit=20)
        wait_until(lambda: _idle(plugin, "s1"))
        assert thread_detail(db_url, stranger) is None, "the premise: not a row"

        with client.websocket_connect("/ws") as ws:
            replay = open_session(ws, sessionId="s1", threadId=stranger)
            ready = replay[0]
            assert ready["t"] == "session.ready"
            assert not ready.get("restored"), "replaced, not kept"
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

    Ownership was pinned on the fresh-session path only: a socket that had
    never been seen before asking for somebody else's thread. A reload is
    the other path -- the session id is known, the arrival is REPLACED and
    carries a ``superseded`` entry -- and the entry is the evidence the keep
    is granted on. It says which thread was this session's, and nothing
    about whose the requested one is, so a resume decision that consults it
    without comparing thread ids grants a stranger's conversation on the
    strength of a reload having occurred.
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
            hers = open_session(ws, sessionId="s-alice")
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
            his = open_session(ws, sessionId="s-bob")
            bob_thread = his[0]["threadId"]
            his += read_until(ws, "step.upsert", limit=20)
        wait_until(lambda: _idle(plugin, "s-bob"))

        with client.websocket_connect("/ws") as ws:
            replay = open_session(ws, sessionId="s-bob", threadId=alice_thread)
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

    # Nothing of hers was resumed, said, or written into.
    assert resumed == []
    assert frames_of(replay, "error") == [], tags(replay)
    outputs = [f["step"].get("output") for f in frames_of(replay, "step.upsert")]
    assert "alice's secret" not in outputs, outputs
    after = thread_detail(db_url, alice_thread)
    assert after is not None
    assert len(after.steps) == len(before.steps), [s.output for s in after.steps]


def test_an_idle_reload_resumes_the_conversation_it_was_in(
    make_plugin: Callable[..., ChainlitPlugin],  # noqa: F811
    test_config: Any,
    auth: ChainlitAuth,  # noqa: F811
    db_url: str,  # noqa: F811
) -> None:
    """The behavior the thread restore deliberately changes, pinned.

    An idle session is REPLACED on a page load -- but the descriptor now
    carries the thread the tab was in, so the replacement resumes that
    conversation instead of opening a blank chat at ``/``.
    """
    seed_user(db_url, ALICE)

    async def on_message(msg: cl.Message) -> None:
        await cl.Message(content=f"echo {msg.content}").send()

    test_config.code.on_message = on_message
    test_config.code.on_chat_resume = _noop_resume
    plugin = make_plugin()

    with create_test_client(plugins=[plugin]) as client:
        login(client, auth, ALICE)
        with client.websocket_connect("/ws") as ws:
            handshake = open_session(ws, sessionId="s1")
            thread_id = handshake[0]["threadId"]
            ws.send_text(message("keep this"))
            read_until(ws, "step.upsert", limit=40)
        wait_for_thread(db_url, thread_id, lambda d: len(d.steps) >= 2)
        wait_until(lambda: _disconnected(plugin, "s1"))

        # The same tab reloads: stored id, stored thread, nothing running.
        with client.websocket_connect("/ws") as ws:
            ws.send_text(hello(sessionId="s1", threadId=thread_id))
            frames = read_until(ws, "thread.resume", limit=40)

    outputs = [step.get("output") for step in frames[-1]["thread"]["steps"]]
    assert "echo keep this" in outputs, outputs
