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
import uuid
from typing import Any, List, Optional, Sequence

from chainlit.protocol.codec import encode_server
from chainlit.protocol.payloads import AskActionSpec, Step as StepPayload, TextElement
from chainlit.protocol.server import AskStart, StepUpsert
from chainlit.ws.handshake import arrive, restore
from chainlit.ws.registry import ClaimOutcome, SessionRegistry
from chainlit.ws.session import PendingAsk, Session, TranscriptEntry


def make(session_id: str) -> Session:
    return Session(id=session_id)


def factory(thread_id: Optional[str]) -> Session:
    """What ``ApplicationRunner.make_session`` does, minus the database half.

    The handle is minted here because nothing on the wire offers one; the
    thread is minted only when the client named none.
    """
    return Session(id=str(uuid.uuid4()), thread_id=thread_id or str(uuid.uuid4()))


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


async def test_a_first_visit_is_given_a_conversation_of_its_own() -> None:
    """Nothing is offered and nothing is looked up.

    A browser with an empty address bar has no thread to name, so the
    server mints one and says so in ``session.ready``. Nothing is
    *requested*, which is what keeps the resume path out of the database:
    a thread minted a microsecond ago cannot be missing from it.
    """
    registry = SessionRegistry()

    arrival = await arrive(
        registry=registry,
        user_identifier="ada",
        page_load=True,
        thread_id=None,
        make_session=factory,
    )

    assert arrival.outcome is ClaimOutcome.CREATED
    session = arrival.session
    assert session.thread_id
    assert session.requested_thread_id is None
    assert registry.entry_of_thread(session.thread_id) is not None
    assert registry.find(session.id) is session


async def test_a_conversation_nobody_is_in_is_the_one_the_session_gets() -> None:
    """The address bar names a thread the reaper has already taken.

    It is asked for, so the runner will look it up; whether there is
    anything there is the resume's business, not the handshake's.
    """
    registry = SessionRegistry()

    arrival = await arrive(
        registry=registry,
        user_identifier="ada",
        page_load=True,
        thread_id="t1",
        make_session=factory,
    )

    assert arrival.outcome is ClaimOutcome.CREATED
    assert arrival.session.thread_id == "t1"
    assert arrival.session.requested_thread_id == "t1"
    assert registry.entry_of_thread("t1") is not None


async def test_reloading_a_live_conversation_keeps_it() -> None:
    """The reversal at the heart of this stage.

    An idle session used to be thrown away on a page load: F5 re-ran the
    hooks and the ``user_session`` dict started empty. The session *is* the
    conversation now, so the reload is the same session handed a new
    socket -- and ``fresh_page_load`` is the only thing the flag still
    decides, because the browser is holding nothing to draw with.
    """
    registry = SessionRegistry()
    held = make("s1")
    held.thread_id = "t1"
    registry.register(held, thread_id="t1", user_identifier="ada")

    arrival = await arrive(
        registry=registry,
        user_identifier="ada",
        page_load=True,
        thread_id="t1",
        make_session=factory,
    )

    assert arrival.outcome is ClaimOutcome.KEPT
    assert arrival.session is held
    assert arrival.fresh_page_load is True
    assert len(registry) == 1


async def test_a_transport_blip_keeps_it_without_rebuilding_the_screen() -> None:
    """``pageLoad:false`` means the browser still has what it was shown.

    The replay runs either way; what it may skip is the furniture the
    client never lost -- the element a live question is drawn in.
    """
    registry = SessionRegistry()
    held = make("s1")
    held.thread_id = "t1"
    registry.register(held, thread_id="t1", user_identifier="ada")

    arrival = await arrive(
        registry=registry,
        user_identifier="ada",
        page_load=False,
        thread_id="t1",
        make_session=factory,
    )

    assert arrival.outcome is ClaimOutcome.KEPT
    assert arrival.fresh_page_load is False


async def test_coming_back_marks_the_session_connected_again() -> None:
    registry = SessionRegistry()
    held = make("s1")
    held.thread_id = "t1"
    held.connected = False
    entry = registry.register(
        held, thread_id="t1", user_identifier="ada", connected=False
    )

    await arrive(
        registry=registry,
        user_identifier="ada",
        page_load=True,
        thread_id="t1",
        make_session=factory,
    )

    assert held.connected is True
    assert entry.connected is True


async def test_the_reaper_waiting_for_this_user_is_stopped() -> None:
    """The one thing an arrival into a kept session must do.

    The teardown was scheduled when the socket went; the user came back
    inside the grace period, and a reaper left running would take the
    conversation down underneath them a few minutes later.
    """
    registry = SessionRegistry()
    held = make("s1")
    held.thread_id = "t1"
    held.reaper = asyncio.ensure_future(asyncio.sleep(300))
    registry.register(held, thread_id="t1", user_identifier="ada")

    try:
        await arrive(
            registry=registry,
            user_identifier="ada",
            page_load=True,
            thread_id="t1",
            make_session=factory,
        )
    finally:
        if held.reaper is not None:
            held.reaper.cancel()

    assert held.reaper is None


async def test_somebody_elses_conversation_is_answered_with_a_fresh_one() -> None:
    """Not a refusal, and not a word about the thread that was asked for.

    A close code meaning "that is not yours" confirms it exists, and the
    id in a URL is a bearer token in everything but name. The arriving
    user gets a conversation of their own; ``session.ready`` names it, and
    that is how the client learns the address it asked for is not the one
    it got.
    """
    registry = SessionRegistry()
    theirs = make("s1")
    theirs.thread_id = "t1"
    registry.register(theirs, thread_id="t1", user_identifier="ada")

    arrival = await arrive(
        registry=registry,
        user_identifier="grace",
        page_load=True,
        thread_id="t1",
        make_session=factory,
    )

    assert arrival.outcome is ClaimOutcome.CREATED
    assert arrival.session is not theirs
    assert arrival.session.thread_id != "t1"
    # Nothing to look up: a thread somebody is live in is not one whose
    # absence from the database this user may hear about.
    assert arrival.session.requested_thread_id is None


async def test_the_stranger_goes_on_looking_at_their_screen() -> None:
    """Reading the registry is not touching it.

    The foreign session keeps its thread, its socket and its entry: a
    second user guessing a URL must not so much as disconnect the first.
    """
    registry = SessionRegistry()
    theirs = make("s1")
    theirs.thread_id = "t1"
    ask(theirs)
    entry = registry.register(theirs, thread_id="t1", user_identifier="ada")

    await arrive(
        registry=registry,
        user_identifier="grace",
        page_load=True,
        thread_id="t1",
        make_session=factory,
    )

    assert registry.entry_of_thread("t1") is entry
    assert entry.connected is True
    assert theirs.pending_ask is not None
    assert len(registry) == 2


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
