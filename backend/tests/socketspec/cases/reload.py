"""What a page reload means for the conversation it lands in.

Reloading used to mean "start over": the client offered a session id, an
idle session under it was thrown away, and the hooks ran again. It does not
mean that any more, and this file is where the reversal is written down.

The client names a *thread* and only a thread -- the one in the address bar
-- so a reload, a duplicated tab and a dropped network all say the same
sentence, and the answer to all three is the session that is in that thread.
What still differs is how much the browser is holding: a page load lost its
screen and needs the whole replay, a transport blip did not.

Starting over is now a thing the user asks for by name: "New chat" is
``session.clear``, which gives the conversation up outright. That is stated
in ``tests/test_reload_ask.py`` and in the live socket tests, because it
ends a session the table has no vocabulary for ending.
"""

from ..spec import AskState, Given, Incoming, Scenario, assert_that

RELOAD = Incoming("hello", {"pageLoad": True})
RECONNECT = Incoming("hello", {"pageLoad": False})


def _outcome(result, expected: str, message: str) -> bool:
    return assert_that(
        result.state["on_open"] == expected,
        f"{message} (the connection was {result.state['on_open']})",
    )


RELOAD_SCENARIOS = (
    Scenario(
        name="reloading an idle conversation hands it back",
        why=(
            "The session is the conversation. Starting a new one under the "
            "same address left the thread holding a screen the application "
            "had never greeted -- and threw away the hooks' work, the "
            "user_session dict and everything the app had put in it, on a "
            "gesture the user makes to see the page again."
        ),
        given=Given(chat_started=True),
        when=(RELOAD,),
        then=lambda result: (
            _outcome(result, "kept", "a reload started a new conversation"),
            assert_that(
                result.state["fresh_page_load"],
                "the client kept its screen across a page load",
            ),
        ),
    ),
    Scenario(
        name="reloading while a question is waiting keeps the conversation",
        why=(
            "The server is blocked on an answer. Starting over abandons a "
            "question that will go on being waited on until it times out, "
            "and the user has no way left to answer it."
        ),
        given=Given(chat_started=True, pending_ask=AskState(remaining=60)),
        when=(RELOAD,),
        then=lambda result: _outcome(
            result, "kept", "a waiting question was thrown away"
        ),
    ),
    Scenario(
        name="reloading while work is running keeps the conversation",
        why=(
            "The work is not the user's to lose. It was started, it may have "
            "been paid for, and it will post its results -- into a "
            "conversation that has to still be there when it does."
        ),
        given=Given(chat_started=True, running_task=True),
        when=(RELOAD,),
        then=lambda result: _outcome(
            result, "kept", "running work was cancelled by a reload"
        ),
    ),
    Scenario(
        name="reloading while a rescued answer is still in flight keeps the conversation",
        why=(
            "Nothing is running, but the session is holding the only copy of "
            "something the user typed -- an answer that arrived before the "
            "handshake finished."
        ),
        given=Given(chat_started=True, parked_reply=True),
        when=(RELOAD,),
        then=lambda result: _outcome(
            result, "kept", "an answer in flight was dropped by a reload"
        ),
    ),
    Scenario(
        name="an expired question no longer decides anything",
        why=(
            "Liveness used to decide whether a reload kept the conversation, "
            "so a question past its deadline meant 'start over'. Nothing "
            "reads it on this path now: the conversation is handed back "
            "whatever it is holding, and what is left of a dead question is "
            "the replay's business, not the claim's."
        ),
        given=Given(chat_started=True, pending_ask=AskState(remaining=None)),
        when=(RELOAD,),
        then=lambda result: _outcome(
            result, "kept", "an expired question lost the user their conversation"
        ),
    ),
    Scenario(
        name="a transport reconnect keeps the conversation and the screen",
        why=(
            "The page never went away -- the connection did. The user did not "
            "ask for anything, and their screen is still showing the "
            "conversation as it was, so the replay may skip the furniture "
            "the client never lost."
        ),
        given=Given(chat_started=True),
        when=(RECONNECT,),
        then=lambda result: (
            _outcome(result, "kept", "a dropped connection started a new conversation"),
            assert_that(
                not result.state["fresh_page_load"],
                "a reconnect was treated as though the screen had been lost",
            ),
        ),
    ),
    Scenario(
        name="a conversation nobody is in opens a new one",
        why=(
            "The ordinary first visit, and the reaper's aftermath: the thread "
            "the address bar names is free, so this connection begins a "
            "session in it. It has to be the same path either way, or the two "
            "diverge in exactly the state that is hardest to reproduce."
        ),
        given=Given(server_holds_session=False),
        when=(RELOAD,),
        then=lambda result: _outcome(
            result, "created", "a first connection did not open a conversation"
        ),
    ),
    Scenario(
        name="a conversation belonging to someone else is answered with a fresh one",
        why=(
            "The thread id is a bearer token in everything but name, so it "
            "is checked -- but the answer to a failed check is not a "
            "refusal. Saying 'that conversation is not yours' says that it "
            "exists. The stranger gets a thread of their own and hears "
            "nothing, which is also what a thread that never existed gets."
        ),
        given=Given(chat_started=True, owned_by_someone_else=True),
        when=(RELOAD,),
        then=lambda result: (
            _outcome(result, "created", "someone else's conversation was handed over"),
            assert_that(
                "s1" in result.state["live_sessions"],
                "the stranger's own session was disturbed by a guessed URL",
            ),
        ),
    ),
)
