"""Messages that do not survive a resume.

Some messages are only meaningful while the conversation is live -- an offer
with a deadline, a prompt tied to a session that has ended. A step flagged
that way is stripped from the resumed conversation, deleted from storage
together with what hangs off it, and taken off the client's screen.

Deleting anything on a reconnect is the dangerous end of the protocol, so
every row here is as much about what must *not* be deleted: a thread whose
question is still being waited on, and a reconnect of a session that already
did the deletion once.
"""

from typing import Any, Dict, Mapping, Tuple

from ..frames import Expect
from ..spec import AskState, Given, Incoming, Result, Scenario, assert_that

HELLO = Incoming("hello")

THREAD = "thread-1"
KEEP = {"id": "m1", "type": "user_message", "output": "which one?"}
FLAGGED = {
    "id": "m2",
    "type": "assistant_message",
    "output": "offer, valid for an hour",
    "metadata": {"resume_policy": "delete"},
}
CHILD = {"id": "m3", "parentId": "m2", "type": "run", "output": "{}"}
ATTACHMENT = {"id": "el-2", "forId": "m2"}
LATER = {
    "id": "m9",
    "type": "assistant_message",
    "output": "a fresh offer, made after the resume",
    "metadata": {"resume_policy": "delete"},
}


def _thread(
    steps: Tuple[Mapping[str, Any], ...],
    elements: Tuple[Mapping[str, Any], ...] = (),
) -> Dict[str, Any]:
    return {"id": THREAD, "steps": list(steps), "elements": list(elements)}


def _resuming(thread: Dict[str, Any], **kwargs: Any) -> Given:
    return Given(
        resuming_thread=THREAD,
        hooks=("chat_resume",),
        stored_thread=thread,
        **kwargs,
    )


def _ids(steps: Any) -> list:
    return [step.get("id") for step in steps]


def _shows(step_id: str) -> Expect:
    return Expect(
        "thread.resume", {"thread.steps": lambda steps: step_id in _ids(steps)}
    )


def _resumed_steps(result: Result) -> list:
    return _ids((result.state.get("chat_resume_thread") or {}).get("steps") or [])


RESUME_DELETE_SCENARIOS = (
    Scenario(
        name="a flagged step is stripped from the resumed conversation and deleted",
        why=(
            "The flag exists because the message stops being true when the "
            "conversation goes away. Rebuilding the feed with it still in "
            "place shows the user an offer the server will not honour."
        ),
        given=_resuming(_thread((KEEP, FLAGGED), (ATTACHMENT,))),
        when=(HELLO,),
        expect=(
            Expect("thread.first_interaction", {"interaction": "resume"}),
            Expect("thread.resume", {"thread.steps": lambda s: _ids(s) == ["m1"]}),
            Expect("step.delete", {"step.id": "m2"}),
        ),
        then=lambda result: (
            assert_that(
                result.state["deleted_steps"] == ["m2"], "the flagged step was kept"
            ),
            assert_that(
                result.state["deleted_elements"] == ["el-2"],
                "the flagged step's attachment was left behind",
            ),
            assert_that(
                _resumed_steps(result) == ["m1"],
                "the application was handed the step the user never sees",
            ),
        ),
    ),
    Scenario(
        name="what hangs off a doomed step goes with it",
        why=(
            "A child left behind points at a parent that no longer exists. "
            "It renders as a top-level message of its own -- the internals of "
            "a deleted exchange, surfaced to the user."
        ),
        given=_resuming(_thread((KEEP, FLAGGED, CHILD))),
        when=(HELLO,),
        expect=(
            Expect("thread.resume", {"thread.steps": lambda s: _ids(s) == ["m1"]}),
        ),
        then=lambda result: assert_that(
            result.state["deleted_steps"] == ["m2", "m3"],
            "the orphaned child of a deleted step survived",
        ),
    ),
    Scenario(
        name="the deletion happens on the first resume, not on every reconnect",
        why=(
            "One session resumes once and reconnects many times. Re-running "
            "the decision on a reconnect would delete the messages the "
            "resumed conversation has produced since -- the flag means 'does "
            "not survive a resume', not 'does not survive a reconnect'."
        ),
        given=_resuming(_thread((KEEP, FLAGGED), (ATTACHMENT,))),
        when=(HELLO, HELLO),
        then=lambda result: (
            assert_that(
                result.ledger.count(Expect("thread.resume")) == 2,
                "the second handshake never re-entered the resume branch",
            ),
            assert_that(
                result.state["deleted_steps"] == ["m2"],
                "the reconnect ran the deletion a second time",
            ),
            assert_that(
                result.ledger.count(Expect("step.delete")) == 1,
                "the client was told twice to remove the same step",
            ),
        ),
    ),
    Scenario(
        name="a message produced after the resume survives the next reconnect",
        why=(
            "The flag means the message does not survive a resume, not that "
            "it does not survive a reconnect. A conversation that resumed and "
            "then made a fresh offer would lose it to the next transport "
            "blip -- the user watches a live offer disappear."
        ),
        given=_resuming(_thread((KEEP,)), produced_between_connections=(LATER,)),
        when=(HELLO, HELLO),
        expect=(Expect("thread.resume", {"thread.steps": lambda s: "m9" in _ids(s)}),),
        then=lambda result: assert_that(
            result.state["deleted_steps"] == [],
            "a reconnect deleted a message the resume never saw",
        ),
    ),
    Scenario(
        name="a step whose attachment cannot be deleted is kept, hidden and retried",
        why=(
            "Deleting the step first would orphan the attachment forever -- "
            "its owner never enters the doomed set again, and no later pass "
            "would know to look for it. Keeping the step leaves the state "
            "retryable, so it must not be visible in the meantime."
        ),
        given=_resuming(_thread((KEEP, FLAGGED), (ATTACHMENT,)), undeletable=("el-2",)),
        when=(HELLO, HELLO),
        then=lambda result: (
            assert_that(
                result.state["deleted_elements"] == ["el-2", "el-2"],
                "the failed deletion was not retried on the next resume",
            ),
            assert_that(
                result.state["deleted_steps"] == [],
                "the step was deleted while its attachment still existed",
            ),
            assert_that(
                result.ledger.count(_shows("m2")) == 0,
                "a step kept only because its deletion failed was shown anyway",
            ),
        ),
    ),
    Scenario(
        name="a step a live question is waiting on is never deleted",
        why=(
            "The flag says the message does not outlive the conversation, "
            "and a question still being waited on says the conversation is "
            "very much alive. Deleting it takes away the user's only way to "
            "answer something the server is blocked on."
        ),
        given=_resuming(
            _thread((KEEP, FLAGGED)), pending_ask=AskState(step_id="m2", remaining=60)
        ),
        when=(HELLO,),
        expect=(
            Expect(
                "thread.resume", {"thread.steps": lambda s: _ids(s) == ["m1", "m2"]}
            ),
        ),
        forbid=("step.delete",),
        then=lambda result: assert_that(
            result.state["deleted_steps"] == [],
            "the step of a live question was deleted",
        ),
    ),
)
