"""What the project data routes enforce, against a real migrated database.

Nothing here mocks a service. The questions these routes raise — does the
cursor come back to the right row, does the upsert replace instead of adding,
can Bob read Alice's thread — are all questions about SQL, and a stubbed
service answers every one of them "yes" by construction.

Two things are stubbed, because they are not this module's:

* the **live websocket registry**, through the ``SessionRegistry`` protocol
  ``chainlit.controllers.project`` declares. The routes only ask it three
  things, and a dict answers all three;
* nothing else.

The authentication middleware is installed in every client, with two real
identities. An ownership test run in an app with no authentication passes
whether or not the ownership check exists, which makes it worse than no test:
it is a green light nailed to the wall.

``create_async_test_client``, not ``create_test_client``: the aiosqlite
engine belongs to the test's event loop, and the sync client runs the
application in another one. ``tests/persistence/test_dependencies.py`` made
the same choice for the same reason.
"""

import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Set

import pytest
import pytest_asyncio
from advanced_alchemy.extensions.litestar import SQLAlchemyInitPlugin
from litestar.di import Provide
from litestar.testing import create_async_test_client
from sqlalchemy.ext.asyncio import AsyncEngine

from chainlit.controllers.project import ProjectController
from chainlit.persistence import Persistence
from chainlit.persistence.records import (
    ElementRecord,
    FeedbackRecord,
    StepRecord,
    ThreadPatch,
)
from chainlit.security import chainlit_auth
from tests.persistence.conftest import database_url, engine

# Re-exported so pytest finds the persistence fixtures from this module.
__all__ = ["database_url", "engine"]

# Long enough that PyJWT does not warn, which this repo's -W settings would
# turn into an error.
TEST_SECRET = "a-test-secret-that-is-long-enough-for-hs256"

ALICE = "alice@example.com"
BOB = "bob@example.com"


class Identity:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier


class StubSession:
    """A ``LiveSession``: a user, and an action dispatcher."""

    def __init__(
        self, user: Optional[Identity], actions: Optional[Dict[str, Any]] = None
    ) -> None:
        self.user = user
        self.actions = actions or {}
        self.called: List[Dict[str, Any]] = []

    async def call_action(self, action: Any) -> Any:
        name = action.get("name")
        if name not in self.actions:
            raise LookupError(name)
        self.called.append(dict(action))
        return self.actions[name]


class StubRegistry:
    """A ``SessionRegistry``: the three questions the routes ask it."""

    def __init__(self, sessions: Optional[Dict[str, StubSession]] = None) -> None:
        self.sessions = sessions or {}
        self.live_threads: Set[str] = set()
        self.protected: Dict[str, Set[str]] = {}

    def find(self, session_id: str) -> Optional[StubSession]:
        return self.sessions.get(session_id)

    def has_live_task(self, thread_id: str) -> bool:
        return thread_id in self.live_threads

    def protected_step_ids(self, thread_id: str) -> Set[str]:
        return self.protected.get(thread_id, set())


@pytest_asyncio.fixture
async def persistence(engine: AsyncEngine) -> Persistence:
    """A migrated, empty PostgreSQL database, one per test.

    The ``engine`` fixture is the persistence suite's own -- there is one
    dialect now, and the controllers are exercised on it.
    """
    return Persistence.from_engine(engine)


@pytest.fixture
def auth():
    return chainlit_auth(token_secret=TEST_SECRET)


@pytest.fixture
def registry() -> StubRegistry:
    return StubRegistry(
        {
            "alice-session": StubSession(Identity(ALICE), {"greet": "hello alice"}),
            "bob-session": StubSession(Identity(BOB)),
        }
    )


@pytest_asyncio.fixture
async def client(
    persistence: Persistence, auth, registry: StubRegistry
) -> AsyncIterator[Any]:
    async with create_async_test_client(
        route_handlers=[ProjectController],
        plugins=[SQLAlchemyInitPlugin(config=persistence.config)],
        dependencies={
            **persistence.dependencies(),
            "sessions": Provide(lambda: registry, sync_to_thread=False),
            "persistence_enabled": Provide(lambda: True, sync_to_thread=False),
        },
        on_app_init=[auth.on_app_init],
    ) as test_client:
        yield test_client


def login(client: Any, auth: Any, identifier: str) -> None:
    client.cookies.set(auth.key, auth.create_token(identifier))


