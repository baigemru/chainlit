"""Serving the blob behind a persisted element.

The bucket is private, so the application reads the object and forwards it
rather than pointing the browser at the store. That makes this route the only
thing standing between one user's thread and another's files, and most of what
is asserted here is a refusal.

The storage client is a dict. Nothing about the questions below -- who may
read, what the browser is told the file is called -- is a question about S3,
and a real client would only add a network to the answer.

Fixtures are copied from ``test_project.py`` rather than imported: the two
files are owned by different changes, and an import would make every edit
there an edit here.
"""

import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Union

import pytest
import pytest_asyncio
from advanced_alchemy.extensions.litestar import SQLAlchemyInitPlugin
from litestar.di import Provide
from litestar.testing import create_async_test_client
from sqlalchemy.ext.asyncio import AsyncEngine

from chainlit.controllers.project import ProjectController
from chainlit.persistence import Persistence
from chainlit.persistence.records import ElementRecord, ThreadPatch
from chainlit.persistence.storage.base import BaseStorageClient
from chainlit.security import chainlit_auth
from tests.persistence.conftest import database_url, engine

# Re-exported so pytest finds the persistence fixtures from this module.
__all__ = ["database_url", "engine"]

TEST_SECRET = "a-test-secret-that-is-long-enough-for-hs256"

ALICE = "alice@example.com"
BOB = "bob@example.com"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Precomputed rather than derived in the assertion: a test that encodes the
# name the same way the code does passes whatever the code does.
CYRILLIC_NAME = "Отчёт по товарам.xlsx"
CYRILLIC_QUOTED = "%D0%9E%D1%82%D1%87%D1%91%D1%82%20%D0%BF%D0%BE%20%D1%82%D0%BE%D0%B2%D0%B0%D1%80%D0%B0%D0%BC.xlsx"


