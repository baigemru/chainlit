"""Replies that arrive with nowhere to go, and the parent link.

A reply is a plain message, not a request/response pair, precisely so it can
sit in the client's send buffer across a reconnect. The cost of that choice is
that a reply can outlive the question: the server restarted, the ask timed
out, the user pressed stop. The user typed something either way, and the rule
this fork settled on is that their input is never silently dropped where the
server can still save it.

Which is not the same as saving everything. A click on a dead button is not
content; rescuing it would post a form payload into the conversation.
"""

import uuid
from typing import Optional

from ..frames import Expect
from ..spec import AskState, Given, Incoming, Scenario

TEXT_REPLY = {
    "type": "user_message",
    "output": "the answer nobody was waiting for",
    "createdAt": "2026-08-27T12:00:00.000000Z",
    "id": str(uuid.uuid4()),
}

BUTTON_REPLY = {"id": "a1", "name": "continue", "forId": "step-1"}


def _reply(value: object, step_id: Optional[str] = "step-1") -> Incoming:
    return Incoming("ask.reply", {"stepId": step_id, "value": value})


ORPHAN_SCENARIOS = (
    Scenario(
        name="a typed reply with no question left is rescued as a message",
        superseded=(
            "an answer for a question the session is not showing is parked on "
            "session.parked_replies and delivered when the ask is (re)asked "
            "(ws.connection._deliver_reply, emitter._deliver_parked). It is never "
            "converted into a message, and no ask.end goes out for it."
        ),
        why=(
            "The user typed it. The ask it answered is gone -- server "
            "restart, timeout, stop -- but the words are still content, and "
            "dropping them loses work the user did."
        ),
        given=Given(pending_ask=None),
        when=(_reply(TEXT_REPLY),),
        expect=(Expect("ask.end"), Expect("step.upsert")),
    ),
    Scenario(
        name="a click with no question left only takes the form down",
        superseded=(
            "an answer for a question the session is not showing is parked on "
            "session.parked_replies and delivered when the ask is (re)asked "
            "(ws.connection._deliver_reply, emitter._deliver_parked). It is never "
            "converted into a message, and no ask.end goes out for it."
        ),
        why=(
            "A dead button is not content. Rescuing it would post a form "
            "payload into the conversation as if the user had typed it."
        ),
        given=Given(pending_ask=None),
        when=(_reply(BUTTON_REPLY),),
        expect=(Expect("ask.end"),),
        forbid=("step.upsert",),
    ),
    Scenario(
        name="a redelivered reply that was already answered is not rescued",
        superseded=(
            "an answer for a question the session is not showing is parked on "
            "session.parked_replies and delivered when the ask is (re)asked "
            "(ws.connection._deliver_reply, emitter._deliver_parked). It is never "
            "converted into a message, and no ask.end goes out for it."
        ),
        why=(
            "The slot empties milliseconds after an answer is taken, so a "
            "redelivery -- the send buffer after a blip, a second tab -- "
            "looks exactly like an orphan. Rescuing it would post the user's "
            "answer a second time as a new message."
        ),
        given=Given(pending_ask=None, last_resolved_ask_step_id="step-1"),
        when=(_reply(TEXT_REPLY),),
        expect=(Expect("ask.end"),),
        forbid=("step.upsert",),
    ),
    Scenario(
        name="a reply for another step is ignored while a question is live",
        why=(
            "A stale reply must not answer the question that replaced the "
            "one it was for, must not take that question's form down, and "
            "must not start a second conversation turn beside a server that "
            "is still waiting."
        ),
        given=Given(pending_ask=AskState(step_id="live-step")),
        when=(_reply(TEXT_REPLY, step_id="dead-step"),),
        forbid=("ask.end", "step.upsert", "ask.start"),
    ),
)


PARENT_SCENARIOS = (
    Scenario(
        name="a thread that descends from another says so on every reconnect",
        why=(
            "Until the first interaction persists it, the parent link exists "
            "only on the session -- and the client's copy of it died with the "
            "previous socket."
        ),
        given=Given(restored=True, chat_started=True, parent_thread="thread-a"),
        when=(Incoming("hello"),),
        expect=(Expect("thread.parent", {"parentThreadId": "thread-a"}),),
    ),
    Scenario(
        name="a thread with no parent says nothing about one",
        why="Sending a null parent would be the client's cue to render a link.",
        given=Given(restored=True, chat_started=True, parent_thread=None),
        when=(Incoming("hello"),),
        forbid=("thread.parent",),
    ),
)
