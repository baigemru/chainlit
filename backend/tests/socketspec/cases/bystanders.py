"""The sessions that are not this one -- and why none of them is in this thread.

A conversation used to be several sockets: the tab the user was looking at,
the tab they left open on another screen, and the session a connection
walked away from without closing. The server could not tell the last two
apart except by what they were still holding, and an eviction sweep decided
which of them was holding the conversation open for nobody.

None of that exists. A thread holds one session, so a second connection to
it is that session changing hands -- there is nothing left to sweep, and
``Bystander`` can now only mean *another conversation*. The driver refuses a
row that states one in this thread rather than pretending to build it.

What is left is the rule that survived the collapse: one conversation says
nothing about any other. A resume reads the thread it is resuming, and the
work and questions of every other thread are none of its business.

The other half of the old family -- a live conversation's flagged messages
being protected from a reader -- moved with the reader. It is the HTTP read
path now (``controllers.project.hide_resume_deleted``), pinned in
``tests/controllers/test_project.py``, because the only reader that can meet
a live session is the one that is not it.
"""

from typing import Any, Dict, Mapping, Tuple

from ..frames import Expect
from ..spec import AskState, Bystander, Given, Incoming, Result, Scenario, assert_that

HELLO = Incoming("hello")

THREAD = "thread-1"
ELSEWHERE = "another-thread"
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
    """Opening a conversation nobody is in, while other ones are live."""
    return Given(
        server_holds_session=False,
        resuming_thread=THREAD,
        hooks=("chat_resume",),
        stored_thread=_thread(steps),
        bystanders=bystanders,
    )


def _ids(steps: Any) -> list:
    return [step.get("id") for step in steps]


def _survived(result: Result) -> bool:
    return "bystander-0" in result.state["live_sessions"]


BYSTANDER_SCENARIOS = (
    Scenario(
        name="a session of another conversation is never touched",
        why=(
            "Resuming one conversation says nothing about any other. A rule "
            "that reached past the thread it was asked about would let one "
            "reconnect cancel a question waiting in a conversation the user "
            "has open somewhere else."
        ),
        given=_resuming(
            Bystander(connected=False, pending_ask=AskState(), thread=ELSEWHERE)
        ),
        when=(HELLO,),
        then=lambda result: assert_that(
            _survived(result), "a session of a different conversation was evicted"
        ),
    ),
    Scenario(
        name="work running in another conversation protects nothing here",
        why=(
            "Protection is about the thread being read, not about the server "
            "being busy. A query that answered 'something is running "
            "somewhere' would leave every flagged message on screen for as "
            "long as any user anywhere had work in flight."
        ),
        given=_resuming(
            Bystander(connected=True, running_task=True, thread=ELSEWHERE),
            steps=(KEEP, FLAGGED),
        ),
        when=(HELLO,),
        expect=(
            Expect("thread.resume", {"thread.steps": lambda s: _ids(s) == ["m1"]}),
        ),
        then=lambda result: assert_that(
            _survived(result), "the other conversation's session was torn down"
        ),
    ),
    Scenario(
        name="a question waiting in another conversation protects nothing here",
        why=(
            "Same rule, stated about the other protection query: the step "
            "ids a live question holds are the ones in *its* thread. Reading "
            "them across threads would keep a stranger's offer on this "
            "user's screen because the ids happened to collide."
        ),
        given=_resuming(
            Bystander(
                connected=True, pending_ask=AskState(step_id="m2"), thread=ELSEWHERE
            ),
            steps=(KEEP, FLAGGED),
        ),
        when=(HELLO,),
        expect=(
            Expect("thread.resume", {"thread.steps": lambda s: _ids(s) == ["m1"]}),
        ),
    ),
    Scenario(
        name="nothing is ever evicted on arrival",
        why=(
            "The sweep is gone, and its absence is worth a row: a thread "
            "holds one session, so the arriving connection either is that "
            "session or is in a thread of its own. An arrival that tore "
            "anything down would be tearing down a conversation nobody asked "
            "it about."
        ),
        given=_resuming(
            Bystander(connected=False, pending_ask=AskState(), thread=ELSEWHERE),
            Bystander(connected=True, running_task=True, thread="a-third-thread"),
        ),
        when=(HELLO,),
        then=lambda result: assert_that(
            result.state["evicted"] == [],
            f"the arrival evicted {result.state['evicted']}",
        ),
    ),
)
