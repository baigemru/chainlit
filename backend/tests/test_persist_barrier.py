import asyncio
from unittest.mock import patch

import pytest

from chainlit.persist_barrier import (
    _pending_persists,
    create_persist_task,
    wait_for_persist,
)


@pytest.fixture(autouse=True)
def clean_registry():
    _pending_persists.clear()
    yield
    _pending_persists.clear()


class TestCreatePersistTask:
    @pytest.mark.asyncio
    async def test_tracks_task_and_cleans_registry(self):
        release = asyncio.Event()

        async def work():
            await release.wait()

        task = create_persist_task(work(), thread_id="t1")

        assert task in _pending_persists["t1"]

        release.set()
        await task
        await asyncio.sleep(0)  # let the done callback run

        assert "t1" not in _pending_persists

    @pytest.mark.asyncio
    async def test_failed_task_exception_is_retrieved_and_logged(self):
        async def boom():
            raise RuntimeError("db down")

        with patch("chainlit.persist_barrier.logger") as mock_logger:
            create_persist_task(boom(), thread_id="t1")
            # Never awaited by anyone: the done callback must retrieve the
            # exception (no "exception was never retrieved") and log it.
            await asyncio.sleep(0.01)

        assert mock_logger.warning.called
        assert "t1" not in _pending_persists

    @pytest.mark.asyncio
    async def test_untracked_without_thread_id_outside_context(self):
        async def work():
            pass

        task = create_persist_task(work())

        assert _pending_persists == {}
        await task

    @pytest.mark.asyncio
    async def test_thread_id_resolved_from_context(self, mock_chainlit_context):
        async with mock_chainlit_context:

            async def work():
                pass

            task = create_persist_task(work())

            assert task in _pending_persists["test_thread_id"]
            await task

    @pytest.mark.asyncio
    async def test_cancelled_task_does_not_warn(self):
        async def work():
            await asyncio.sleep(60)

        task = create_persist_task(work(), thread_id="t1")
        with patch("chainlit.persist_barrier.logger") as mock_logger:
            task.cancel()
            await asyncio.sleep(0.01)

        assert not mock_logger.warning.called
        assert "t1" not in _pending_persists


class TestWaitForPersist:
    @pytest.mark.asyncio
    async def test_noop_without_thread_id(self):
        await wait_for_persist(None)
        await wait_for_persist("")

    @pytest.mark.asyncio
    async def test_noop_without_pending_tasks(self):
        await wait_for_persist("nothing_pending")

    @pytest.mark.asyncio
    async def test_waits_for_pending_task(self):
        done = {"v": False}

        async def slow():
            await asyncio.sleep(0.05)
            done["v"] = True

        create_persist_task(slow(), thread_id="t1")

        await wait_for_persist("t1")

        assert done["v"] is True

    @pytest.mark.asyncio
    async def test_picks_up_tasks_spawned_by_awaited_ones(self):
        """Round two catches the child a round-one task scheduled — the
        init_thread -> flush_method_queue chain needs exactly this hop."""
        order = []

        async def second():
            await asyncio.sleep(0.02)
            order.append("second")

        async def first():
            await asyncio.sleep(0.02)
            create_persist_task(second(), thread_id="t1")
            order.append("first")

        create_persist_task(first(), thread_id="t1")

        await wait_for_persist("t1")

        assert order == ["first", "second"]

    @pytest.mark.asyncio
    async def test_bounded_rounds_return_on_continuously_spawning_thread(self):
        """An actively-streaming thread keeps scheduling new persist tasks;
        the two-round bound must let the reader catch up and move on
        instead of riding the full deadline."""
        stop = {"v": False}

        def spawn():
            async def gen():
                await asyncio.sleep(0.01)
                if not stop["v"]:
                    spawn()

            create_persist_task(gen(), thread_id="t1")

        spawn()
        loop = asyncio.get_running_loop()
        start = loop.time()
        try:
            await wait_for_persist("t1", timeout=5.0)
            elapsed = loop.time() - start
        finally:
            stop["v"] = True
        # Two generations of ~10ms tasks, not the 5s deadline.
        assert elapsed < 1.0
        # Let the chain die before the registry is cleared.
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_timeout_logs_warning_and_returns(self):
        release = asyncio.Event()

        async def stuck():
            await release.wait()

        task = create_persist_task(stuck(), thread_id="t1")

        with patch("chainlit.persist_barrier.logger") as mock_logger:
            # Must return (no raise) despite the still-pending task.
            await wait_for_persist("t1", timeout=0.05)

        assert mock_logger.warning.called
        assert not task.done()
        release.set()
        await task