class DictStorage(BaseStorageClient):
    """A bucket that is a dict, and a log of what was asked of it."""

    def __init__(self, objects: Optional[Dict[str, bytes]] = None) -> None:
        self.objects = dict(objects or {})
        self.reads: List[str] = []

    async def upload_file(
        self,
        object_key: str,
        data: Union[bytes, str],
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.objects[object_key] = data.encode() if isinstance(data, str) else data
        return {"object_key": object_key, "url": f"memory://{object_key}"}

    async def delete_file(self, object_key: str) -> bool:
        return self.objects.pop(object_key, None) is not None

    async def get_read_url(self, object_key: str) -> str:
        return f"memory://{object_key}"

    async def read_file(self, object_key: str) -> Optional[bytes]:
        self.reads.append(object_key)
        return self.objects.get(object_key)

    async def close(self) -> None:
        return None


@pytest_asyncio.fixture
async def persistence(engine: AsyncEngine) -> Persistence:
    return Persistence.from_engine(engine)


@pytest.fixture
def auth():
    return chainlit_auth(token_secret=TEST_SECRET)


@pytest.fixture
def storage() -> DictStorage:
    return DictStorage()


@pytest_asyncio.fixture
async def client(
    persistence: Persistence, auth, storage: DictStorage
) -> AsyncIterator[Any]:
    async with create_async_test_client(
        route_handlers=[ProjectController],
        plugins=[SQLAlchemyInitPlugin(config=persistence.config)],
        dependencies={
            **persistence.dependencies(),
            "sessions": Provide(lambda: None, sync_to_thread=False),
            "persistence_enabled": Provide(lambda: True, sync_to_thread=False),
            "storage": Provide(lambda: storage, sync_to_thread=False),
        },
        on_app_init=[auth.on_app_init],
    ) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def client_without_storage(persistence: Persistence, auth) -> AsyncIterator[Any]:
    """An application that persists elements but has nowhere to put blobs.

    No ``storage`` key at all, which is also the assertion that the route's
    dependency is optional: a required one with no provider is a registration
    error, and this client would not start.
    """
    async with create_async_test_client(
        route_handlers=[ProjectController],
        plugins=[SQLAlchemyInitPlugin(config=persistence.config)],
        dependencies={
            **persistence.dependencies(),
            "sessions": Provide(lambda: None, sync_to_thread=False),
            "persistence_enabled": Provide(lambda: True, sync_to_thread=False),
        },
        on_app_init=[auth.on_app_init],
    ) as test_client:
        yield test_client


def login(client: Any, auth: Any, identifier: str) -> None:
    client.cookies.set(auth.key, auth.create_token(identifier))


async def make_thread(persistence: Persistence, *, owner: str) -> str:
    thread_id = str(uuid.uuid4())
    async with persistence.uow() as uow:
        await uow.threads.patch(
            thread_id, ThreadPatch(name="a thread", user_identifier=owner)
        )
    return thread_id


async def make_element(
    persistence: Persistence,
    thread_id: str,
    *,
    name: str = "report.xlsx",
    object_key: Optional[str] = "alice/report.xlsx",
    mime: Optional[str] = XLSX_MIME,
    url: Optional[str] = "https://a-bucket.s3.amazonaws.com/alice/report.xlsx",
) -> str:
    element_id = str(uuid.uuid4())
    async with persistence.uow() as uow:
        await uow.elements.save(
            ElementRecord(
                id=element_id,
                name=name,
                type="file",
                thread_id=thread_id,
                object_key=object_key,
                mime=mime,
                url=url,
            )
        )
    return element_id


def file_url(thread_id: str, element_id: str) -> str:
    return f"/project/thread/{thread_id}/element/{element_id}/file"


async def test_the_author_gets_the_blob(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """Bytes, mime and filename, through the application."""
    storage.objects["alice/report.xlsx"] = b"PK\x03\x04 a spreadsheet"
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)
    login(client, auth, ALICE)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 200
    assert response.content == b"PK\x03\x04 a spreadsheet"
    assert response.headers["content-type"].startswith(XLSX_MIME)
    assert response.headers["content-disposition"] == (
        'attachment; filename="report.xlsx"'
    )
    assert storage.reads == ["alice/report.xlsx"]


async def test_somebody_elses_thread_is_not_read_at_all(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """404, and the bucket is never touched.

    The read is asserted *not* to have happened, not only the status: a route
    that fetched first and refused afterwards would pass a status assertion
    while still reaching into another user's storage.
    """
    storage.objects["alice/report.xlsx"] = b"alice's numbers"
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)
    login(client, auth, BOB)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 404
    assert storage.reads == []


async def test_a_caller_with_no_cookie_is_refused(
    client, persistence: Persistence, storage: DictStorage
) -> None:
    storage.objects["alice/report.xlsx"] = b"alice's numbers"
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 401
    assert storage.reads == []


async def test_an_element_that_does_not_exist_is_a_404(
    client, auth, persistence: Persistence
) -> None:
    thread_id = await make_thread(persistence, owner=ALICE)
    login(client, auth, ALICE)

    response = await client.get(file_url(thread_id, str(uuid.uuid4())))

    assert response.status_code == 404


async def test_an_element_of_another_thread_is_not_reachable_through_this_one(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """The lookup is thread-scoped, so Bob's own thread is not a key to it."""
    storage.objects["alice/report.xlsx"] = b"alice's numbers"
    alice_thread = await make_thread(persistence, owner=ALICE)
    bob_thread = await make_thread(persistence, owner=BOB)
    element_id = await make_element(persistence, alice_thread)
    login(client, auth, BOB)

    response = await client.get(file_url(bob_thread, element_id))

    assert response.status_code == 404
    assert storage.reads == []


async def test_an_element_with_no_blob_is_a_404(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """An element given a url it already had has nothing here to serve."""
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(
        persistence, thread_id, object_key=None, url="https://example.com/cat.png"
    )
    login(client, auth, ALICE)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 404
    assert storage.reads == []


async def test_an_empty_object_key_is_not_a_key(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """An upload that failed leaves the column empty rather than absent.

    Asserted on the read log, not only the status: an empty key reaches the
    store as a lookup of ``""``, which every one of them answers with a miss
    -- so the 404 would arrive either way, one network round trip later.
    """
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id, object_key="")
    login(client, auth, ALICE)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 404
    assert storage.reads == []


async def test_an_application_with_no_storage_refuses_rather_than_fails(
    client_without_storage, auth, persistence: Persistence
) -> None:
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)
    login(client_without_storage, auth, ALICE)

    response = await client_without_storage.get(file_url(thread_id, element_id))

    assert response.status_code == 404


async def test_a_blob_the_store_has_lost_is_a_404(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """The row survives a delete in the bucket; the response must not 500."""
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)
    login(client, auth, ALICE)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 404
    assert storage.reads == ["alice/report.xlsx"]


async def test_a_cyrillic_name_survives_the_header(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """RFC 5987, because a header is latin-1 and this consumer is not.

    The encoded form is written out: this is the one place where "the code
    and the test agree" is not evidence, since both would be wrong together.
    """
    storage.objects["alice/report.xlsx"] = b"PK\x03\x04"
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id, name=CYRILLIC_NAME)
    login(client, auth, ALICE)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f"attachment; filename*=utf-8''{CYRILLIC_QUOTED}"
    )


@pytest.mark.parametrize(
    ("name", "mime"),
    [("chart.png", "image/png"), ("report.pdf", "application/pdf")],
)
async def test_what_the_browser_can_draw_is_shown_in_place(
    client, auth, persistence: Persistence, storage: DictStorage, name: str, mime: str
) -> None:
    """``inline``: the UI embeds these in the conversation, not in a download."""
    storage.objects["alice/report.xlsx"] = b"bytes"
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id, name=name, mime=mime)
    login(client, auth, ALICE)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'inline; filename="{name}"'


async def test_the_stored_mime_beats_the_extension(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """The name is a fallback, not the answer.

    Blobs are spooled under names the extension of which says nothing -- the
    upload path has written ``.bin`` and no extension at all -- and what the
    sender declared at upload time is the better fact.
    """
    storage.objects["alice/report.xlsx"] = b"\x89PNG"
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(
        persistence, thread_id, name="chart.bin", mime="image/png"
    )
    login(client, auth, ALICE)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"] == 'inline; filename="chart.bin"'


async def test_a_row_with_no_mime_is_named_by_its_extension(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """``mime`` is NULL on every row written before the column was filled.

    Served as ``application/octet-stream``, a PNG is a download rather than
    an image, so the name is asked before the fallback is taken.
    """
    storage.objects["alice/report.xlsx"] = b"\x89PNG"
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id, name="chart.png", mime=None)
    login(client, auth, ALICE)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"] == 'inline; filename="chart.png"'


async def test_a_nameless_extension_falls_back_to_a_download(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    storage.objects["alice/report.xlsx"] = b"bytes"
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id, name="dump", mime=None)
    login(client, auth, ALICE)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["content-disposition"] == 'attachment; filename="dump"'
