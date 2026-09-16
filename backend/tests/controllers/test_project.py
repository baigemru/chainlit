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

import chainlit.config
from chainlit.controllers.project import ProjectController
from chainlit.persistence import Persistence
from chainlit.persistence.records import (
    ElementRecord,
    FeedbackRecord,
    StepRecord,
    ThreadPatch,
)
from chainlit.persistence.storage.base import BaseStorageClient
from chainlit.persistence.writer import SaveElement, SessionWriter, WriterRegistry
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
    """A ``LiveSession``: a user, an action dispatcher, and an ending.

    Plus the two element members the write-back route reads. ``held`` is what
    the real session's transcript and panel are, flattened to the one question
    the route asks -- and ``remembered`` is what it is here to prove, because
    the copy the reconnect replays is the half a row cannot stand in for.
    """

    def __init__(
        self,
        user: Optional[Identity],
        actions: Optional[Dict[str, Any]] = None,
        *,
        thread_id: Optional[str] = None,
        writer: Optional[Any] = None,
        held: Optional[Set[str]] = None,
    ) -> None:
        self.user = user
        self.actions = actions or {}
        self.called: List[Dict[str, Any]] = []
        self.releases = 0
        self.thread_id = thread_id
        self.writer = writer
        self.held: Set[str] = held or set()
        self.remembered: List[Any] = []

    async def call_action(self, action: Any) -> Any:
        name = action.get("name")
        if name not in self.actions:
            raise LookupError(name)
        self.called.append(dict(action))
        return self.actions[name]

    async def release(self) -> None:
        self.releases += 1

    def remember_element(self, payload: Any) -> None:
        self.remembered.append(payload)

    def holds_element(self, element_id: str) -> bool:
        return element_id in self.held


class StubRegistry:
    """A ``SessionRegistry``: the four questions the routes ask it."""

    def __init__(self, sessions: Optional[Dict[str, StubSession]] = None) -> None:
        self.sessions = sessions or {}
        self.threads: Dict[str, StubSession] = {}
        self.live_threads: Set[str] = set()
        self.protected: Dict[str, Set[str]] = {}

    def find(self, session_id: str) -> Optional[StubSession]:
        return self.sessions.get(session_id)

    def find_thread(self, thread_id: Optional[str]) -> Optional[StubSession]:
        return self.threads.get(str(thread_id))

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