# --------------------------------------------------------------------------
# Seeding. Every write here goes through a session of its own and is
# committed, so a read inside the request proves the request read it.
# --------------------------------------------------------------------------


async def make_user(persistence: Persistence, identifier: str) -> str:
    async with persistence.uow() as uow:
        return (await uow.users.save(identifier)).id


async def make_thread(
    persistence: Persistence,
    *,
    owner: str,
    owner_id: Optional[str] = None,
    name: str = "a thread",
    metadata: Optional[Dict[str, Any]] = None,
    updated_at: Optional[str] = None,
) -> str:
    thread_id = str(uuid.uuid4())
    async with persistence.uow() as uow:
        await uow.threads.patch(
            thread_id,
            ThreadPatch(
                name=name,
                user_identifier=owner,
                user_id=owner_id,
                metadata=metadata if metadata is not None else {},
            ),
        )
        if updated_at is not None:
            from sqlalchemy import update

            from chainlit.persistence.models import THREADS
            from chainlit.persistence.services import to_datetime

            await uow.session.execute(
                update(THREADS)
                .where(THREADS.c["id"] == uuid.UUID(thread_id))
                .values({"updatedAt": to_datetime(updated_at)})
            )
    return thread_id


async def make_step(
    persistence: Persistence,
    thread_id: str,
    *,
    step_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    output: str = "",
) -> str:
    step_id = step_id or str(uuid.uuid4())
    async with persistence.uow() as uow:
        await uow.steps.save(
            StepRecord(
                id=step_id,
                type="assistant_message",
                thread_id=thread_id,
                name="assistant",
                parent_id=parent_id,
                output=output,
                metadata=metadata or {},
            )
        )
    return step_id


async def make_element(
    persistence: Persistence, thread_id: str, *, for_id: Optional[str] = None
) -> str:
    element_id = str(uuid.uuid4())
    async with persistence.uow() as uow:
        await uow.elements.save(
            ElementRecord(
                id=element_id,
                name="chart",
                type="custom",
                thread_id=thread_id,
                for_id=for_id,
                props={"a": 1},
            )
        )
    return element_id


async def stored_metadata(persistence: Persistence, thread_id: str) -> Dict[str, Any]:
    async with persistence.uow() as uow:
        thread = await uow.threads.fetch(thread_id)
    assert thread is not None
    return thread.metadata


async def feedback_rows(persistence: Persistence) -> List[Any]:
    from sqlalchemy import select

    from chainlit.persistence.models import FEEDBACKS

    async with persistence.uow() as uow:
        return list((await uow.session.execute(select(*FEEDBACKS.c))).all())


# --------------------------------------------------------------------------
# /health
# --------------------------------------------------------------------------


async def test_health_is_public_while_the_data_routes_are_not(client) -> None:
    """One app, no cookie, both answers.

    An orchestrator has no cookie. Asserted next to a guarded route, because
    "public" only means anything in an app where something else refuses.
    """
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    assert (await client.post("/project/threads", json={})).status_code == 401


async def test_translations_are_public(client) -> None:
    """The login page is rendered in them, and has no cookie yet."""
    response = await client.get("/project/translations?language=en-US")
    assert response.status_code == 200
    assert "translation" in response.json()


async def test_a_language_that_is_not_a_language_is_refused(client) -> None:
    """``language`` is interpolated into a filesystem path downstream."""
    response = await client.get("/project/translations?language=../../../etc/passwd")
    assert response.status_code == 400


# --------------------------------------------------------------------------
# The history page
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def history(persistence: Persistence) -> Dict[str, Any]:
    """Five of Alice's threads and one of Bob's, in a known order."""
    alice_id = await make_user(persistence, ALICE)
    bob_id = await make_user(persistence, BOB)
    threads = []
    for index in range(5):
        threads.append(
            await make_thread(
                persistence,
                owner=ALICE,
                owner_id=alice_id,
                name=f"alice {index}",
                updated_at=f"2026-08-2{index}T12:00:00.000000Z",
            )
        )
    bob_thread = await make_thread(
        persistence,
        owner=BOB,
        owner_id=bob_id,
        name="bob only",
        updated_at="2026-08-27T12:00:00.000000Z",
    )
    # Newest activity first is the order the route promises.
    return {
        "alice_id": alice_id,
        "bob_id": bob_id,
        "newest_first": list(reversed(threads)),
        "bob_thread": bob_thread,
    }


