"""The handshake: what happens between "the socket is up" and "the app runs".

Everything here is about a session that already exists when the client
arrives -- handed over from a profile switch, or handed back after a
reconnect. Two things make this the densest corner of the protocol: the
handover has to be readable by the application's start callback, which pins
it *before* the callback is scheduled; and the client may say hello more than
once for one session, which pins the callback to running exactly once no
matter which branch each hello takes.
"""

from ..frames import Expect
from ..spec import Given, Handover, Incoming, Scenario, assert_that

HELLO = Incoming("hello")


def _fresh(**kwargs) -> Given:
    """A session handed its id by a switch, before anyone has interacted."""
    return Given(restored=True, has_first_interaction=False, **kwargs)


HANDSHAKE_SCENARIOS = (
    Scenario(
        name="the handover is readable by the start callback that runs on it",
        why=(
            "The whole point of parking the message server-side is that the "
            "application reads it out of the user session on start. Applying "
            "it after the callback is scheduled loses the race silently -- "
            "the app sees nothing and answers a question nobody asked."
        ),
        given=_fresh(
            hooks=("chat_start",),
            handover=Handover(message="Compare the two offers", parent="thread-a"),
        ),
        when=(HELLO,),
        expect=(
            Expect(
                "thread.first_interaction", {"interaction": "Compare the two offers"}
            ),
            Expect("thread.parent", {"parentThreadId": "thread-a"}),
        ),
        then=lambda result: (
            assert_that(
                result.state["chat_start_saw_handover"] == "Compare the two offers",
                "the start callback could not see the handover",
            ),
            assert_that(
                result.state["hook_runs"].get("chat_start") == 1,
                "the start callback did not run exactly once",
            ),
            assert_that(
                result.state["has_first_interaction"],
                "claiming a handover did not count as the first interaction",
            ),
        ),
    ),
    Scenario(
        name="a handover carrying only a parent links the thread without starting it",
        why=(
            "A profile switch with no message still has to tell the new "
            "thread what it descends from. Counting that as an interaction "
            "would create and name an empty thread for a user who has not "
            "said anything yet."
        ),
        given=_fresh(handover=Handover(parent="thread-a")),
        when=(HELLO,),
        expect=(Expect("thread.parent", {"parentThreadId": "thread-a"}),),
        forbid=("thread.first_interaction",),
        then=lambda result: assert_that(
            not result.state["has_first_interaction"],
            "a parent-only handover counted as an interaction",
        ),
    ),
    Scenario(
        name="an empty handover still counts, and the profile names the thread",
        why=(
            "Presence is not truthiness: an empty string is a message that "
            "was handed over, and the thread it opens has to be named "
            "something. Testing the value for truth instead of for presence "
            "drops the handover and leaves the thread unnamed."
        ),
        given=_fresh(chat_profile="Support", handover=Handover(message="")),
        when=(HELLO,),
        expect=(Expect("thread.first_interaction", {"interaction": "Support"}),),
        then=lambda result: assert_that(
            result.state["handover_delivered"] == "",
            "an empty handover was dropped instead of delivered",
        ),
    ),
    Scenario(
        name="a session that has already interacted leaves the record for its successor",
        superseded=(
            "handshake.restore re-sends thread.first_interaction on every kept "
            "arrival (a page load loses it client-side), so the frame no longer "
            "evidences a handover being delivered; a kept arrival never claims the "
            "transit record (runner.on_arrival returns before _claim_transit). "
            "Inferred from the code, not from the reversal list -- may be "
            "reclassified as a bug."
        ),
        why=(
            "The record is parked under an id the current session still "
            "answers to. If its socket flaps between the park and the claim, "
            "it re-runs the handshake -- and swallowing the record there "
            "would deliver the message to the chat it was meant to replace."
        ),
        given=Given(
            restored=True,
            chat_started=True,
            handover=Handover(message="for the successor"),
        ),
        when=(HELLO,),
        forbid=("thread.first_interaction", "thread.parent"),
        then=lambda result: assert_that(
            result.state["handover_parked"],
            "the record meant for the successor was swallowed",
        ),
    ),
    Scenario(
        name="a record parked by another user is dropped, not delivered",
        why=(
            "Session ids are handed out by the server, but a claim still "
            "has to prove ownership: delivering another user's parked "
            "message is a cross-account leak, and leaving it parked is one "
            "that waits."
        ),
        given=_fresh(
            handover=Handover(
                message="someone else's", parent="their-thread", foreign=True
            )
        ),
        when=(HELLO,),
        forbid=("thread.first_interaction", "thread.parent"),
        then=lambda result: (
            assert_that(
                result.state["handover_delivered"] is None,
                "another user's message was delivered",
            ),
            assert_that(
                result.state["parent_thread_id"] is None,
                "another user's thread was linked as the parent",
            ),
            assert_that(
                not result.state["handover_parked"],
                "a foreign record was left parked for someone else to claim",
            ),
        ),
    ),
    Scenario(
        name="the start callback runs once however often the client says hello",
        why=(
            "One session can be greeted many times -- a transport blip, a "
            "second tab, a client retry. Running the application's start "
            "callback again would replay its opening messages over a "
            "conversation already in progress."
        ),
        given=Given(hooks=("chat_start",)),
        when=(HELLO, HELLO),
        then=lambda result: assert_that(
            result.state["hook_runs"].get("chat_start") == 1,
            "the start callback did not run exactly once",
        ),
    ),
    Scenario(
        name="the start callback runs once even when the handover opens no thread",
        why=(
            "A handover carrying only a parent leaves the session "
            "un-interacted, so every later hello takes the same branch again. "
            "The guard has to sit on the session, not on the branch -- one "
            "per branch is one the second hello walks straight past."
        ),
        given=_fresh(hooks=("chat_start",), handover=Handover(parent="thread-a")),
        when=(HELLO, HELLO),
        then=lambda result: assert_that(
            result.state["hook_runs"].get("chat_start") == 1,
            "the start callback did not run exactly once",
        ),
    ),
    Scenario(
        name="the start callback runs once across two different handshake branches",
        superseded=(
            "thread.first_interaction is re-sent by every restore "
            "(handshake.restore), so 'opened once' can no longer be counted in "
            "frames. The start-once guard across the handover and reconnect branches "
            "is the sibling row 'the start callback runs once even when the handover "
            "opens no thread'. Inferred, not enumerated -- may be reclassified."
        ),
        why=(
            "The first hello takes the handover branch and the second cannot "
            "-- claiming the handover made this an interacted session. A "
            "guard that lived in the branch rather than on the session would "
            "let the second hello start the app a second time."
        ),
        given=_fresh(hooks=("chat_start",), handover=Handover(message="go")),
        when=(HELLO, HELLO),
        then=lambda result: (
            assert_that(
                result.state["hook_runs"].get("chat_start") == 1,
                "the start callback did not run exactly once",
            ),
            assert_that(
                result.ledger.count(Expect("thread.first_interaction")) == 1,
                "the thread was opened twice",
            ),
        ),
    ),
)
