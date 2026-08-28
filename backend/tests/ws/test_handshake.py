"""What arriving means, and what the client is shown afterwards.

The scenario table in ``tests/socketspec`` is the normative statement of
this behaviour and drives the old transport today; a second driver against
this stack lands with the application bridge, because most rows need the
hooks an application registers. These tests cover what does not: the claim
decision, the once-only side effects, and the replay order.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, List, Sequence

from chainlit.protocol.codec import encode_server
from chainlit.protocol.payloads import AskActionSpec, Step as StepPayload, TextElement
from chainlit.protocol.server import AskStart, StepUpsert
from chainlit.ws.handshake import (
    arrive,
    restore,
    sweep_superseded,
)
from chainlit.ws.registry import ClaimOutcome, SessionRegistry
from chainlit.ws.session import PendingAsk, Session, TranscriptEntry


def make(session_id: str) -> Session:
    return Session(id=session_id)


def tags(session: Session) -> List[str]:
    """The tags this session queued, in order.

    Read off the encoded frame rather than the struct, because the tag is
    a fact about the wire and reading it any other way would let the two
    disagree.
    """
    return [
        json.loads(encode_server(item))["t"] for item in session.outbound.pending_frames
    ]


def queued(session: Session, kind: type) -> List[Any]:
    """Every queued frame of one branch of the union."""
    return [item for item in session.outbound.pending_frames if isinstance(item, kind)]


def ask(
    session: Session, *, remaining: float = 60.0, step_id: str = "q1"
) -> PendingAsk:
    pending = PendingAsk(
        step_id=step_id,
        step=StepPayload(id=step_id, output="pick one"),
        spec=AskActionSpec(step_id=step_id, timeout=90),
        future=asyncio.get_running_loop().create_future(),
        deadline=time.monotonic() + remaining,
        restore_actions=[],
    )
    session.pending_ask = pending
    return pending


# ---------------------------------------------------------------- arriving


async def test_an_unknown_id_creates_a_conversation() -> None:
    registry = SessionRegistry()
    arrival = await arrive(
        registry=registry,
        session_id="s1",
        user_identifier="ada",
        page_load=True,
        thread_id=None,
        make_session=make,
    )
    assert arrival.outcome is ClaimOutcome.CREATED
    assert arrival.session is not None
    assert registry.get("s1") is not None


async def test_reloading_an_idle_conversation_starts_a_new_one() -> None:
    """What reloading has always meant.

    Handing the old conversation back would make the one gesture everyone
    uses to start over stop working.
    """
    registry = SessionRegistry()
    first = make("s1")
    registry.register(first, user_identifier="ada", thread_id="t1")

    arrival = await arrive(
        registry=registry,
        session_id="s1",
        user_identifier="ada",
        page_load=True,
        thread_id="t1",
        make_session=make,
    )
    assert arrival.outcome is ClaimOutcome.REPLACED
    assert arrival.session is not first
    assert [entry.session for entry in arrival.superseded] == [first]


async def test_reloading_while_a_question_waits_keeps_the_conversation() -> None:
    """Starting over abandons a question the server goes on waiting for."""
    registry = SessionRegistry()
    held = make("s1")
    ask(held)
    registry.register(held, user_identifier="ada", thread_id="t1")

    arrival = await arrive(
        registry=registry,
        session_id="s1",
        user_identifier="ada",
        page_load=True,
        thread_id="t1",
        make_session=make,
    )
    assert arrival.outcome is ClaimOutcome.KEPT
    assert arrival.session is held
    assert arrival.fresh_page_load is True


async def test_a_session_of_another_user_is_refused_without_a_word() -> None:
    """Saying "that exists but is not yours" is saying that it exists."""
    registry = SessionRegistry()
    registry.register(make("s1"), user_identifier="ada", thread_id="t1")

    arrival = await arrive(
        registry=registry,
        session_id="s1",
        user_identifier="grace",
        page_load=False,
        thread_id="t1",
        make_session=make,
    )
    assert arrival.refused
    assert arrival.session is None


# ------------------------------------------------------------- superseding


async def test_an_abandoned_question_in_this_thread_is_evicted() -> None:
    registry = SessionRegistry()
    abandoned = make("old")
    ask(abandoned)
    registry.register(abandoned, user_identifier="ada", thread_id="t1", connected=False)
    arriving = make("new")
    registry.register(arriving, user_identifier="ada", thread_id="t1")

    evicted = sweep_superseded(registry, "t1", arriving)

    assert [entry.session for entry in evicted] == [abandoned]
    assert registry.get("old") is None
    assert registry.get("new") is not None


async def test_a_running_task_does_not_shield_an_abandoned_question() -> None:
    """A task protects steps from deletion, which is a different question.

    Conflating the two is the regression this sweep exists for: an offer
    that waits for hours keeps its task alive for every one of them.
    """
    registry = SessionRegistry()
    abandoned = make("old")
    ask(abandoned)
    abandoned.current_task = asyncio.ensure_future(asyncio.sleep(30))
    registry.register(abandoned, user_identifier="ada", thread_id="t1", connected=False)
    arriving = make("new")
    registry.register(arriving, user_identifier="ada", thread_id="t1")

    try:
        assert [
            entry.session for entry in sweep_superseded(registry, "t1", arriving)
        ] == [abandoned]
    finally:
        abandoned.current_task.cancel()


async def test_a_connected_tab_showing_the_same_question_is_left_alone() -> None:
    registry = SessionRegistry()
    other_tab = make("old")
    ask(other_tab)
    registry.register(other_tab, user_identifier="ada", thread_id="t1", connected=True)
    arriving = make("new")
    registry.register(arriving, user_identifier="ada", thread_id="t1")

    assert sweep_superseded(registry, "t1", arriving) == []
    assert registry.get("old") is not None


async def test_a_session_of_another_conversation_is_never_touched() -> None:
    registry = SessionRegistry()
    elsewhere = make("old")
    ask(elsewhere)
    registry.register(elsewhere, user_identifier="ada", thread_id="t2", connected=False)
    arriving = make("new")
    registry.register(arriving, user_identifier="ada", thread_id="t1")

    assert sweep_superseded(registry, "t1", arriving) == []
    assert registry.get("old") is not None


# ----------------------------------------------------------------- replay


async def test_the_conversation_is_replayed_under_the_form_in_order() -> None:
    """A form is answered in the light of what came before it."""
    session = make("s1")
    session.transcript = [
        TranscriptEntry(step=StepPayload(id="m1", output="what is your name?")),
        TranscriptEntry(step=StepPayload(id="m2", output="and your quest?")),
    ]
    ask(session)

    await restore(session)

    sent = tags(session)
    assert sent.index("step.upsert") < sent.index("ask.start")
    assert sent.count("step.upsert") == 2


async def test_the_buttons_arrive_before_the_form() -> None:
    """A form that arrives first is a form with no buttons."""
    session = make("s1")
    pending = ask(session)
    pending.restore_actions = [
        {"id": "a1", "name": "yes", "payload": {}},  # type: ignore[list-item]
    ]

    await restore(session)

    sent = tags(session)
    assert sent.index("action.add") < sent.index("ask.start")


async def test_an_attachment_the_server_holds_twice_goes_out_once() -> None:
    session = make("s1")
    element = TextElement(id="el-1", for_id="m1", name="notes")
    session.transcript = [
        TranscriptEntry(step=StepPayload(id="m1"), elements=[element, element])
    ]

    await restore(session)

    assert tags(session).count("element.upsert") == 1


async def test_an_answered_question_is_not_put_back_on_screen() -> None:
    """The reply is free to land in any of the awaits above it."""
    session = make("s1")
    pending = ask(session)
    pending.future.set_result({"kind": "action"})

    await restore(session)

    assert "ask.start" not in tags(session)


async def test_the_form_comes_back_with_what_is_left_of_its_deadline() -> None:
    """A form that resets its timer on every hiccup never times out."""
    session = make("s1")
    ask(session, remaining=12.0)

    await restore(session)

    started = queued(session, AskStart)
    assert len(started) == 1
    assert started[0].spec.timeout <= 12


async def test_the_spinner_is_the_last_thing_said() -> None:
    """It is a boolean, and the honest value is the one true at the end."""
    session = make("s1")
    session.transcript = [TranscriptEntry(step=StepPayload(id="m1"))]

    await restore(session)

    assert tags(session)[-1] == "task.indicator"


# ------------------------------------------------------ the persisted half


class _Store:
    def __init__(self, entries: Sequence[TranscriptEntry]) -> None:
        self.entries = list(entries)

    async def transcript_of(self, thread_id: str) -> Sequence[TranscriptEntry]:
        return self.entries


async def test_an_empty_memory_falls_back_to_what_was_written_down() -> None:
    session = make("s1")
    session.thread_id = "t1"
    session.resumed_thread_id = "t1"
    store = _Store([TranscriptEntry(step=StepPayload(id="m1"))])

    await restore(session, thread_store=store)

    assert tags(session).count("step.upsert") == 1


async def test_a_thread_the_session_was_not_let_into_is_not_read_from_storage() -> None:
    """The id in the hello is the client's claim, not its right.

    The application decides whether a session may resume a thread and marks
    it ``resumed_thread_id``. Without that mark the storage fallback must
    stay shut, or a refused claim is answered from the database anyway.
    """
    session = make("s1")
    session.thread_id = "t1"
    store = _Store([TranscriptEntry(step=StepPayload(id="somebody-elses"))])

    await restore(session, thread_store=store)

    assert "step.upsert" not in tags(session)


async def test_a_live_memory_is_not_overwritten_by_storage() -> None:
    """The session lived through this conversation; storage is the fallback.

    Asserted on *which* step came back, not how many. Both sources hold
    exactly one here, so a count cannot tell the fallback firing wrongly
    from it not firing at all -- which is the mutation that survived the
    first version of this test.
    """
    session = make("s1")
    session.thread_id = "t1"
    session.resumed_thread_id = "t1"
    session.transcript = [TranscriptEntry(step=StepPayload(id="live"))]
    store = _Store([TranscriptEntry(step=StepPayload(id="stored"))])

    await restore(session, thread_store=store)

    assert [item.step.id for item in queued(session, StepUpsert)] == ["live"]
