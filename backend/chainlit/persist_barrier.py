"""Track background data-layer writes so thread reads can wait for them.

Step/message/element persistence is intentionally fire-and-forget: ``send()``
must not block the UI on the data layer. But a thread read that follows a
reconnect (the ``resume_thread`` snapshot, the transcript resync fallback,
``GET /project/thread``) replaces the client's feed wholesale — if it outruns
an in-flight ``create_step`` task, the freshest steps vanish from the feed
until the next reload. This module keeps a strong reference to every pending
persistence task per thread and lets readers wait (bounded) for them.

Holding the reference also fixes two latent hazards of bare
``asyncio.create_task``: tasks could be garbage-collected mid-flight, and
their exceptions were never retrieved (logged only at GC time, if ever).
"""

import asyncio
from typing import Any, Coroutine, Dict, Optional, Set

from chainlit.logger import logger

_pending_persists: Dict[str, Set[asyncio.Task]] = {}


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(f"Background persistence task failed: {exc!r}")


def create_persist_task(
    coro: Coroutine[Any, Any, Any], *, thread_id: Optional[str] = None
) -> Optional[asyncio.Task]:
    """Schedule a background persistence coroutine, tracked per thread.

    Drop-in replacement for ``asyncio.create_task`` at persistence call
    sites: it only raises what ``asyncio.create_task`` itself raises, so the
    surrounding ``fail_on_persist_error`` semantics are preserved. When
    ``thread_id`` is not given it is resolved from the current session's
    context; without any thread id the task still runs, just untracked.
    """
    if thread_id is None:
        try:
            from chainlit.context import context

            thread_id = context.session.thread_id
        except Exception:
            thread_id = None

    task = asyncio.create_task(coro)

    if thread_id:
        tracked_thread_id: str = thread_id
        _pending_persists.setdefault(tracked_thread_id, set()).add(task)

        def _on_done(t: asyncio.Task, thread_id: str = tracked_thread_id) -> None:
            bucket = _pending_persists.get(thread_id)
            if bucket is not None:
                bucket.discard(t)
                if not bucket:
                    _pending_persists.pop(thread_id, None)
            _log_task_exception(t)

        task.add_done_callback(_on_done)
    else:
        task.add_done_callback(_log_task_exception)

    return task


async def wait_for_persist(thread_id: Optional[str], timeout: float = 10.0) -> None:
    """Wait until the thread's pending persistence tasks have finished.

    A no-op when nothing is pending. Runs at most TWO rounds: round one
    awaits the tasks pending at entry; round two re-snapshots ONCE to catch
    tasks spawned by the awaited ones (the ``init_thread`` →
    ``flush_method_queue`` chain needs exactly one extra hop) and awaits
    only the new ones. Bounding the rounds keeps an actively-streaming
    thread — new create_step tasks every few hundred ms — from pinning the
    caller to the full deadline: the reader catches up with what was in
    flight and moves on. One overall deadline covers both rounds. Never
    raises: on timeout (or any internal error) a warning is logged and the
    caller proceeds with a possibly incomplete read — same behavior as
    before the barrier existed, just narrower odds.
    """
    if not thread_id:
        return
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        awaited: Set[asyncio.Task] = set()
        for _round in range(2):
            pending = {
                task
                for task in _pending_persists.get(thread_id, set())
                if not task.done() and task not in awaited
            }
            if not pending:
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning(
                    f"Timed out after {timeout}s waiting for {len(pending)} "
                    f"pending persistence task(s) of thread {thread_id}; "
                    f"reading anyway."
                )
                return
            awaited |= pending
            _done, not_done = await asyncio.wait(pending, timeout=remaining)
            if not_done:
                logger.warning(
                    f"Timed out after {timeout}s waiting for {len(not_done)} "
                    f"pending persistence task(s) of thread {thread_id}; "
                    f"reading anyway."
                )
                return
    except Exception:
        logger.warning(
            f"Failed waiting for pending persistence tasks of thread {thread_id}",
            exc_info=True,
        )