async def test_the_history_pages_forward_through_its_cursor(
    client, auth, history: Dict[str, Any]
) -> None:
    """A keyset page, and the cursor that continues it.

    Both halves matter. A page that comes back in the right order proves the
    ordering; only following ``endCursor`` proves the cursor *is* a position
    and not a decoration.
    """
    login(client, auth, ALICE)

    first = (await client.post("/project/threads", json={"first": 2})).json()
    assert [t["name"] for t in first["data"]] == ["alice 4", "alice 3"]
    assert first["pageInfo"]["hasNextPage"] is True

    second = (
        await client.post(
            "/project/threads",
            json={"first": 2, "cursor": first["pageInfo"]["endCursor"]},
        )
    ).json()
    assert [t["name"] for t in second["data"]] == ["alice 2", "alice 1"]

    last = (
        await client.post(
            "/project/threads",
            json={"first": 2, "cursor": second["pageInfo"]["endCursor"]},
        )
    ).json()
    assert [t["name"] for t in last["data"]] == ["alice 0"]
    assert last["pageInfo"]["hasNextPage"] is False


async def test_the_history_ignores_the_user_id_the_client_asks_for(
    client, auth, history: Dict[str, Any]
) -> None:
    """The one that matters. ``userId`` in the body is not an authorization.

    It used to be a filter the client set, so a client that set somebody
    else's id got somebody else's history back.
    """
    login(client, auth, ALICE)
    page = (
        await client.post(
            "/project/threads", json={"first": 20, "userId": history["bob_id"]}
        )
    ).json()

    names = [t["name"] for t in page["data"]]
    assert names == ["alice 4", "alice 3", "alice 2", "alice 1", "alice 0"]
    assert "bob only" not in names


async def test_a_page_size_outside_the_bounds_is_refused(client, auth, history) -> None:
    login(client, auth, ALICE)
    assert (await client.post("/project/threads", json={"first": 0})).status_code == 400
    assert (
        await client.post("/project/threads", json={"first": 1000})
    ).status_code == 400


async def test_a_caller_with_no_persisted_user_gets_a_404(client, auth) -> None:
    login(client, auth, "nobody@example.com")
    assert (await client.post("/project/threads", json={})).status_code == 404


# --------------------------------------------------------------------------
# Reading a thread, and reading a shared one
# --------------------------------------------------------------------------


async def test_a_thread_is_refused_to_anyone_but_its_author(
    client, auth, persistence: Persistence
) -> None:
    """The single most important assertion in this file.

    ``404``, not ``403``: a 403 tells whoever asks that the thread exists and
    is somebody else's, which is most of what an enumeration needs.
    """
    thread_id = await make_thread(persistence, owner=ALICE, name="alice's secret")

    login(client, auth, BOB)
    response = await client.get(f"/project/thread/{thread_id}")
    assert response.status_code == 404
    assert b"secret" not in response.content

    login(client, auth, ALICE)
    mine = await client.get(f"/project/thread/{thread_id}")
    assert mine.status_code == 200
    assert mine.json()["name"] == "alice's secret"


async def test_the_share_route_serves_what_the_thread_route_refuses(
    client, auth, persistence: Persistence
) -> None:
    """The deliberate exception, and the line it does not cross.

    A shared thread is readable by a stranger — that is the feature. An
    unshared one is not, and the refusal is the same 404, so a share link
    cannot be used to discover which threads exist.
    """
    shared = await make_thread(
        persistence, owner=ALICE, name="published", metadata={"is_shared": True}
    )
    private = await make_thread(persistence, owner=ALICE, name="not published")

    # No cookie at all: a share link that needs a login is not a share link.
    assert (await client.get(f"/project/thread/{shared}")).status_code == 401

    response = await client.get(f"/project/share/{shared}")
    assert response.status_code == 200
    assert response.json()["name"] == "published"

    assert (await client.get(f"/project/share/{private}")).status_code == 404


async def test_a_shared_thread_does_not_carry_the_sessions_own_metadata(
    client, persistence: Persistence
) -> None:
    """``env`` is the user's API keys. It has no business leaving the box."""
    thread_id = await make_thread(
        persistence,
        owner=ALICE,
        metadata={
            "is_shared": True,
            "env": {"OPENAI_API_KEY": "sk-do-not-leak"},
            "chat_profile": "internal",
            "chat_settings": {"temperature": 0},
            "topic": "public",
        },
    )

    response = await client.get(f"/project/share/{thread_id}")

    assert response.status_code == 200
    assert b"sk-do-not-leak" not in response.content
    assert response.json()["metadata"] == {"is_shared": True, "topic": "public"}


