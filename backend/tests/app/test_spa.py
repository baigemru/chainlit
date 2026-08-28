"""The SPA fallback, and the 404s it must not swallow.

``/thread/<id>`` is a client-side route: it matches no handler, so the server
has to answer it with ``index.html`` or a page reload 404s. The same miss
arriving from ``fetch`` has to stay a 404 -- a fallback that answers every
miss with the SPA turns every client bug into a silent 200 of HTML.
"""

from pathlib import Path

from litestar import get
from litestar.exceptions import NotFoundException
from litestar.testing import create_test_client

from chainlit.plugin import ChainlitPlugin

from .conftest import INDEX_MARKER

BROWSER = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


@get("/api/thing", exclude_from_auth=True)
async def missing_thing() -> None:
    raise NotFoundException("no such thing")


def _client(frontend_dir: Path, **kwargs):
    return create_test_client(
        route_handlers=[missing_thing],
        plugins=[ChainlitPlugin(frontend_dir=frontend_dir)],
        debug=False,
        **kwargs,
    )


def test_an_unrouted_browser_path_gets_the_spa(frontend_dir: Path):
    with _client(frontend_dir) as client:
        response = client.get("/thread/abc-123", headers=BROWSER)

    assert response.status_code == 200
    assert INDEX_MARKER in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_the_spa_is_served_with_200_not_404(frontend_dir: Path):
    """The browser is at a real client-side route; the document that boots the
    app must not arrive as an error."""
    with _client(frontend_dir) as client:
        assert client.get("/", headers=BROWSER).status_code == 200


def test_an_unrouted_api_path_still_404s(frontend_dir: Path):
    """No ``Accept: text/html`` -- this is a client calling, not a browser
    navigating. httpx sends ``*/*``, exactly as the frontend's own fetch does."""
    with _client(frontend_dir) as client:
        response = client.get("/no/such/route")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["status_code"] == 404


def test_a_404_raised_by_a_route_is_not_swallowed(frontend_dir: Path):
    """The route matched and answered 'not found'. That is an answer, not a
    missing page, and it must survive to the client verbatim."""
    with _client(frontend_dir) as client:
        response = client.get("/api/thing")

    assert response.status_code == 404
    assert response.json()["detail"] == "no such thing"


def test_a_post_to_an_unrouted_path_404s_even_from_a_browser(frontend_dir: Path):
    """The SPA is a document to navigate to, never the answer to a write."""
    with _client(frontend_dir) as client:
        response = client.post("/thread/abc-123", headers=BROWSER, json={})

    assert response.status_code == 404


def test_the_assets_router_serves_real_files(frontend_dir: Path):
    with _client(frontend_dir) as client:
        response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert "console.log" in response.text


def test_a_missing_asset_is_a_404_not_the_spa(frontend_dir: Path):
    """A stale bundle reference must fail loudly. Serving ``index.html`` as
    ``app-deadbeef.js`` gives the browser a syntax error instead."""
    with _client(frontend_dir) as client:
        response = client.get("/assets/app-deadbeef.js", headers=BROWSER)

    assert response.status_code == 404
    assert INDEX_MARKER not in response.text


def test_a_host_can_keep_its_own_404_handler(frontend_dir: Path):
    from litestar import Request, Response

    def host_404(request: Request, exc: Exception) -> Response:
        return Response(content={"host": True}, status_code=404)

    with _client(frontend_dir, exception_handlers={NotFoundException: host_404}) as c:
        response = c.get("/thread/abc-123", headers=BROWSER)

    assert response.json() == {"host": True}


def test_without_a_built_frontend_a_browser_path_404s(tmp_path: Path):
    """No ``dist`` on disk is a deployment error, and it has to look like one
    rather than like an empty page."""
    with create_test_client(
        plugins=[ChainlitPlugin(frontend_dir=tmp_path / "nothing")], debug=False
    ) as client:
        assert client.get("/thread/abc", headers=BROWSER).status_code == 404
