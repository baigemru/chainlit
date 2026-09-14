"""Which url a stored element comes back with.

The column is not the answer. Every ``url`` this fork has ever written for an
uploaded blob points into the object store, and the consumer's store is a
private bucket -- so a thread reloaded from the database rendered a column of
broken images, and did so for every element ever persisted.

Read back through the real services against the migrated database rather than
by handing ``row_to_element`` a stub row: the substitution is about what the
row actually holds (a NULL ``objectKey``, a NULL ``threadId``), and a stub row
holds whatever the test decided it holds.
"""

from typing import Any, Dict, Optional

from chainlit.persistence.records import ElementRecord
from tests.persistence.conftest import make_thread, new_id

# What the dead writer produced. Verbatim shape from production rows: bucket
# hostname, region-less, no signature -- a url that 403s in a browser.
DEAD_URL = "https://panda-bucket.s3.amazonaws.com/user/element/report.xlsx"


async def save_element(
    uow: Any,
    thread_id: Optional[str],
    *,
    url: Optional[str],
    object_key: Optional[str],
    chainlit_key: Optional[str] = None,
    name: str = "report.xlsx",
) -> str:
    element_id = new_id()
    await uow.elements.save(
        ElementRecord(
            id=element_id,
            name=name,
            type="file",
            thread_id=thread_id,
            url=url,
            object_key=object_key,
            chainlit_key=chainlit_key,
            mime="application/vnd.ms-excel",
        )
    )
    return element_id


async def test_a_stored_blob_is_served_by_the_application(uow: Any) -> None:
    """The dead bucket url is replaced by this application's own route."""
    thread_id = await make_thread(uow)
    element_id = await save_element(
        uow, thread_id, url=DEAD_URL, object_key="user/element/report.xlsx"
    )

    element = await uow.elements.fetch(thread_id, element_id)

    assert element is not None
    assert element.url == f"/project/thread/{thread_id}/element/{element_id}/file"


async def test_the_route_is_app_relative(uow: Any) -> None:
    """No leading origin and no root path.

    The client's ``buildEndpoint`` prepends the deployment's root path, the
    same way it does for ``/project/file/{id}``. A prefix added here would be
    applied twice and the element would 404 behind a reverse proxy.
    """
    thread_id = await make_thread(uow)
    element_id = await save_element(uow, thread_id, url=None, object_key="k")

    element = await uow.elements.fetch(thread_id, element_id)

    assert element is not None
    assert element.url is not None
    assert element.url.startswith("/project/thread/")


async def test_an_element_that_was_never_uploaded_keeps_its_url(uow: Any) -> None:
    """``cl.Image(url="https://...")`` has no blob and needs no route.

    Nothing was uploaded, so ``objectKey`` is empty and the stored url is the
    only one there is -- and unlike the bucket's, it works.
    """
    thread_id = await make_thread(uow)
    element_id = await save_element(
        uow, thread_id, url="https://example.com/cat.png", object_key=None
    )

    element = await uow.elements.fetch(thread_id, element_id)

    assert element is not None
    assert element.url == "https://example.com/cat.png"


async def test_the_session_key_is_left_alone(uow: Any) -> None:
    """The live path serves elements through ``chainlitKey`` and still must."""
    thread_id = await make_thread(uow)
    element_id = await save_element(
        uow,
        thread_id,
        url=DEAD_URL,
        object_key="user/element/report.xlsx",
        chainlit_key="a-spooled-file-id",
    )

    element = await uow.elements.fetch(thread_id, element_id)

    assert element is not None
    assert element.chainlit_key == "a-spooled-file-id"


async def test_a_reloaded_thread_carries_the_substituted_url(uow: Any) -> None:
    """The bug's own path: the thread detail the UI redraws itself from."""
    thread_id = await make_thread(uow)
    element_id = await save_element(
        uow, thread_id, url=DEAD_URL, object_key="user/element/report.xlsx"
    )

    detail = await uow.threads.get_detail(thread_id)

    assert detail is not None
    urls: Dict[str, Any] = {element.id: element.url for element in detail.elements}
    assert urls[element_id] == f"/project/thread/{thread_id}/element/{element_id}/file"
