"""What a route handler gets when it names a service.

These run a real Litestar app over the migrated database rather than calling
the providers directly: the questions worth asking here — is the injected
session the request's, is it committed on the way out, is it rolled back when
the handler fails — are all questions about the app, and none of them can be
answered by inspecting a ``Provide``.

Every read-back goes through a *second*, standalone session. A read inside
the request's own session passes whether or not anything was committed, so it
would assert nothing about the thing under test.

Handlers are annotated ``FromPath``/``NamedDependency`` rather than left
to inference: 2.24 deprecates the inferred form and 3.0 drops it, and a
handler written here is the shape phase 5 copies.
"""

import uuid
from typing import Optional

from advanced_alchemy.extensions.litestar import SQLAlchemyInitPlugin
from litestar import post
from litestar.di import NamedDependency
from litestar.params import FromPath
from litestar.testing import create_async_test_client

from chainlit.persistence import Persistence, StepService, ThreadPatch, ThreadService


async def name_of(persistence: Persistence, thread_id: str) -> Optional[str]:
    """Read the thread back through a session of our own."""
    async with persistence.uow() as uow:
        thread = await uow.threads.fetch(thread_id)
    return None if thread is None else thread.name


async def test_a_handler_names_the_one_service_it_needs(
    persistence: Persistence,
) -> None:
    @post("/threads/{thread_id:uuid}")
    async def rename(
        thread_id: FromPath[uuid.UUID], threads: NamedDependency[ThreadService]
    ) -> None:
        await threads.patch(str(thread_id), ThreadPatch(name="named by the handler"))

    thread_id = str(uuid.uuid4())
    async with create_async_test_client(
        route_handlers=[rename],
        plugins=[SQLAlchemyInitPlugin(config=persistence.config)],
        dependencies=persistence.dependencies(),
    ) as client:
        response = await client.post(f"/threads/{thread_id}")

    assert response.status_code == 201
    assert await name_of(persistence, thread_id) == "named by the handler"


async def test_a_write_that_is_never_committed_is_not_a_write(
    persistence: Persistence,
) -> None:
    """The response decides. A handler that fails leaves nothing behind.

    Without the autocommit before-send handler the request session is closed
    and not committed, and this test's twin above fails; with a plain commit
    it is this one that fails. Both are needed to pin the choice.
    """

    @post("/threads/{thread_id:uuid}")
    async def rename_then_fail(
        thread_id: FromPath[uuid.UUID], threads: NamedDependency[ThreadService]
    ) -> None:
        await threads.patch(str(thread_id), ThreadPatch(name="never happened"))
        raise RuntimeError("the handler failed after writing")

    thread_id = str(uuid.uuid4())
    async with create_async_test_client(
        route_handlers=[rename_then_fail],
        plugins=[SQLAlchemyInitPlugin(config=persistence.config)],
        dependencies=persistence.dependencies(),
        raise_server_exceptions=False,
    ) as client:
        response = await client.post(f"/threads/{thread_id}")

    assert response.status_code == 500
    assert await name_of(persistence, thread_id) is None


async def test_two_services_in_one_handler_are_one_transaction(
    persistence: Persistence,
) -> None:
    """The unit of work is the request, not an object passed around.

    Both services are handed the same session, so a handler that touches two
    tables commits them together or not at all — which is what the
    ``UnitOfWork`` exists to give the callers that have no request.
    """
    shared: dict[str, bool] = {}

    @post("/threads/{thread_id:uuid}")
    async def touch_two(
        thread_id: FromPath[uuid.UUID],
        threads: NamedDependency[ThreadService],
        steps: NamedDependency[StepService],
    ) -> None:
        shared["same_session"] = threads.repository.session is steps.repository.session
        await threads.patch(str(thread_id), ThreadPatch(name="two services"))

    thread_id = str(uuid.uuid4())
    async with create_async_test_client(
        route_handlers=[touch_two],
        plugins=[SQLAlchemyInitPlugin(config=persistence.config)],
        dependencies=persistence.dependencies(),
    ) as client:
        response = await client.post(f"/threads/{thread_id}")

    assert response.status_code == 201
    assert shared["same_session"] is True
    assert await name_of(persistence, thread_id) == "two services"
