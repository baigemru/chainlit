"""The other sessions the server is still holding.

A conversation is not one socket. There is the tab the user is looking at,
the tab they left open on another screen, and the session a connection
walked away from and never closed. The server cannot tell the last two apart
by looking at them -- only by what they are still holding, and by whether
anyone is on the other end.

That matters twice over. A session parked on a question nobody will ever
answer keeps the whole conversation looking busy, and a conversation that
looks busy is never tidied up. But a session that is genuinely working, or
genuinely on screen, must survive: evicting it takes work away from a user
who is still there.

One thing is deliberately not stated here: that the arriving session never
sweeps itself up. It cannot -- handing the socket over is what marks a
session connected again, and that happens before any of this runs, so no
scenario can put the server in the state the check defends against. The check
is a belt to a suspender in another function, and the table says nothing
about it rather than pretending to.
"""

from typing import Any, Dict, Mapping, Tuple

from ..frames import Expect
from ..spec import AskState, Bystander, Given, Incoming, Result, Scenario, assert_that

HELLO = Incoming("hello")

THREAD = "thread-1"
KEEP = {"id": "m1", "type": "user_message", "output": "which one?"}
FLAGGED = {
    "id": "m2",
    "type": "assistant_message",
    "output": "offer, valid for an hour",
    "metadata": {"resume_policy": "delete"},
}


def _thread(steps: Tuple[Mapping[str, Any], ...] = (KEEP,)) -> Dict[str, Any]:
    return {"id": THREAD, "steps": list(steps), "elements": []}


def _resuming(
    *bystanders: Bystander, steps: Tuple[Mapping[str, Any], ...] = (KEEP,)
) -> Given:
    return Given(
        resuming_thread=THREAD,
        hooks=("chat_resume",),
        stored_thread=_thread(steps),
        bystanders=bystanders,
    )


def _ids(steps: Any) -> list:
    return [step.get("id") for step in steps]


def _kept(result: Result) -> bool:
    return result.state["evicted"] == []


BYSTANDER_SCENARIOS = (
    Scenario(
        name="a session parked on a question nobody came back to is evicted",
        why=(
            "A question with a long deadline holds its session open for as "
            "long as the deadline lasts. Every reload leaves another one "
            "behind, each still counting as work in progress, and the "
            "conversation is never idle again -- while the user who would "
            "have answered is right here, in the new session."
        ),
        given=_resuming(Bystander(connected=False, pending_ask=AskState())),
        when=(HELLO,),
        then=lambda result: (
            assert_that(
                result.state["evicted"] == ["bystander-0"],
                "the abandoned session was left holding the conversation",
            ),
            assert_that(
                "bystander-0" not in result.state["live_sessions"],
                "the evicted session is still in the registry",
            ),
        ),
    ),
    Scenario(
        name="a second tab showing the same question is left alone",
        why=(
            "Its question is really on screen and the user can really answer "
            "it. Evicting it takes the form away mid-answer, in a tab the "
            "server was given no reason to think was gone."
        ),
        given=_resuming(Bystander(connected=True, pending_ask=AskState())),
        when=(HELLO,),
        then=lambda result: assert_that(
            _kept(result), "a connected session was evicted"
        ),
    ),
    Scenario(
        name="a disconnected session working between questions is left alone",
        why=(
            "Nothing is waiting on the user -- the work is waiting on itself, "
            "and it will post its results when it finishes. Cancelling it "
            "because the socket went away throws away work that was paid for."
        ),
        given=_resuming(Bystander(connected=False, running_task=True)),
        when=(HELLO,),
        then=lambda result: assert_that(
            _kept(result), "a session between questions was evicted"
        ),
    ),
    Scenario(
        name="a session of another conversation is never touched",
        why=(
            "Resuming one conversation says nothing about any other. Widening "
            "the sweep to every abandoned session would let one reconnect "
            "cancel a question waiting in a conversation the user has open "
            "somewhere else."
        ),
        given=_resuming(
            Bystander(connected=False, pending_ask=AskState(), thread="another-thread")
        ),
        when=(HELLO,),
        then=lambda result: assert_that(
            _kept(result), "a session of a different conversation was evicted"
        ),
    ),
    Scenario(
        name="eviction happens first, so the conversation reads as idle again",
        why=(
            "This is what the eviction is for. The abandoned session's work "
            "makes the conversation look alive, and nothing is tidied up "
            "while it does -- so the messages that should not have survived "
            "pile up, one more on every reload."
        ),
        given=_resuming(
            Bystander(
                connected=False,
                pending_ask=AskState(step_id="a-question-of-its-own"),
                running_task=True,
            ),
            steps=(KEEP, FLAGGED),
        ),
        when=(HELLO,),
        then=lambda result: (
            assert_that(
                result.state["evicted"] == ["bystander-0"],
                "the abandoned session survived the resume",
            ),
            assert_that(
                result.state["deleted_steps"] == ["m2"],
                "the conversation still read as busy after the eviction",
            ),
        ),
    ),
    Scenario(
        name="work running in a tab that is still open protects what it produced",
        why=(
            "The messages are not leftovers -- they are being produced right "
            "now, in a session someone is watching. A resume from a second "
            "tab that deleted them would have the two feeds disagree, and the "
            "running work would put its rows back as orphans."
        ),
        given=_resuming(
            Bystander(connected=True, running_task=True), steps=(KEEP, FLAGGED)
        ),
        when=(HELLO,),
        expect=(
            Expect(
                "thread.resume", {"thread.steps": lambda s: _ids(s) == ["m1", "m2"]}
            ),
        ),
        then=lambda result: assert_that(
            result.state["deleted_steps"] == [],
            "a live conversation's messages were deleted from under it",
        ),
    ),
    Scenario(
        name="a question waiting in another session protects its own step",
        why=(
            "Whose session the question belongs to is not the point -- that "
            "somebody is being asked is. Deleting the step from under another "
            "live question leaves that user with nothing to answer."
        ),
        given=_resuming(
            Bystander(connected=True, pending_ask=AskState(step_id="m2")),
            steps=(KEEP, FLAGGED),
        ),
        when=(HELLO,),
        expect=(
            Expect(
                "thread.resume", {"thread.steps": lambda s: _ids(s) == ["m1", "m2"]}
            ),
        ),
        then=lambda result: assert_that(
            result.state["deleted_steps"] == [],
            "another session's live question did not protect its step",
        ),
    ),
)
