"""The composer's words, and the turn, as a reconnect owes them.

Both rows here are about a reload landing in the middle of something. The
composer's placeholder and hint used to be a translation and a profile
property, so a reload had nothing to give back and put the translation over a
field the application had just explained. And the spinner's frame used to
carry only half of what it says: ``accepting`` was left to its default, so a
reload in the middle of an ordinary turn opened the composer under it.
"""

from ..frames import Expect
from ..spec import Given, Incoming, Result, Scenario, assert_that

HELLO = Incoming("hello")


def _composer_frames(result: Result) -> list:
    return [frame for frame in result.ledger.frames if frame.tag == "composer.state"]


COMPOSER_SCENARIOS = (
    Scenario(
        name="a reconnect is given the composer's words back, after the panel",
        why=(
            "What the composer says is the application's account of what "
            "typing now does, and it changes inside one conversation. A reload "
            "that forgot it would put the translated placeholder back over a "
            "running job; stated after the panel and before the spinner, it is "
            "screen like the rest of the replay."
        ),
        given=Given(
            restored=True,
            chat_started=True,
            composer_placeholder="Add a correction",
            composer_hint="41 of 60 left",
        ),
        when=(HELLO,),
        expect=(
            Expect("sidebar.state"),
            Expect(
                "composer.state",
                {"placeholder": "Add a correction", "hint": "41 of 60 left"},
            ),
            Expect("task.indicator"),
        ),
        then=lambda result: assert_that(
            len(_composer_frames(result)) == 1,
            f"the composer was stated {len(_composer_frames(result))} times",
        ),
    ),
    Scenario(
        name="a default composer is still stated",
        why=(
            "A client that kept its atoms through a blip missed any clear the "
            "application sent while it was away. Only a frame saying "
            "'nothing' takes the old hint off the screen."
        ),
        given=Given(restored=True, chat_started=True),
        when=(HELLO,),
        expect=(Expect("composer.state"),),
        then=lambda result: assert_that(
            [frame.payload for frame in _composer_frames(result)] == [{}],
            f"the default composer was not a bare frame: {_composer_frames(result)}",
        ),
    ),
    Scenario(
        name="a reconnect in the middle of a turn keeps the composer shut",
        why=(
            "The spinner's frame carries two answers, and the replay used to "
            "give only one: an absent `accepting` decodes as true, so the "
            "reload of a page whose turn was still running opened the "
            "composer and every button in the feed under it."
        ),
        given=Given(restored=True, chat_started=True, running_task=True),
        when=(HELLO,),
        expect=(Expect("task.indicator", {"running": True, "accepting": False}),),
        # Every one of them, not just some: the application's resync after the
        # replay says it right, and a replay that said it wrong first is the
        # window in which the composer opened.
        then=lambda result: assert_that(
            all(
                frame.payload.get("accepting") is False
                for frame in result.ledger.frames
                if frame.tag == "task.indicator"
            ),
            f"a task.indicator opened the turn: {result.ledger.frames}",
        ),
    ),
)
