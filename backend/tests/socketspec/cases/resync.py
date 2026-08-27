"""When the server's own memory of the conversation is gone.

A restored session normally replays from memory. But the session that was
handed back is not always the session that did the work: the finishing task's
context can be lost, or the server can rebuild the session outright, and then
memory is empty while persistence has everything.

The rows below are about that fallback -- and about what it must *not* send,
which is the part a rewrite gets wrong. Persistence holds the whole thread,
including the machinery steps the user never sees.
"""

from typing import Any, Dict, Mapping, Optional, Tuple

from ..frames import Expect
from ..spec import Given, Incoming, Result, Scenario

HELLO = Incoming("hello")

MESSAGE = {"id": "m1", "type": "user_message", "output": "hello"}
REPLY = {"id": "m2", "type": "assistant_message", "output": "hi"}
MACHINERY = {"id": "s1", "type": "run", "output": "{}"}
ATTACHMENT = {"id": "el-1", "forId": "m1"}
FOREIGN_ATTACHMENT = {"id": "el-2", "forId": "not-in-this-thread"}


def _thread(
    steps: Tuple[Mapping[str, Any], ...] = (),
    elements: Tuple[Mapping[str, Any], ...] = (),
) -> Dict[str, Any]:
    return {"id": "test_thread_id", "steps": list(steps), "elements": list(elements)}


def _restored(
    *,
    stored_thread: Optional[Mapping[str, Any]] = None,
    has_first_interaction: bool = True,
) -> Given:
    return Given(
        restored=True,
        chat_started=True,
        has_first_interaction=has_first_interaction,
        transcript=(),
        stored_thread=stored_thread,
    )


RESYNC_SCENARIOS = (
    Scenario(
        name="an empty memory falls back to what was persisted",
        why=(
            "The session handed back is not always the one that did the work. "
            "Without the fallback the user reconnects into a conversation the "
            "server is holding but cannot show them."
        ),
        given=_restored(stored_thread=_thread(steps=(MESSAGE, REPLY))),
        when=(HELLO,),
        expect=(
            Expect("step.upsert", {"step.id": "m1"}),
            Expect("step.upsert", {"step.id": "m2"}),
        ),
    ),
    Scenario(
        name="the machinery steps stay out of the replay",
        why=(
            "Persistence holds the whole thread, tool runs and all. Replaying "
            "those puts internals into the conversation the user reads."
        ),
        given=_restored(stored_thread=_thread(steps=(MESSAGE, MACHINERY))),
        when=(HELLO,),
        expect=(Expect("step.upsert", {"step.id": "m1"}),),
        then=lambda result: _assert(
            _count(result, "step.upsert", "step.id", "s1") == 0,
            "a machinery step was replayed into the conversation",
        ),
    ),
    Scenario(
        name="persisted attachments come back with their step",
        why="A resumed conversation without its images is not the conversation.",
        given=_restored(
            stored_thread=_thread(steps=(MESSAGE,), elements=(ATTACHMENT,))
        ),
        when=(HELLO,),
        expect=(
            Expect("step.upsert", {"step.id": "m1"}),
            Expect("element.upsert", {"element.id": "el-1"}),
        ),
    ),
    Scenario(
        name="an attachment belonging to nothing here is not replayed",
        why=(
            "It would arrive addressed to a step this client does not have, "
            "and hang in the feed with nothing to attach to."
        ),
        given=_restored(
            stored_thread=_thread(steps=(MESSAGE,), elements=(FOREIGN_ATTACHMENT,))
        ),
        when=(HELLO,),
        then=lambda result: _assert(
            _count(result, "element.upsert", "element.id", "el-2") == 0,
            "an orphan attachment was replayed",
        ),
    ),
    Scenario(
        name="without persistence there is nothing to fall back to",
        why="Nothing to read, and no reason to pretend otherwise.",
        given=_restored(stored_thread=None),
        when=(HELLO,),
        forbid=("step.upsert",),
    ),
    Scenario(
        name="a thread persistence has never heard of replays nothing",
        why=(
            "An empty read is an answer, not a reason to retry differently -- "
            "and not a reason to send an empty conversation over the one the "
            "client is already showing."
        ),
        given=_restored(stored_thread={}),
        when=(HELLO,),
        forbid=("step.upsert",),
    ),
    Scenario(
        name="before the first interaction there is nothing persisted yet",
        why=(
            "The thread row does not exist until the conversation starts. "
            "Reading here costs a query on every reconnect of every session "
            "that never said anything."
        ),
        given=_restored(
            stored_thread=_thread(steps=(MESSAGE,)), has_first_interaction=False
        ),
        when=(HELLO,),
        forbid=("step.upsert",),
    ),
)


def _count(result: Result, tag: str, path: str, value: object) -> int:
    return result.ledger.count(Expect(tag, {path: value}))


def _assert(condition: object, message: str) -> bool:
    assert condition, message
    return True
