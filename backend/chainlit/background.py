"""Work that runs in the conversation without taking the person's turn.

A chat turn is normally the whole of what the session is doing: ``on_message``
is launched, the composer locks, the spinner lights, and the next thing the
person can do is stop it. That is right for an answer -- there is nothing
useful to type while one is being written -- and wrong for a run that takes
minutes and produces something the person can steer. The one consumer's
scenarios are the second kind: somebody watching a search go past has a
correction to make, and until now the only place to make it was the Stop
button.

So an application may say which it is. ``run_in_background`` hands the engine
a coroutine to run under the session's context, exactly as ``on_message``
runs, with one difference the wire carries: the work counts as *running* and
not as *the current turn*. The spinner stays lit, Stop stays where it is, the
composer stays open, and a message sent while it runs starts its own
``on_message`` beside it rather than waiting for it.

What it is not is a way to escape the session. The task is held by the
session, cancelled by Stop and cancelled by teardown, because a run nobody
can stop in a conversation nobody is in is the failure mode this fork has
already paid for once. Work that has to outlive the chat is the
application's, and comes back in through ``cl.deliver_to_thread``.

The two things a caller owes: the coroutine must tolerate cancellation, and
it must not assume it is alone -- ``on_message`` may now run while it does,
and anything both of them touch is shared.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable

from chainlit.context import context

__all__ = ["run_in_background"]


def run_in_background(coro: Awaitable[Any]) -> "asyncio.Task[Any]":
    """Run ``coro`` in this session without holding the composer shut.

    Call it from inside a session -- ``on_message``, ``on_chat_start``, an
    action callback -- and return; the work carries on under the same bound
    context, so ``cl.Message``, ``cl.Sidebar`` and the rest go on working
    inside it.

    The returned task is the session's, not the caller's: it is already held
    and already cleaned up after. Await it only if the caller genuinely has
    to, and know that awaiting it inside ``on_message`` puts the turn back in
    the foreground, which is the thing this call exists to avoid.

    An exception inside is logged and swallowed, like any other callback the
    engine launches; ``CancelledError`` is not.

    Args:
        coro (Awaitable[Any]): The application's work.

    Returns:
        asyncio.Task[Any]: The running task, held by the session.

    Raises:
        ChainlitContextException: called from outside a session.
    """
    session = context.session
    runner = session.runner
    if runner is None:
        raise RuntimeError(
            "This session has no runner, so there is nothing to launch the "
            "background task under."
        )
    return runner.launch_background(session, coro)