async def test_a_thread_read_hides_the_steps_a_resume_would_delete(
    client, auth, persistence: Persistence, registry: StubRegistry
) -> None:
    """Flagged steps, and their children, do not reach the client."""
    thread_id = await make_thread(persistence, owner=ALICE)
    kept = await make_step(persistence, thread_id, output="kept")
    doomed = await make_step(
        persistence, thread_id, metadata={"resume_policy": "delete"}, output="doomed"
    )
    child = await make_step(
        persistence, thread_id, parent_id=doomed, output="child of doomed"
    )
    await make_element(persistence, thread_id, for_id=doomed)

    login(client, auth, ALICE)
    body = (await client.get(f"/project/thread/{thread_id}")).json()

    assert [step["id"] for step in body["steps"]] == [kept]
    assert child not in [step["id"] for step in body["steps"]]
    assert body.get("elements", []) == []


async def test_a_step_a_live_ask_is_holding_stays_visible(
    client, auth, persistence: Persistence, registry: StubRegistry
) -> None:
    """The protection the seam exists for.

    Without it, a second tab reading the thread makes the message the first
    tab is still answering disappear from the feed.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    doomed = await make_step(
        persistence, thread_id, metadata={"resume_policy": "delete"}
    )
    registry.protected[thread_id] = {doomed}

    login(client, auth, ALICE)
    body = (await client.get(f"/project/thread/{thread_id}")).json()

    assert [step["id"] for step in body["steps"]] == [doomed]


async def test_a_thread_with_a_running_task_is_not_filtered_at_all(
    client, auth, persistence: Persistence, registry: StubRegistry
) -> None:
    thread_id = await make_thread(persistence, owner=ALICE)
    doomed = await make_step(
        persistence, thread_id, metadata={"resume_policy": "delete"}
    )
    registry.live_threads.add(thread_id)

    login(client, auth, ALICE)
    body = (await client.get(f"/project/thread/{thread_id}")).json()

    assert [step["id"] for step in body["steps"]] == [doomed]


# --------------------------------------------------------------------------
# Elements
# --------------------------------------------------------------------------


async def test_an_element_is_read_through_the_thread_it_belongs_to(
    client, auth, persistence: Persistence
) -> None:
    """The element id alone is not the key.

    The author check is about the *thread*, so a lookup by element id would
    authorise against one resource and then read another.
    """
    alice_thread = await make_thread(persistence, owner=ALICE)
    bob_thread = await make_thread(persistence, owner=BOB)
    element_id = await make_element(persistence, bob_thread)

    login(client, auth, ALICE)
    # Alice owns the thread in the path, but not the element in it.
    response = await client.get(f"/project/thread/{alice_thread}/element/{element_id}")
    assert response.status_code == 404

    login(client, auth, BOB)
    mine = await client.get(f"/project/thread/{bob_thread}/element/{element_id}")
    assert mine.status_code == 200
    assert mine.json()["id"] == element_id


async def test_an_element_of_another_users_thread_is_refused(
    client, auth, persistence: Persistence
) -> None:
    bob_thread = await make_thread(persistence, owner=BOB)
    element_id = await make_element(persistence, bob_thread)

    login(client, auth, ALICE)
    response = await client.get(f"/project/thread/{bob_thread}/element/{element_id}")
    assert response.status_code == 404
    assert b"chart" not in response.content


async def test_a_custom_element_is_written_back_through_its_session(
    client, auth, persistence: Persistence
) -> None:
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)

    login(client, auth, ALICE)
    response = await client.put(
        "/project/element",
        json={
            "sessionId": "alice-session",
            "element": {
                "id": element_id,
                "name": "chart",
                "type": "custom",
                "threadId": thread_id,
                "display": "inline",
                "props": {"a": 2},
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    async with persistence.uow() as uow:
        element = await uow.elements.fetch(thread_id, element_id)
    assert element is not None
    assert element.props == {"a": 2}


async def test_a_non_custom_element_is_not_written(
    client, auth, persistence: Persistence
) -> None:
    """Only ``custom`` is client-writable; the rest the app writes itself."""
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)

    login(client, auth, ALICE)
    response = await client.put(
        "/project/element",
        json={
            "sessionId": "alice-session",
            "element": {
                "id": element_id,
                "name": "chart",
                "type": "image",
                "props": {"a": 3},
            },
        },
    )

    assert response.json() == {"success": False}
    async with persistence.uow() as uow:
        element = await uow.elements.fetch(thread_id, element_id)
    assert element is not None
    assert element.props == {"a": 1}


async def test_an_element_write_into_another_users_session_is_refused(
    client, auth, persistence: Persistence
) -> None:
    thread_id = await make_thread(persistence, owner=BOB)
    element_id = await make_element(persistence, thread_id)

    login(client, auth, ALICE)
    response = await client.put(
        "/project/element",
        json={
            "sessionId": "bob-session",
            "element": {
                "id": element_id,
                "name": "chart",
                "type": "custom",
                "threadId": thread_id,
                "props": {"a": 99},
            },
        },
    )

    assert response.status_code == 401
    async with persistence.uow() as uow:
        element = await uow.elements.fetch(thread_id, element_id)
    assert element is not None
    assert element.props == {"a": 1}


async def test_an_element_write_is_refused_on_another_users_thread(
    client, auth, persistence: Persistence
) -> None:
    """A live session of one's own is not a licence over every element.

    Element ids are not secrets — ``/project/share`` hands them to strangers
    by design — so a check on the session alone lets Alice, from her own
    session, overwrite an element of Bob's thread.
    """
    bob_thread = await make_thread(persistence, owner=BOB)
    element_id = await make_element(persistence, bob_thread)

    login(client, auth, ALICE)
    response = await client.put(
        "/project/element",
        json={
            "sessionId": "alice-session",
            "element": {
                "id": element_id,
                "name": "chart",
                "type": "custom",
                # Not even claimed: the stored row is what decides.
                "props": {"a": 99},
            },
        },
    )

    assert response.status_code == 404
    async with persistence.uow() as uow:
        element = await uow.elements.fetch(bob_thread, element_id)
    assert element is not None
    assert element.props == {"a": 1}


async def test_an_element_delete_is_refused_on_another_users_thread(
    client, auth, persistence: Persistence
) -> None:
    """And the delete is the same hole, one step worse.

    Removing by id alone — which is what a payload with no ``threadId`` used
    to do — deletes anybody's element.
    """
    bob_thread = await make_thread(persistence, owner=BOB)
    element_id = await make_element(persistence, bob_thread)

    login(client, auth, ALICE)
    response = await client.request(
        "DELETE",
        "/project/element",
        json={
            "sessionId": "alice-session",
            "element": {"id": element_id, "name": "chart", "type": "custom"},
        },
    )

    assert response.status_code == 404
    async with persistence.uow() as uow:
        assert await uow.elements.fetch(bob_thread, element_id) is not None


async def test_an_element_payload_with_no_usable_id_is_a_400(
    client, auth, persistence: Persistence
) -> None:
    """A malformed payload is the client's mistake, not a 500."""
    login(client, auth, ALICE)
    response = await client.put(
        "/project/element",
        json={
            "sessionId": "alice-session",
            "element": {"name": "chart", "type": "custom"},
        },
    )
    assert response.status_code == 400


