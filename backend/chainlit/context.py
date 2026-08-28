"""The per-coroutine context every ``cl.*`` call reads.

A context variable, because the application's callbacks run as tasks the
transport creates, and the session those tasks belong to has to travel with
them without being passed through every signature. Whoever launches a
callback sets it (see ``chainlit.runner``); nothing else may.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from chainlit.emitter import Emitter
    from chainlit.step import Step
    from chainlit.ws.session import Session

__all__ = [
    "CL_RUN_NAMES",
    "ChainlitContext",
    "ChainlitContextException",
    "context",
    "context_var",
    "get_context",
    "init_context",
    "local_steps",
]

CL_RUN_NAMES = ["on_chat_start", "on_message"]


class ChainlitContextException(Exception):
    def __init__(self, msg: str = "Chainlit context not found", *args: object):
        super().__init__(msg, *args)


class ChainlitContext:
    """One session and its emitter, as seen from inside a callback."""

    __slots__ = ("emitter", "loop", "session")

    def __init__(self, session: "Session", emitter: "Emitter") -> None:
        self.loop = asyncio.get_running_loop()
        self.session = session
        self.emitter = emitter

    @property
    def current_step(self) -> Optional["Step"]:
        if previous_steps := local_steps.get():
            return previous_steps[-1]
        return None

    @property
    def current_run(self) -> Optional["Step"]:
        if previous_steps := local_steps.get():
            return next(
                (step for step in previous_steps if step.name in CL_RUN_NAMES), None
            )
        return None


context_var: ContextVar[ChainlitContext] = ContextVar("chainlit")
local_steps: ContextVar[Optional[List["Step"]]] = ContextVar(
    "local_steps", default=None
)


def init_context(
    session: "Session", emitter: Optional["Emitter"] = None
) -> ChainlitContext:
    """Bind the current task to ``session``. Returns what it bound."""
    if emitter is None:
        from chainlit.emitter import Emitter

        emitter = Emitter(session)
    ctx = ChainlitContext(session, emitter)
    context_var.set(ctx)
    return ctx


def get_context() -> ChainlitContext:
    try:
        return context_var.get()
    except LookupError as error:
        raise ChainlitContextException from error


class _ContextProxy:
    """``cl.context`` -- resolves the variable on every attribute access.

    A proxy rather than a module-level object, because the object differs
    per task and a module-level name is bound once. Ten lines that replace a
    dependency.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> object:
        return getattr(get_context(), name)

    def __repr__(self) -> str:
        return "<chainlit.context>"


context: ChainlitContext = _ContextProxy()  # type: ignore[assignment]
