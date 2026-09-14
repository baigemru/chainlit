"""Serving the blob behind a persisted element.

The bucket is private, so the application reads the object and forwards it
rather than pointing the browser at the store. That makes these routes the
only thing standing between one user's thread and another's files, and most of
what is asserted here is a refusal.

There are two of them, and one body of delivery under both. The author route
is gated on whose thread it is; the share route on whether the thread's owner
published it. Every question that is not that gate -- the filename header, the
mime, the validator, the 404 on a broken row -- is asked of both, and the
caching cases are parametrized over the pair rather than written twice, since
a behaviour that held on one form only would be a behaviour the shared helper
does not actually have.

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

# Same rule for the validator: written out, not recomputed with hashlib here.
SPREADSHEET = b"PK\x03\x04 a spreadsheet"
SPREADSHEET_ETAG = '"edbb2c8da2d2b5edc4071d7a97a6b181"'
SECOND_DRAFT = b"a second draft"
SECOND_DRAFT_ETAG = '"206873835d06f54d6859466d66f4c121"'

CACHE_CONTROL = "private, max-age=3600"


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


async def make_thread(
    persistence: Persistence, *, owner: str, shared: Optional[bool] = None
) -> str:
    thread_id = str(uuid.uuid4())
    async with persistence.uow() as uow:
        await uow.threads.patch(
            thread_id,
            ThreadPatch(
                name="a thread",
                user_identifier=owner,
                metadata={} if shared is None else {"is_shared": shared},
            ),
        )
    return thread_id


async def set_shared(persistence: Persistence, thread_id: str, shared: bool) -> None:
    """Publish or withdraw, the way ``PUT /project/thread/share`` does."""
    async with persistence.uow() as uow:
        await uow.threads.patch(thread_id, ThreadPatch(metadata={"is_shared": shared}))


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


def share_url(thread_id: str, element_id: str) -> str:
    return f"/project/share/{thread_id}/element/{element_id}/file"


async def a_reachable_blob(
    client: Any, auth: Any, persistence: Persistence, form: str
) -> str:
    """A url on ``form`` that this client is entitled to read.

    The two forms are entitled differently -- one by a cookie, the other by
    the thread's ``is_shared`` -- which is the whole of what the parametrized
    cases below must not care about.
    """
    thread_id = await make_thread(
        persistence, owner=ALICE, shared=True if form == "share" else None
    )
    element_id = await make_element(persistence, thread_id)
    if form == "author":
        login(client, auth, ALICE)
        return file_url(thread_id, element_id)
    return share_url(thread_id, element_id)


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


# --------------------------------------------------------------------------
# The share form: same blob, a different reason to be allowed it
# --------------------------------------------------------------------------


async def test_a_stranger_gets_the_blob_of_a_published_thread(
    client, persistence: Persistence, storage: DictStorage
) -> None:
    """No cookie at all, which is the point: a share link that needs a login
    is not a share link, and a shared page whose every image 404s is not a
    shared page."""
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    thread_id = await make_thread(persistence, owner=ALICE, shared=True)
    element_id = await make_element(persistence, thread_id)

    response = await client.get(share_url(thread_id, element_id))

    assert response.status_code == 200
    assert response.content == SPREADSHEET
    assert response.headers["content-type"].startswith(XLSX_MIME)
    assert response.headers["content-disposition"] == (
        'attachment; filename="report.xlsx"'
    )


async def test_an_unpublished_thread_serves_no_files(
    client, persistence: Persistence, storage: DictStorage
) -> None:
    """The gate is the thread's own flag, and it refuses before the bucket.

    Asserted on the read log as well as the status: a route that fetched the
    object and refused afterwards would pass the status assertion while
    reaching into a private thread's storage on any stranger's request.
    """
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)

    response = await client.get(share_url(thread_id, element_id))

    assert response.status_code == 404
    assert storage.reads == []
    assert "etag" not in response.headers
    assert "cache-control" not in response.headers


async def test_a_withdrawn_share_takes_its_files_with_it(
    client, persistence: Persistence, storage: DictStorage
) -> None:
    """The flag is read per request, so unpublishing is immediate.

    Nothing here is signed or handed out with an expiry, which is what makes
    that true: there is no outstanding token for a file that outlives the
    thread's own answer to "may a stranger read this".
    """
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    thread_id = await make_thread(persistence, owner=ALICE, shared=True)
    element_id = await make_element(persistence, thread_id)
    url = share_url(thread_id, element_id)

    assert (await client.get(url)).status_code == 200

    await set_shared(persistence, thread_id, False)

    assert (await client.get(url)).status_code == 404
    assert storage.reads == ["alice/report.xlsx"]


async def test_a_shared_thread_is_not_a_key_to_another_threads_element(
    client, persistence: Persistence, storage: DictStorage
) -> None:
    """Publishing one thread publishes one thread.

    The element lookup is scoped to the thread in the url, so an id lifted
    out of a private thread -- ids are not secrets, this route hands them to
    strangers by design -- does not become readable by pairing it with a
    shared thread's id.
    """
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    published = await make_thread(persistence, owner=ALICE, shared=True)
    private = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, private)

    response = await client.get(share_url(published, element_id))

    assert response.status_code == 404
    assert storage.reads == []


async def test_publishing_a_thread_does_not_open_the_author_route(
    client, persistence: Persistence, storage: DictStorage
) -> None:
    """The share is a second door, not a hole in the first one."""
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    thread_id = await make_thread(persistence, owner=ALICE, shared=True)
    element_id = await make_element(persistence, thread_id)

    response = await client.get(file_url(thread_id, element_id))

    assert response.status_code == 401
    assert storage.reads == []


# The rewrite that points a shared thread's elements at the route above is
# asserted in ``test_project.py``, next to the rest of ``get_shared_thread``:
# it is a property of the thread read, and this client has no session
# registry for that route to take.


# --------------------------------------------------------------------------
# Validators: a reloaded thread should not re-download every blob
# --------------------------------------------------------------------------


@pytest.mark.parametrize("form", ["author", "share"])
async def test_the_blob_arrives_with_a_validator_and_a_private_cache(
    client, auth, persistence: Persistence, storage: DictStorage, form: str
) -> None:
    """``private`` is not decoration.

    Without it a shared cache between the browser and the app may hold one
    user's file against a url and hand it to whoever asks next -- and the
    author route's whole job is that this does not happen.
    """
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    url = await a_reachable_blob(client, auth, persistence, form)

    response = await client.get(url)

    assert response.status_code == 200
    assert response.headers["etag"] == SPREADSHEET_ETAG
    assert response.headers["cache-control"] == CACHE_CONTROL


@pytest.mark.parametrize("form", ["author", "share"])
async def test_a_matching_validator_is_answered_with_no_body(
    client, auth, persistence: Persistence, storage: DictStorage, form: str
) -> None:
    """304, and both headers repeated on it.

    RFC 9110 §15.4.5: a cache updates its stored response from the 304, so a
    304 that dropped ``cache-control`` would silently shorten the freshness
    it just confirmed.

    The read log is asserted because it pins the honest limitation rather
    than letting the comment in the handler stand alone: the object is
    fetched again to compute the tag. The saving is the client's download,
    not the bucket's GET.
    """
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    url = await a_reachable_blob(client, auth, persistence, form)

    first = await client.get(url)
    again = await client.get(url, headers={"if-none-match": first.headers["etag"]})

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == SPREADSHEET_ETAG
    assert again.headers["cache-control"] == CACHE_CONTROL
    assert storage.reads == ["alice/report.xlsx", "alice/report.xlsx"]
    # Not asserted: that the 304 carries no content-type or content-length.
    # Litestar cannot emit either on a status that forbids a body -- the
    # branch that sets them is behind ``status_allows_body``
    # (``response/base.py:107-120``) -- so there is no change to this handler
    # that would make such an assertion fail, and an assertion nothing can
    # red is a claim of coverage rather than coverage.


@pytest.mark.parametrize("form", ["author", "share"])
async def test_a_stale_validator_is_answered_with_the_bytes(
    client, auth, persistence: Persistence, storage: DictStorage, form: str
) -> None:
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    url = await a_reachable_blob(client, auth, persistence, form)

    response = await client.get(url, headers={"if-none-match": '"not-this-one"'})

    assert response.status_code == 200
    assert response.content == SPREADSHEET


async def test_a_validator_anywhere_in_the_list_matches(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """``If-None-Match`` is a list; a browser holding two versions sends both."""
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    url = await a_reachable_blob(client, auth, persistence, "author")

    response = await client.get(
        url, headers={"if-none-match": f'"an-older-one", {SPREADSHEET_ETAG}'}
    )

    assert response.status_code == 304


async def test_a_star_matches_whatever_is_there(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    url = await a_reachable_blob(client, auth, persistence, "author")

    response = await client.get(url, headers={"if-none-match": "*"})

    assert response.status_code == 304


async def test_a_weak_validator_never_matches_a_strong_one(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """RFC 9110 §8.8.3.2's strong comparison, which is what exact means.

    Nothing here ever tags weakly, so a ``W/`` in the request came from
    somewhere else and the bytes are the honest reply. Written down because
    stripping the prefix is the obvious-looking helpfulness that would break
    it without breaking anything else.
    """
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    url = await a_reachable_blob(client, auth, persistence, "author")

    response = await client.get(url, headers={"if-none-match": f"W/{SPREADSHEET_ETAG}"})

    assert response.status_code == 200
    assert response.content == SPREADSHEET


async def test_new_bytes_under_the_same_key_get_a_new_validator(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """The tag is of the content, and it has to be.

    The upload path writes with ``overwrite=True``, so one ``objectKey``
    holds different bytes over a row's life. A tag derived from the key would
    pin every browser that ever loaded the thread to the first version of the
    file, for an hour at a time, with no way to notice.
    """
    storage.objects["alice/report.xlsx"] = SPREADSHEET
    url = await a_reachable_blob(client, auth, persistence, "author")

    first = await client.get(url)
    assert first.headers["etag"] == SPREADSHEET_ETAG

    storage.objects["alice/report.xlsx"] = SECOND_DRAFT
    response = await client.get(url, headers={"if-none-match": SPREADSHEET_ETAG})

    assert response.status_code == 200
    assert response.content == SECOND_DRAFT
    assert response.headers["etag"] == SECOND_DRAFT_ETAG


async def test_a_404_offers_nothing_to_cache(
    client, auth, persistence: Persistence, storage: DictStorage
) -> None:
    """A refusal a browser stored for an hour is a refusal that outlives its
    reason -- the share being published, the blob being re-uploaded."""
    thread_id = await make_thread(persistence, owner=ALICE)
    element_id = await make_element(persistence, thread_id)
    login(client, auth, ALICE)

    # The row is fine; the bucket has lost the object.
    refused = await client.get(file_url(thread_id, element_id))

    assert refused.status_code == 404
    assert "etag" not in refused.headers
    assert "cache-control" not in refused.headers