async def test_a_custom_element_is_removed(
    client, auth, persistence: Persistence
) -> None:
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)

    login(client, auth, ALICE)
    response = await client.request(
        "DELETE",
        "/project/element",
        json={
            "sessionId": "alice-session",
            "element": {
                "id": element_id,
                "name": "chart",
                "type": "custom",
                "threadId": thread_id,
            },
        },
    )

    # 200, not the 204 Litestar gives a DELETE: the route answers with a body.
    assert response.status_code == 200
    async with persistence.uow() as uow:
        assert await uow.elements.fetch(thread_id, element_id) is None


# --------------------------------------------------------------------------
# Feedback
# --------------------------------------------------------------------------


async def test_feedback_on_a_step_is_one_row_however_often_it_is_set(
    client, auth, persistence: Persistence
) -> None:
    """Migration 0003 made ``forId`` unique, so the second save replaces.

    The returned id is the row that *survived*, not the one the client
    proposed — a client that has lost the id would otherwise be told its new
    id was stored while the old row went on being what every reader joins to.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    step_id = await make_step(persistence, thread_id)

    login(client, auth, ALICE)
    first = await client.put(
        "/feedback",
        json={"sessionId": "alice-session", "feedback": {"forId": step_id, "value": 1}},
    )
    assert first.status_code == 200
    surviving = first.json()["feedbackId"]

    proposed = str(uuid.uuid4())
    second = await client.put(
        "/feedback",
        json={
            "sessionId": "alice-session",
            "feedback": {
                "id": proposed,
                "forId": step_id,
                "value": 0,
                "comment": "changed my mind",
            },
        },
    )

    assert second.status_code == 200
    assert second.json()["feedbackId"] == surviving
    assert second.json()["feedbackId"] != proposed

    rows = await feedback_rows(persistence)
    assert len(rows) == 1
    assert str(rows[0].id) == surviving
    assert rows[0].value == 0
    assert rows[0].comment == "changed my mind"


async def test_feedback_on_another_users_step_is_refused(
    client, auth, persistence: Persistence
) -> None:
    """A unique ``forId`` means writing is also overwriting.

    Without the author check any logged-in user could replace the thumbs on
    anybody's message, because the upsert does not add a second row.
    """
    thread_id = await make_thread(persistence, owner=BOB)
    step_id = await make_step(persistence, thread_id)

    login(client, auth, ALICE)
    response = await client.put(
        "/feedback",
        json={"sessionId": "alice-session", "feedback": {"forId": step_id, "value": 1}},
    )

    assert response.status_code == 404
    assert await feedback_rows(persistence) == []


async def test_feedback_on_a_step_that_does_not_exist_is_refused(
    client, auth, persistence: Persistence
) -> None:
    """The step is where the thread comes from: the column is NOT NULL."""
    login(client, auth, ALICE)
    response = await client.put(
        "/feedback",
        json={
            "sessionId": "alice-session",
            "feedback": {"forId": str(uuid.uuid4()), "value": 1},
        },
    )
    assert response.status_code == 404


async def test_feedback_is_deleted_by_its_author(
    client, auth, persistence: Persistence
) -> None:
    thread_id = await make_thread(persistence, owner=ALICE)
    step_id = await make_step(persistence, thread_id)
    async with persistence.uow() as uow:
        feedback_id = await uow.feedbacks.save(
            FeedbackRecord(for_id=step_id, thread_id=thread_id, value=1)
        )

    login(client, auth, ALICE)
    response = await client.request(
        "DELETE", "/feedback", json={"feedbackId": feedback_id}
    )

    assert response.status_code == 200
    assert await feedback_rows(persistence) == []


async def test_another_users_feedback_is_not_deletable(
    client, auth, persistence: Persistence
) -> None:
    thread_id = await make_thread(persistence, owner=BOB)
    step_id = await make_step(persistence, thread_id)
    async with persistence.uow() as uow:
        feedback_id = await uow.feedbacks.save(
            FeedbackRecord(for_id=step_id, thread_id=thread_id, value=1)
        )

    login(client, auth, ALICE)
    response = await client.request(
        "DELETE", "/feedback", json={"feedbackId": feedback_id}
    )

    assert response.status_code == 404
    assert len(await feedback_rows(persistence)) == 1


# --------------------------------------------------------------------------
# Renaming, sharing and deleting a thread
# --------------------------------------------------------------------------


async def test_a_thread_is_renamed_by_its_author_and_by_nobody_else(
    client, auth, persistence: Persistence
) -> None:
    thread_id = await make_thread(persistence, owner=ALICE, name="before")

    login(client, auth, BOB)
    refused = await client.put(
        "/project/thread", json={"threadId": thread_id, "name": "bob was here"}
    )
    assert refused.status_code == 404

    login(client, auth, ALICE)
    allowed = await client.put(
        "/project/thread", json={"threadId": thread_id, "name": "after"}
    )
    assert allowed.status_code == 200

    async with persistence.uow() as uow:
        thread = await uow.threads.fetch(thread_id)
    assert thread is not None
    assert thread.name == "after"


async def test_sharing_a_thread_sets_and_clears_the_flag(
    client, auth, persistence: Persistence
) -> None:
    """Merged into the stored metadata, never written over it.

    The topic below is what proves the merge: a read-modify-write in the
    handler would drop it, and two tabs toggling different keys would be a
    lost update.
    """
    thread_id = await make_thread(persistence, owner=ALICE, metadata={"topic": "keep"})

    login(client, auth, ALICE)
    await client.put(
        "/project/thread/share", json={"threadId": thread_id, "isShared": True}
    )
    shared = await stored_metadata(persistence, thread_id)
    assert shared["is_shared"] is True
    assert shared["shared_at"]
    assert shared["topic"] == "keep"

    await client.put(
        "/project/thread/share", json={"threadId": thread_id, "isShared": False}
    )
    withdrawn = await stored_metadata(persistence, thread_id)
    assert withdrawn["is_shared"] is False
    # ``None`` in a metadata patch deletes the key.
    assert "shared_at" not in withdrawn
    assert withdrawn["topic"] == "keep"

    assert (await client.get(f"/project/share/{thread_id}")).status_code == 404


async def test_a_thread_is_not_shareable_by_a_stranger(
    client, auth, persistence: Persistence
) -> None:
    """Otherwise anyone could publish anyone's conversation."""
    thread_id = await make_thread(persistence, owner=ALICE)

    login(client, auth, BOB)
    response = await client.put(
        "/project/thread/share", json={"threadId": thread_id, "isShared": True}
    )

    assert response.status_code == 404
    assert await stored_metadata(persistence, thread_id) == {}
    assert (await client.get(f"/project/share/{thread_id}")).status_code == 404


