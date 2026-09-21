"""``/public``: the app's own static files, served without a login.

The old server had a hand-written route for this. Litestar's static router
does the same job with the traversal guard built in, so the only things
worth pinning are the contract: the files come from the plugin's
``public_dir``, a logged-out browser gets them, and a path that climbs out
of the directory does not.
"""

from pathlib import Path

import pytest
from litestar.testing import create_test_client

from chainlit.plugin import ChainlitPlugin
from chainlit.security import chainlit_auth


@pytest.fixture
def public_dir(tmp_path: Path) -> Path:
    public = tmp_path / "public"
    (public / "elements").mkdir(parents=True)
    (public / "avatar.png").write_bytes(b"\x89PNG-avatar")
    (public / "elements" / "Widget.jsx").write_text("export default () => null")
    return public


def _client(frontend_dir: Path, public_dir: Path, **kwargs):
    return create_test_client(
        plugins=[
            ChainlitPlugin(
                frontend_dir=frontend_dir,
                public_dir=public_dir,
                auth=chainlit_auth("s" * 32),
            )
        ],
        debug=False,
        **kwargs,
    )


def test_a_public_file_is_served_to_a_logged_out_browser(
    frontend_dir: Path, public_dir: Path
):
    with _client(frontend_dir, public_dir) as client:
        avatar = client.get("/public/avatar.png")
        element = client.get("/public/elements/Widget.jsx")

    assert avatar.status_code == 200
    assert avatar.content == b"\x89PNG-avatar"
    assert element.status_code == 200
    assert "export default" in element.text


def test_a_missing_public_file_is_a_404_not_the_spa(
    frontend_dir: Path, public_dir: Path
):
    with _client(frontend_dir, public_dir) as client:
        response = client.get("/public/nope.png", headers={"accept": "text/html"})

    assert response.status_code == 404


def test_a_path_that_climbs_out_of_public_is_refused(
    frontend_dir: Path, public_dir: Path
):
    (public_dir.parent / "secret.txt").write_text("nope")
    with _client(frontend_dir, public_dir) as client:
        response = client.get("/public/../secret.txt")

    assert response.status_code == 404


def test_an_element_source_carries_a_short_cache_control(
    frontend_dir: Path, public_dir: Path
):
    """``/public/elements`` has its own router, and it is the header.

    An element's ``.jsx`` is compiled in the browser, so an edit to it is a
    deploy; without a ``Cache-Control`` the browser applies heuristic
    freshness and can serve yesterday's form for days. ``max-age`` rather
    than a revalidation directive because Litestar 2.24 never reads
    ``If-None-Match`` -- a revalidation could only ever be a full download.
    """
    with _client(frontend_dir, public_dir) as client:
        element = client.get("/public/elements/Widget.jsx")
        avatar = client.get("/public/avatar.png")

    assert element.status_code == 200
    assert element.headers["cache-control"] == "max-age=60"
    # The rest of ``/public`` is untouched: a logo is not redeployed by
    # editing a file the browser already has.
    assert "cache-control" not in avatar.headers


def test_a_missing_element_is_a_404_from_the_element_router(
    frontend_dir: Path, public_dir: Path
):
    """The more specific router answers, rather than falling through."""
    with _client(frontend_dir, public_dir) as client:
        response = client.get(
            "/public/elements/Nope.jsx", headers={"accept": "text/html"}
        )

    assert response.status_code == 404
