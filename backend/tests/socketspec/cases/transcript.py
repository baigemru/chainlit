"""Replaying the conversation the client lost.

A reconnecting client has whatever its browser still holds -- possibly
nothing. The server holds the conversation, so on every reconnect of a
session it kept, it re-sends it. The rows below are about *when* that
happens, *what* goes with it, and what must not go twice.

The client upserts steps and elements by id, which is what makes re-sending
safe; it is also what makes "send it again" the correct answer to "we are not
sure it arrived".
"""

from typing import Optional

from ..frames import Expect
from ..spec import (
    AskState,
    Given,
    Incoming,
    Result,
    Scenario,
    TranscriptStep,
    assert_that,
)

HELLO = Incoming("hello")

FIRST = TranscriptStep(id="m1", output="what is your name?")
SECOND = TranscriptStep(id="m2", output="and your quest?")
ATTACHMENT = {"id": "el-1", "forId": "m1"}


def _restored(
    *,
    transcript: tuple = (),
    pending_ask: Optional[AskState] = None,
    fresh_page_load: bool = True,
    restored: bool = True,
) -> Given:
    return Given(
        restored=restored,
        chat_started=True,
        fresh_page_load=fresh_page_load,
        transcript=transcript,
        pending_ask=pending_ask,
    )


def _count(result: Result, tag: str, path: str, value: object) -> int:
    from ..frames import Expect as _Expect

    matcher = _Expect(tag, {path: value})
    return sum(1 for frame in result.ledger.frames if matcher.matches(frame))


TRANSCRIPT_SCENARIOS = (
    Scenario(
        name="the conversation is replayed under the form, in order",
        why=(
            "A form is answered in the light of what came before it -- the "
            "results of the flow that asked. Sending the form first, or the "
            "messages out of order, changes what the user is answering."
        ),
        given=_restored(transcript=(FIRST, SECOND), pending_ask=AskState()),
        when=(HELLO,),
        expect=(
            Expect("step.upsert", {"step.id": "m1"}),
            Expect("step.upsert", {"step.id": "m2"}),
            Expect("ask.start"),
        ),
    ),
    Scenario(
        name="a transport reconnect replays too, not only a page load",
        why=(
            "Emits into a dying socket are dropped on the floor. 'The client "
            "did not reload' is not evidence that it received anything, so "
            "the blip has to converge the same way the reload does."
        ),
        given=_restored(transcript=(FIRST,), fresh_page_load=False),
        when=(HELLO,),
        expect=(Expect("step.upsert", {"step.id": "m1"}),),
    ),
    Scenario(
        name="a session the server did not keep replays nothing",
        why=(
            "There is no conversation to re-send: this client is starting "
            "one. Replaying here would put someone else's history on screen."
        ),
        given=_restored(transcript=(FIRST,), restored=False),
        when=(HELLO,),
        forbid=("step.upsert",),
    ),
    Scenario(
        name="attachments come back with their message",
        why=(
            "A message rebuilt from storage carries no element objects, so "
            "the attachments have to travel from what was recorded when the "
            "conversation was rebuilt -- otherwise a resumed thread loses "
            "every image in it."
        ),
        given=_restored(
            transcript=(TranscriptStep(id="m1", stored_elements=(ATTACHMENT,)),)
        ),
        when=(HELLO,),
        expect=(
            Expect("step.upsert", {"step.id": "m1"}),
            Expect("element.upsert", {"element.id": "el-1"}),
        ),
    ),
    Scenario(
        name="an attachment held both live and stored is sent once",
        why=(
            "The two sources overlap while a session is alive. Sending both "
            "would upsert the same element twice on every reconnect."
        ),
        given=_restored(
            transcript=(
                TranscriptStep(
                    id="m1", elements=(ATTACHMENT,), stored_elements=(ATTACHMENT,)
                ),
            )
        ),
        when=(HELLO,),
        expect=(Expect("element.upsert", {"element.id": "el-1"}),),
        then=lambda result: assert_that(
            _count(result, "element.upsert", "element.id", "el-1") == 1,
            "the same attachment went out twice",
        ),
    ),
    Scenario(
        name="a running shimmer survives the replay",
        superseded=(
            "payload drift: protocol.payloads.Wait carries texts/intervalMs/loop, not "
            "kind, so the row can never match a real frame. Respell against "
            "step.wait.texts."
        ),
        why=(
            "The client force-overwrites a step's wait state on every upsert, "
            "so a replay that omits it stops a spinner for work that is still "
            "running -- and the user is told it finished."
        ),
        given=_restored(
            transcript=(TranscriptStep(id="m1", wait={"kind": "thinking"}),)
        ),
        when=(HELLO,),
        expect=(
            Expect("step.upsert", {"step.id": "m1", "step.wait.kind": "thinking"}),
        ),
    ),
)