async def test_a_thread_is_deleted_by_its_author_and_by_nobody_else(
    client, auth, persistence: Persistence
) -> None:
    thread_id = await make_thread(persistence, owner=ALICE)
    await make_step(persistence, thread_id)

    login(client, auth, BOB)
    refused = await client.request(
        "DELETE", "/project/thread", json={"threadId": thread_id}
    )
    assert refused.status_code == 404
    async with persistence.uow() as uow:
        assert await uow.threads.fetch(thread_id) is not None

    login(client, auth, ALICE)
    allowed = await client.request(
        "DELETE", "/project/thread", json={"threadId": thread_id}
    )
    assert allowed.status_code == 200
    async with persistence.uow() as uow:
        assert await uow.threads.fetch(thread_id) is None


async def test_a_thread_id_that_is_not_a_uuid_never_reaches_a_handler(
    client, auth
) -> None:
    login(client, auth, ALICE)
    assert (await client.get("/project/thread/not-a-uuid")).status_code == 404


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------


async def test_an_action_runs_against_its_own_session(
    client, auth, registry: StubRegistry
) -> None:
    login(client, auth, ALICE)
    response = await client.post(
        "/project/action",
        json={"sessionId": "alice-session", "action": {"name": "greet", "id": "1"}},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "response": "hello alice"}
    assert registry.sessions["alice-session"].called[0]["name"] == "greet"


