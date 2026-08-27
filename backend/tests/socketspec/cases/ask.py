"""The ask lifecycle: the hardest thing the transport carries.

An ask is the one place where the server is *waiting* on the client, so every
reconnect has to decide whether the question on screen is still the question
being waited on. Getting it wrong is not a cosmetic bug: a cleared form over
a live ask leaves the user with no way to answer something the server will
block on until it times out, and a re-emitted form over a dead one collects
an answer nobody reads.

The fork rewrote this once already (asks moved off socket.io acks onto an
event plus a future with a monotonic deadline). These rows are what that work
established, restated so the next transport has to satisfy it too.
"""

from typing import Optional

from ..frames import Expect
from ..spec import AskState, Given, Incoming, Scenario

ACTION = {"id": "a1", "name": "continue", "forId": "step-1"}
ELEMENT = {"id": "el-1", "forId": "step-1"}

HELLO = Incoming("hello")


def _reconnect(
    *,
    pending_ask: Optional[AskState] = None,
    fresh_page_load: bool = True,
) -> Given:
    """A session handed back to a client that reconnected."""
    return Given(
        restored=True,
        chat_started=True,
        fresh_page_load=fresh_page_load,
        pending_ask=pending_ask,
    )


ASK_SCENARIOS = (
    Scenario(
        name="live ask is re-emitted with the time that is actually left",
        why=(
            "The deadline is absolute and set when the ask was first sent. "
            "Re-sending the original timeout would let a client reconnect its "
            "way to an unbounded wait."
        ),
        given=_reconnect(pending_ask=AskState(remaining=5, actions=(ACTION,))),
        when=(HELLO,),
        expect=(
            Expect("action.add", {"action.id": "a1"}),
            Expect(
                "ask.start",
                {
                    "step.id": "step-1",
                    "spec.timeout": lambda value: 1 <= value <= 5,
                },
            ),
        ),
        forbid=("ask.end",),
    ),
    Scenario(
        name="actions go out before the form that needs them",
        why=(
            "The client renders the ask form against the actions it holds; a "
            "form that arrives first is a form with no buttons."
        ),
        given=_reconnect(pending_ask=AskState(actions=(ACTION,))),
        when=(HELLO,),
        expect=(Expect("action.add"), Expect("ask.start")),
    ),
    Scenario(
        name="a page reload gets the ask element back",
        why="A reload lost the element; nothing is left to preserve.",
        given=_reconnect(pending_ask=AskState(element=ELEMENT)),
        when=(HELLO,),
        expect=(Expect("element.upsert", {"element.id": "el-1"}), Expect("ask.start")),
    ),
    Scenario(
        name="a transport reconnect keeps the element the client still holds",
        why=(
            "Re-sending it remounts the custom element and wipes whatever the "
            "user had typed into it. The buttons are re-sent regardless -- "
            "they are addressed by id, so a duplicate is free, and 'no page "
            "load' never proved they arrived."
        ),
        given=_reconnect(
            fresh_page_load=False,
            pending_ask=AskState(actions=(ACTION,), element=ELEMENT),
        ),
        when=(HELLO,),
        expect=(Expect("action.add"), Expect("ask.start")),
        forbid=("element.upsert",),
    ),
    Scenario(
        name="no pending ask means the form is cleared",
        why="A form left on screen collects an answer nobody is waiting for.",
        given=_reconnect(pending_ask=None),
        when=(HELLO,),
        expect=(Expect("ask.end"),),
        forbid=("ask.start",),
    ),
    Scenario(
        name="an expired ask is cleared, not re-emitted",
        why=(
            "The deadline passed while the socket was down. Re-sending the "
            "form would invite an answer the waiter has already given up on."
        ),
        given=_reconnect(pending_ask=AskState(remaining=None)),
        when=(HELLO,),
        expect=(Expect("ask.end"),),
        forbid=("ask.start",),
    ),
    Scenario(
        name="an already-answered ask is cleared, not re-emitted",
        why="The answer is in flight; a second form would collect a second one.",
        given=_reconnect(pending_ask=AskState(answered=True)),
        when=(HELLO,),
        expect=(Expect("ask.end"),),
        forbid=("ask.start",),
    ),
    Scenario(
        name="an in-flight call is always cancelled on reconnect",
        why=(
            "A call was correlated through a socket.io ack, which is bound to "
            "the socket id and cannot survive the reconnect. The new protocol "
            "gives it a callId; until then the only safe move is to cancel."
        ),
        given=_reconnect(pending_ask=AskState()),
        when=(HELLO,),
        expect=(Expect("rpc.cancel"),),
    ),
    Scenario(
        name="a reply resolves the ask it names",
        why="This is the whole point of the ask lifecycle.",
        given=Given(pending_ask=AskState()),
        when=(Incoming("ask.reply", {"stepId": "step-1", "value": {"name": "go"}}),),
        then=lambda result: (
            _assert(result.state["ask_resolved"], "the ask was not resolved"),
            _assert(
                result.state["ask_answer"] == {"name": "go"},
                "the answer did not reach the waiter",
            ),
            _assert(
                result.state["last_resolved_ask_step_id"] == "step-1",
                "the resolved step was not recorded",
            ),
        ),
    ),
    Scenario(
        name="a reply naming another step does not resolve the live ask",
        why=(
            "A buffered reply to a question that is already gone must not "
            "answer the question that replaced it."
        ),
        given=Given(pending_ask=AskState()),
        when=(Incoming("ask.reply", {"stepId": "other-step", "value": "x"}),),
        then=lambda result: _assert(
            not result.state["ask_resolved"], "a stale reply resolved a live ask"
        ),
    ),
    Scenario(
        name="a second reply does not overwrite the first",
        why=(
            "The client may redeliver a buffered reply after the reconnect "
            "that already delivered it."
        ),
        given=Given(pending_ask=AskState(answered=True)),
        when=(Incoming("ask.reply", {"stepId": "step-1", "value": "second"}),),
        then=lambda result: _assert(
            result.state["ask_answer"] == "already answered",
            "a duplicate reply overwrote the answer",
        ),
    ),
    Scenario(
        name="a reply with nothing in it is ignored",
        why="A malformed frame must not take the connection down.",
        given=Given(pending_ask=AskState()),
        when=(Incoming("ask.reply", {}),),
        then=lambda result: _assert(
            not result.state["ask_resolved"], "an empty reply resolved the ask"
        ),
    ),
    Scenario(
        name="stop cancels the ask and takes the form down",
        why=(
            "The user pressed stop while a question was on screen. Leaving "
            "the form up would offer an answer to a waiter that no longer "
            "exists."
        ),
        given=Given(pending_ask=AskState()),
        when=(Incoming("stop"),),
        expect=(Expect("ask.end"),),
        then=lambda result: (
            _assert(result.state["ask_cancelled"], "stop did not cancel the ask"),
            _assert(
                not result.state["ask_pending"],
                "stop left the ask slot occupied, so a follow-up ask would be refused",
            ),
        ),
    ),
)


def _assert(condition: object, message: str) -> bool:
    """Assert inside a lambda: a scenario's ``then`` is data, not a function body."""
    assert condition, message
    return True
