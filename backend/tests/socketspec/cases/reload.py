"""What a page reload means for the conversation it lands in.

Reloading has always meant "start over" here, and it has to keep meaning
that: the user who reloads a finished conversation expects a clean one, not
the old one handed back. But the id the client offers is the same either way,
and by the time it arrives the server may be in the middle of something the
user is still owed -- a question waiting for an answer, work that was paid
for, an answer typed just before the reload that has not been filed yet.

So the arrival is a decision, not a lookup: keep what is running, and only
start over when nothing is. The rows below are that decision, plus the two
things the client is told afterwards -- whether it lost its screen, and
whether it is allowed near this conversation at all.
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
        name="reloading an idle conversation starts a new one",
        why=(
            "This is what reloading has always meant. Handing the old "
            "conversation back would make the one gesture everyone uses to "
            "start over stop working."
        ),
        given=Given(chat_started=True),
        when=(RELOAD,),
        then=lambda result: _outcome(
            result, "replaced", "an idle conversation survived a reload"
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
        then=lambda result: (
            _outcome(result, "kept", "a waiting question was thrown away"),
            assert_that(
                result.state["fresh_page_load"],
                "the client kept its screen across a page load",
            ),
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
            "handshake finished. Starting over is the one path where that "
            "input is genuinely lost."
        ),
        given=Given(chat_started=True, parked_reply=True),
        when=(RELOAD,),
        then=lambda result: _outcome(
            result, "kept", "an answer in flight was dropped by a reload"
        ),
    ),
    Scenario(
        name="a question whose deadline has passed does not hold the conversation open",
        why=(
            "Liveness is the deadline, not the presence of a question. A "
            "question nobody can still answer would otherwise keep every "
            "reload returning the same dead conversation."
        ),
        given=Given(chat_started=True, pending_ask=AskState(remaining=None)),
        when=(RELOAD,),
        then=lambda result: _outcome(
            result, "replaced", "an expired question kept the conversation alive"
        ),
    ),
    Scenario(
        name="a transport reconnect never starts a new conversation",
        why=(
            "The page never went away -- the connection did. The user did not "
            "ask for anything, so nothing may be taken from them, and their "
            "screen is still showing the conversation as it was."
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
        name="an id the server has never seen opens a new conversation",
        why=(
            "The ordinary first visit. It has to be the same path as a "
            "reload that found nothing worth keeping, or the two diverge in "
            "exactly the state that is hardest to reproduce."
        ),
        given=Given(server_holds_session=False),
        when=(RELOAD,),
        then=lambda result: _outcome(
            result, "created", "a first connection did not open a conversation"
        ),
    ),
    Scenario(
        name="a conversation belonging to someone else is refused",
        why=(
            "The id is a bearer token in everything but name. Without the "
            "ownership check, guessing one hands over another person's "
            "conversation -- and reconnecting to it is enough to read it."
        ),
        given=Given(chat_started=True, owned_by_someone_else=True),
        when=(RELOAD,),
        then=lambda result: _outcome(
            result, "refused", "someone else's conversation was handed over"
        ),
    ),
)