async def test_an_action_in_another_users_session_is_refused(
    client, auth, registry: StubRegistry
) -> None:
    login(client, auth, ALICE)
    response = await client.post(
        "/project/action",
        json={"sessionId": "bob-session", "action": {"name": "greet", "id": "1"}},
    )

    assert response.status_code == 401
    assert registry.sessions["bob-session"].called == []


async def test_an_action_with_no_callback_is_a_404(client, auth) -> None:
    login(client, auth, ALICE)
    response = await client.post(
        "/project/action",
        json={"sessionId": "alice-session", "action": {"name": "nope", "id": "1"}},
    )
    assert response.status_code == 404


async def test_an_action_in_a_session_that_is_gone_is_a_404(client, auth) -> None:
    login(client, auth, ALICE)
    response = await client.post(
        "/project/action",
        json={"sessionId": "vanished", "action": {"name": "greet", "id": "1"}},
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


async def test_the_settings_describe_the_running_app(client, auth) -> None:
    login(client, auth, ALICE)
    response = await client.get("/project/settings?language=en-US")

    assert response.status_code == 200
    body = response.json()
    assert body["dataPersistence"] is True
    assert set(body) >= {
        "ui",
        "features",
        "userEnv",
        "maskUserEnv",
        "dataPersistence",
        "threadResumable",
        "threadSharing",
        "markdown",
        "chatProfiles",
        "starters",
        "starterCategories",
    }


async def test_the_settings_need_a_login(client) -> None:
    assert (await client.get("/project/settings")).status_code == 401
