import asyncio

import pytest

from chainlit.context import (
    ChainlitContext,
    ChainlitContextException,
    context,
    context_var,
    get_context,
    init_context,
)
from chainlit.emitter import Emitter


async def test_init_context_binds_the_task(session):
    bound = init_context(session)
    assert isinstance(bound, ChainlitContext)
    assert bound.session is session
    assert isinstance(bound.emitter, Emitter)
    assert bound.emitter.session is session
    assert get_context() is bound
    context_var.set(None)  # type: ignore[arg-type]


async def test_init_context_takes_a_given_emitter(session):
    emitter = Emitter(session)
    assert init_context(session, emitter).emitter is emitter
    context_var.set(None)  # type: ignore[arg-type]


async def test_get_context_without_a_binding_raises():
    async def probe():
        with pytest.raises(ChainlitContextException):
            get_context()

    # A fresh task inherits the current context; run in a copy with the
    # variable unset to model a callback launched from nowhere.
    import contextvars

    ctx = contextvars.Context()
    await asyncio.get_running_loop().create_task(probe(), context=ctx)


async def test_the_proxy_resolves_per_task(session_factory):
    one = session_factory(id="one")
    two = session_factory(id="two")

    async def bound_to(session):
        init_context(session)
        await asyncio.sleep(0)
        return context.session.id

    assert await asyncio.gather(
        asyncio.create_task(bound_to(one)), asyncio.create_task(bound_to(two))
    ) == ["one", "two"]


async def test_current_step_tracks_local_steps(session):
    from chainlit.context import local_steps
    from chainlit.step import Step

    bound = init_context(session)
    assert bound.current_step is None
    run = Step(name="on_message", type="run")
    inner = Step(name="tool", type="tool")
    token = local_steps.set([run, inner])
    try:
        assert bound.current_step is inner
        assert bound.current_run is run
    finally:
        local_steps.reset(token)
        context_var.set(None)  # type: ignore[arg-type]