class RecordingStorage(BaseStorageClient):
    """A store that logs its deletes, and can be told to fall over.

    Only ``delete_file`` is exercised here -- these routes never upload -- and
    it raises rather than returning ``False`` when it is told to refuse: the
    real clients swallow their own errors, so an exception is the failure
    this layer has to be the one to absorb.
    """

    def __init__(self) -> None:
        self.deleted: List[str] = []
        self.broken = False

    async def delete_file(self, object_key: str) -> bool:
        self.deleted.append(object_key)
        if self.broken:
            raise OSError("bucket unreachable")
        return True

    async def upload_file(
        self,
        object_key: str,
        data: Any,
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {"object_key": object_key, "url": f"memory://{object_key}"}

    async def get_read_url(self, object_key: str) -> str:
        return f"memory://{object_key}"

    async def read_file(self, object_key: str) -> Optional[bytes]:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
def storage() -> RecordingStorage:
    return RecordingStorage()


@pytest_asyncio.fixture
async def writer_for(persistence: Persistence, registry: StubRegistry) -> Any:
    """Give a stub session a real writer, and close it when the test ends.

    A real one rather than a recorder: the route's whole point is that its
    row goes into the same queue a slot's element goes into, and a double
    would order that queue by agreeing with itself.
    """
    created: List[SessionWriter] = []

    def attach(
        thread_id: str, *, hold: bool = False, session: str = "alice-session"
    ) -> SessionWriter:
        writer = SessionWriter(
            persistence,
            thread_id,
            registry=WriterRegistry(),
            hold_until_interaction=hold,
        ).start()
        stub = registry.sessions[session]
        stub.thread_id = thread_id
        stub.writer = writer
        created.append(writer)
        return writer

    yield attach
    for writer in created:
        await writer.aclose(timeout=5.0)


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
    persistence: Persistence, auth, registry: StubRegistry, storage: RecordingStorage
) -> AsyncIterator[Any]:
    async with create_async_test_client(
        route_handlers=[ProjectController],
        plugins=[SQLAlchemyInitPlugin(config=persistence.config)],
        dependencies={
            **persistence.dependencies(),
            "sessions": Provide(lambda: registry, sync_to_thread=False),
            "persistence_enabled": Provide(lambda: True, sync_to_thread=False),
            "storage": Provide(lambda: storage, sync_to_thread=False),
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
    persistence: Persistence,
    thread_id: str,
    *,
    for_id: Optional[str] = None,
    object_key: Optional[str] = None,
    url: Optional[str] = None,
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
                object_key=object_key,
                url=url,
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


async def test_translations_are_read_off_disk_once_per_language(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second identical request is answered from the response cache.

    The cache key is the path plus the sorted query, so a different
    ``?language=`` is a different entry and reads its own file -- asserted
    too, because a cache that ignored the query would serve English to
    everyone after the first request.
    """
    # Patched on the class: the config is a pydantic model and refuses an
    # instance attribute it has no field for.
    cls = type(chainlit.config.config)
    original = cls.load_translation
    calls: List[str] = []

    def counting(self: Any, language: str) -> Dict[str, Any]:
        calls.append(language)
        return original(self, language)

    monkeypatch.setattr(cls, "load_translation", counting)

    first = await client.get("/project/translations?language=en-US")
    second = await client.get("/project/translations?language=en-US")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1

    assert (await client.get("/project/translations?language=fr-FR")).status_code == 200
    assert len(calls) == 2


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


async def test_a_shared_thread_hands_out_share_urls_for_its_blobs(
    client, persistence: Persistence
) -> None:
    """The read of the thread and the read of its files have to agree.

    ``row_to_element`` writes the *author's* file url onto every row with an
    object behind it, because everywhere else the reader is the author. A
    stranger following that url gets the 404 it owes every stranger, so a
    shared page used to arrive with every persisted image broken.

    The element that owns its url keeps it: the rewrite recognises what the
    substitution wrote, not merely "has a url".
    """
    thread_id = await make_thread(
        persistence, owner=ALICE, metadata={"is_shared": True}
    )
    stored = await make_element(persistence, thread_id, object_key="alice/chart.png")
    external = await make_element(
        persistence, thread_id, url="https://example.com/cat.png"
    )

    response = await client.get(f"/project/share/{thread_id}")

    assert response.status_code == 200
    urls = {element["id"]: element["url"] for element in response.json()["elements"]}
    assert urls[stored] == f"/project/share/{thread_id}/element/{stored}/file"
    assert urls[external] == "https://example.com/cat.png"


async def test_the_author_still_gets_the_author_url(
    client, auth, persistence: Persistence
) -> None:
    """The rewrite belongs to the share handler, not to the row reader.

    The author's own read is unchanged even on a published thread: their
    browser carries the cookie the author route wants, and serving them the
    public form would make every reload of their own thread depend on a flag
    they are free to turn off.
    """
    thread_id = await make_thread(
        persistence, owner=ALICE, metadata={"is_shared": True}
    )
    element_id = await make_element(
        persistence, thread_id, object_key="alice/chart.png"
    )
    login(client, auth, ALICE)

    response = await client.get(f"/project/thread/{thread_id}")

    assert response.status_code == 200
    urls = [element["url"] for element in response.json()["elements"]]
    assert urls == [f"/project/thread/{thread_id}/element/{element_id}/file"]


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


def element_payload(element_id: str, thread_id: Optional[str], **fields: Any) -> Any:
    element: Dict[str, Any] = {
        "id": element_id,
        "name": "chart",
        "type": "custom",
        "display": "inline",
        "props": {"a": 2},
        **fields,
    }
    if thread_id is not None:
        element["threadId"] = thread_id
    return {"sessionId": "alice-session", "element": element}


async def props_of(persistence: Persistence, thread_id: str, element_id: str) -> Any:
    async with persistence.uow() as uow:
        element = await uow.elements.fetch(thread_id, element_id)
    return None if element is None else element.props


async def test_a_custom_element_is_written_back_through_its_session(
    client, auth, persistence: Persistence, registry: StubRegistry, writer_for
) -> None:
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)
    writer = writer_for(thread_id)

    login(client, auth, ALICE)
    response = await client.put(
        "/project/element", json=element_payload(element_id, thread_id)
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    await writer.drain(timeout=5.0)
    assert await props_of(persistence, thread_id, element_id) == {"a": 2}


async def test_a_custom_element_write_back_refreshes_the_session_copy(
    client, auth, persistence: Persistence, registry: StubRegistry, writer_for
) -> None:
    """The row is half the write; the screen is the other half.

    Only the row was ever written, so a reload replayed the transcript and
    the panel the session was still holding -- the props the element had
    before the user changed them.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)
    writer_for(thread_id)

    login(client, auth, ALICE)
    await client.put("/project/element", json=element_payload(element_id, thread_id))

    remembered = registry.sessions["alice-session"].remembered
    assert [(e.id, e.props) for e in remembered] == [(element_id, {"a": 2})]


async def test_a_write_back_waits_behind_what_the_session_already_queued(
    client, auth, persistence: Persistence, writer_for
) -> None:
    """The row goes into the session's queue, not around it.

    A slot's element is queued through the writer and may not be filed for a
    while. A write-back that went straight to the service landed *before* it
    and was then overwritten by it: the user saw fresh props, and the next
    resume showed the stale ones.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)
    writer = writer_for(thread_id, hold=True)
    writer.submit_element(
        ElementRecord(
            id=element_id,
            name="chart",
            type="custom",
            thread_id=thread_id,
            props={"stale": True},
        )
    )

    login(client, auth, ALICE)
    assert (
        await client.put(
            "/project/element", json=element_payload(element_id, thread_id)
        )
    ).status_code == 200

    writer.open_gate()
    await writer.drain(timeout=5.0)
    assert await props_of(persistence, thread_id, element_id) == {"a": 2}


async def test_a_write_back_is_held_until_the_threads_first_interaction(
    client, auth, persistence: Persistence, writer_for
) -> None:
    """Held, not lost: the gate is the writer's, and this row obeys it."""
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)
    writer = writer_for(thread_id, hold=True)

    login(client, auth, ALICE)
    await client.put("/project/element", json=element_payload(element_id, thread_id))

    assert [type(op) for op in writer.held] == [SaveElement]
    assert await props_of(persistence, thread_id, element_id) == {"a": 1}

    writer.open_gate()
    await writer.drain(timeout=5.0)
    assert await props_of(persistence, thread_id, element_id) == {"a": 2}


async def test_an_element_the_session_holds_is_writable_before_it_has_a_row(
    client, auth, persistence: Persistence, registry: StubRegistry, writer_for
) -> None:
    """A panel element saves its props before the thread row exists.

    Nothing is in the database yet -- not the element, not the thread: the
    writer holds every row until the conversation's first interaction. The
    session showing the element is what authorises the write, and without
    that fallback a card in a fresh session was answered with a 404 for as
    long as the gate stayed shut.
    """
    thread_id = str(uuid.uuid4())
    element_id = str(uuid.uuid4())
    writer_for(thread_id, hold=True)
    registry.sessions["alice-session"].held = {element_id}

    login(client, auth, ALICE)
    response = await client.put(
        "/project/element", json=element_payload(element_id, thread_id)
    )

    assert response.status_code == 200
    assert [e.id for e in registry.sessions["alice-session"].remembered] == [element_id]


async def test_a_held_element_claiming_another_thread_is_still_refused(
    client, auth, persistence: Persistence, registry: StubRegistry, writer_for
) -> None:
    """Holding an id is not a licence over the thread the payload names.

    The same hole ``authorize_element`` closes, approached from the other
    side: Alice's session holds an id, the payload claims Bob's thread, and
    the row would be written into it.
    """
    bob_thread = await make_thread(persistence, owner=BOB)
    element_id = await make_element(persistence, bob_thread)
    writer_for(str(uuid.uuid4()))
    registry.sessions["alice-session"].held = {element_id}

    login(client, auth, ALICE)
    response = await client.put(
        "/project/element", json=element_payload(element_id, bob_thread)
    )

    assert response.status_code == 404
    assert await props_of(persistence, bob_thread, element_id) == {"a": 1}


async def test_a_session_with_no_writer_still_refreshes_its_copy(
    client, auth, persistence: Persistence, registry: StubRegistry
) -> None:
    """No writer is no database: the screen is the whole of the write.

    Deliberate, and the reason the route no longer writes through the
    element service: a row filed outside the session's queue is a row that
    can be overtaken by it.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)

    login(client, auth, ALICE)
    response = await client.put(
        "/project/element", json=element_payload(element_id, thread_id)
    )

    assert response.status_code == 200
    assert [e.id for e in registry.sessions["alice-session"].remembered] == [element_id]
    assert await props_of(persistence, thread_id, element_id) == {"a": 1}


async def test_an_element_with_a_non_uuid_id_is_refused_before_the_writer(
    client, auth, persistence: Persistence, registry: StubRegistry, writer_for
) -> None:
    """``elements.id`` is a uuid column, and the writer batches.

    A non-uuid id reaching the queue fails the whole batch it lands in --
    somebody else's rows with it -- so it is refused here, even for an
    element the session is holding.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    writer = writer_for(thread_id, hold=True)
    registry.sessions["alice-session"].held = {"chart1"}

    login(client, auth, ALICE)
    response = await client.put(
        "/project/element", json=element_payload("chart1", thread_id)
    )

    assert response.status_code == 400
    assert writer.held == ()


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

    assert response.status_code == 404
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


async def delete_element(client: Any, thread_id: str, element_id: str) -> Any:
    return await client.request(
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


async def test_removing_an_element_takes_its_blob_with_it(
    client, auth, persistence: Persistence, storage: RecordingStorage
) -> None:
    """The row was the only thing that knew where the bytes were."""
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(
        persistence, thread_id, object_key="alice/chart.png"
    )
    login(client, auth, ALICE)

    response = await delete_element(client, thread_id, element_id)

    assert response.status_code == 200
    assert storage.deleted == ["alice/chart.png"]


async def test_removing_an_element_that_never_had_a_blob_asks_for_nothing(
    client, auth, persistence: Persistence, storage: RecordingStorage
) -> None:
    """``cl.Image(url=...)`` keeps its own url and uploads nothing.

    Asserted on the delete log rather than on the status: a store handed an
    empty key answers the same 200, one round trip and one warning later.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)
    login(client, auth, ALICE)

    response = await delete_element(client, thread_id, element_id)

    assert response.status_code == 200
    assert storage.deleted == []


async def test_a_store_that_refuses_does_not_fail_the_element_delete(
    client, auth, persistence: Persistence, storage: RecordingStorage
) -> None:
    """The row is gone either way; the leak is logged, not raised.

    A 500 here would leave the client believing the element it can no longer
    see is still there, and re-deleting it would not help.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(
        persistence, thread_id, object_key="alice/chart.png"
    )
    storage.broken = True
    login(client, auth, ALICE)

    response = await delete_element(client, thread_id, element_id)

    assert response.status_code == 200
    assert storage.deleted == ["alice/chart.png"]
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


async def test_deleting_a_thread_releases_the_session_living_in_it(
    client, auth, persistence: Persistence, registry: StubRegistry
) -> None:
    """A deleted conversation must not leave a session running in it.

    The session went on holding the thread, reachable by its handle, and
    its next message re-created the row the user had just thrown away. It
    is released before the rows go, in that order: the teardown drains a
    writer that may still have something to file, and rows filed after the
    delete would be a conversation coming back from the dead.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    living = StubSession(Identity(ALICE))
    registry.threads[thread_id] = living
    login(client, auth, ALICE)

    response = await client.request(
        "DELETE", "/project/thread", json={"threadId": thread_id}
    )

    assert response.status_code == 200
    assert living.releases == 1
    async with persistence.uow() as uow:
        assert await uow.threads.fetch(thread_id) is None


async def test_deleting_somebody_elses_thread_releases_nothing(
    client, auth, persistence: Persistence, registry: StubRegistry
) -> None:
    """The refusal comes first, so a guessed id cannot end a live session.

    Authorship is checked before the registry is touched at all; without
    that order a 404 would still have torn the owner's conversation down on
    the way to answering it.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    hers = StubSession(Identity(ALICE))
    registry.threads[thread_id] = hers
    login(client, auth, BOB)

    response = await client.request(
        "DELETE", "/project/thread", json={"threadId": thread_id}
    )

    assert response.status_code == 404
    assert hers.releases == 0


async def test_deleting_a_thread_empties_its_shelf_in_the_bucket(
    client, auth, persistence: Persistence, storage: RecordingStorage
) -> None:
    """Every blob the thread was holding, and only those.

    A second thread is deleted around: the bucket keys travel from the rows
    the DELETE actually removed, so a statement that forgot its ``WHERE``
    would show up here as somebody else's file disappearing.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    other = await make_thread(persistence, owner=ALICE)
    step_id = await make_step(persistence, thread_id)
    await make_element(
        persistence, thread_id, for_id=step_id, object_key="alice/one.png"
    )
    await make_element(persistence, thread_id, object_key="alice/two.png")
    await make_element(persistence, thread_id)
    await make_element(persistence, other, object_key="alice/elsewhere.png")
    login(client, auth, ALICE)

    response = await client.request(
        "DELETE", "/project/thread", json={"threadId": thread_id}
    )

    assert response.status_code == 200
    assert sorted(storage.deleted) == ["alice/one.png", "alice/two.png"]


async def test_a_store_that_refuses_does_not_fail_the_thread_delete(
    client, auth, persistence: Persistence, storage: RecordingStorage
) -> None:
    """One unreachable blob must not keep the thread on the user's screen."""
    thread_id = await make_thread(persistence, owner=ALICE)
    await make_element(persistence, thread_id, object_key="alice/one.png")
    await make_element(persistence, thread_id, object_key="alice/two.png")
    storage.broken = True
    login(client, auth, ALICE)

    response = await client.request(
        "DELETE", "/project/thread", json={"threadId": thread_id}
    )

    assert response.status_code == 200
    # Both attempted: a loop that let the first failure out would leave the
    # rest of the thread's files in the bucket for good.
    assert sorted(storage.deleted) == ["alice/one.png", "alice/two.png"]
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
    """``404``, and the same ``404`` a session that does not exist gets.

    Bob's session id is not a capability, and neither is confirming that it
    is live: a ``401``/``403`` here would tell Alice that the id she holds
    belongs to a session that exists right now.
    """
    login(client, auth, ALICE)
    response = await client.post(
        "/project/action",
        json={"sessionId": "bob-session", "action": {"name": "greet", "id": "1"}},
    )
    vanished = await client.post(
        "/project/action",
        json={"sessionId": "vanished", "action": {"name": "greet", "id": "1"}},
    )

    assert response.status_code == 404
    assert response.json() == vanished.json()
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
