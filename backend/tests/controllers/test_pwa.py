"""What makes this app installable, and what the three PWA routes promise.

Two layers, because two different things can break. :func:`render_manifest`
is a function of the config, a directory and ``APP_ROOT`` (which the icon
fallback resolves the app's favicon through), so the questions about
*content* -- whose name, which prefix, which icons, which colours -- are
asked of it directly. The questions about *delivery* -- is it public, does
the worker carry ``no-cache``, does it carry this release's version, is a
missing worker a 404 -- are asked of a real client.

The authentication middleware is installed in that client, next to one
route that is deliberately not excluded from it. "The manifest is public"
is not an assertion at all in an app with no authentication; it only means
something beside a route that refuses the same anonymous caller.

``FRONTEND_DIST`` is monkeypatched everywhere the built bundle would
otherwise be read. The frontend build owns ``sw.js``, this suite does not,
and a test that passes only in a checkout where someone has run ``pnpm
build`` is a test that reports the build's state rather than the code's.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Optional

import pytest
from litestar import get
from litestar.testing import create_test_client

import chainlit.config
import chainlit.version
from chainlit.controllers import pwa
from chainlit.controllers.index import TAG_PLACEHOLDER, render_index
from chainlit.controllers.pwa import BUILD_PLACEHOLDER, PwaController, render_manifest
from chainlit.security import chainlit_auth

# Long enough that PyJWT does not warn about it, which pytest turns into an
# error under this repo's -W settings.
TEST_SECRET = "a-test-secret-that-is-long-enough-for-hs256"


def a_config(
    name: str = "My App",
    description: str = "",
    root_path: str = "",
    default_theme: Optional[str] = "dark",
    short_name: Any = None,
) -> Any:
    """The five fields ``render_manifest`` reads.

    ``default_theme`` defaults to "dark" here because it does in
    ``UISettings``: a fixture that quietly disagreed with the real default
    would test a deployment nobody runs.

    The rest of ``ui`` is here only because the two tag tests below hand the
    same object to ``render_index``, which reads the custom-asset fields.
    """
    return SimpleNamespace(
        ui=SimpleNamespace(
            name=name,
            short_name=short_name,
            description=description,
            default_theme=default_theme,
            custom_meta_url=None,
            custom_meta_image_url=None,
            custom_css=None,
            custom_css_attributes="",
            custom_js=None,
            custom_js_attributes="",
        ),
        run=SimpleNamespace(root_path=root_path),
    )


@pytest.fixture
def auth():
    return chainlit_auth(token_secret=TEST_SECRET)


@pytest.fixture(autouse=True)
def app_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty app directory, so ``public/`` is whatever a test puts there.

    Autouse, because the icon fallback resolves the app's favicon through
    ``APP_ROOT`` rather than through the directory handed to
    ``render_manifest``: without this, a manifest test would read whatever
    ``public/favicon.*`` the checkout it runs in happens to have, and the
    fallback's declared type would be a property of the machine.
    """
    root = tmp_path / "app"
    (root / "public").mkdir(parents=True)
    monkeypatch.setattr(chainlit.config, "APP_ROOT", str(root))
    return root


@pytest.fixture
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for the built frontend, empty until a test fills it."""
    built = tmp_path / "dist"
    built.mkdir()
    monkeypatch.setattr(pwa, "FRONTEND_DIST", built)
    return built


@get("/private")
async def private_route() -> dict:
    """The contrast: a route that has not opted out of authentication."""
    return {}


@pytest.fixture
def client(auth, app_root: Path, dist: Path) -> Iterator[Any]:
    with create_test_client(
        route_handlers=[PwaController, private_route],
        on_app_init=[auth.on_app_init],
    ) as test_client:
        yield test_client


# --------------------------------------------------------------------------
# The manifest body
# --------------------------------------------------------------------------


def test_the_manifest_carries_this_deployments_identity(tmp_path: Path) -> None:
    manifest = render_manifest(
        a_config(name="Panda", description="A product search assistant"), tmp_path
    )

    assert manifest["name"] == "Panda"
    assert manifest["short_name"] == "Panda"
    assert manifest["description"] == "A product search assistant"
    assert manifest["display"] == "standalone"


def test_without_a_root_path_the_scope_is_the_origin(tmp_path: Path) -> None:
    manifest = render_manifest(a_config(), tmp_path)

    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"


def test_the_manifest_body_embeds_the_root_path(tmp_path: Path) -> None:
    """The reverse of the HTML rule, and the reason both are written down.

    ``render_index`` rewrites every ``href="/`` in the finished document,
    so the tags it injects must not carry the prefix. Nothing rewrites
    JSON, so everything in here must. A manifest whose ``scope`` is ``/``
    on a deployment under ``/app`` is a manifest the browser refuses.
    """
    manifest = render_manifest(a_config(root_path="/app"), tmp_path)

    assert manifest["start_url"] == "/app/"
    assert manifest["scope"] == "/app/"
    assert manifest["icons"][0]["src"] == "/app/favicon"


def test_a_trailing_slash_on_the_root_path_is_not_doubled(tmp_path: Path) -> None:
    assert render_manifest(a_config(root_path="/app/"), tmp_path)["scope"] == "/app/"


def test_an_app_with_no_icons_falls_back_to_the_favicon(tmp_path: Path) -> None:
    """``icons`` may not be empty: an empty array is not a valid manifest.

    Valid is all it buys. The install prompt wants a 192 and a 512 raster
    icon, and the bundled SVG is neither -- see the next two tests for what
    the fallback actually declares.
    """
    icons = render_manifest(a_config(), tmp_path)["icons"]

    assert icons == [{"src": "/favicon", "type": "image/svg+xml", "sizes": "any"}]


def test_the_fallback_icon_declares_the_favicons_real_format(
    app_root: Path, tmp_path: Path
) -> None:
    """``favicon_file()`` returns the first ``favicon.*`` in any format.

    An app that drew its favicon as a PNG got ``image/svg+xml`` announced
    over it, which is a manifest that describes a file the browser then
    fails to decode. And ``sizes: "any"`` is the SVG spelling for "scales
    to anything": over a raster icon it is a second lie, so it goes.
    """
    (app_root / "public" / "favicon.png").write_bytes(b"raster")

    icons = render_manifest(a_config(), tmp_path)["icons"]

    assert icons == [{"src": "/favicon", "type": "image/png"}]


def test_an_svg_favicon_keeps_its_type_and_scales_to_any_size(
    app_root: Path, tmp_path: Path
) -> None:
    (app_root / "public" / "favicon.svg").write_bytes(b"<svg/>")

    icons = render_manifest(a_config(), tmp_path)["icons"]

    assert icons == [{"src": "/favicon", "type": "image/svg+xml", "sizes": "any"}]


def test_a_favicon_of_no_guessable_type_declares_none(
    app_root: Path, tmp_path: Path
) -> None:
    """``type`` is optional; a wrong one is worse than a missing one."""
    (app_root / "public" / "favicon.weird").write_bytes(b"who knows")

    icons = render_manifest(a_config(), tmp_path)["icons"]

    assert icons == [{"src": "/favicon"}]


# --------------------------------------------------------------------------
# Colours and the installed name
# --------------------------------------------------------------------------


def test_the_manifest_colours_follow_a_dark_theme(tmp_path: Path) -> None:
    """``theme_color`` is the Android status bar for the whole session.

    It is not a frame of splash screen: white here over a dark app is a
    white bar until the user closes the app. The hex is ``--background``
    from the ``.dark`` block of ``frontend/src/index.css``.
    """
    manifest = render_manifest(a_config(default_theme="dark"), tmp_path)

    assert manifest["background_color"] == "#212121"
    assert manifest["theme_color"] == "#212121"


def test_the_manifest_colours_follow_a_light_theme(tmp_path: Path) -> None:
    manifest = render_manifest(a_config(default_theme="light"), tmp_path)

    assert manifest["background_color"] == "#ffffff"
    assert manifest["theme_color"] == "#ffffff"


def test_an_unset_theme_is_treated_as_light(tmp_path: Path) -> None:
    """``default_theme`` is Optional; only "dark" is dark."""
    manifest = render_manifest(a_config(default_theme=None), tmp_path)

    assert manifest["theme_color"] == "#ffffff"


def test_the_short_name_is_what_the_home_screen_gets(tmp_path: Path) -> None:
    """A launcher truncates at roughly twelve characters."""
    manifest = render_manifest(
        a_config(name="Panda Product Search Assistant", short_name="Panda"), tmp_path
    )

    assert manifest["name"] == "Panda Product Search Assistant"
    assert manifest["short_name"] == "Panda"


def test_the_apps_own_icons_win_and_carry_their_sizes(tmp_path: Path) -> None:
    (tmp_path / "pwa-icon-192.png").write_bytes(b"small")
    (tmp_path / "pwa-icon-maskable-512.png").write_bytes(b"big")

    icons = render_manifest(a_config(root_path="/app"), tmp_path)["icons"]

    assert icons == [
        {
            "src": "/app/public/pwa-icon-192.png",
            "type": "image/png",
            "sizes": "192x192",
        },
        {
            "src": "/app/public/pwa-icon-maskable-512.png",
            "type": "image/png",
            "sizes": "512x512",
            "purpose": "maskable",
        },
    ]


def test_an_icon_with_no_size_in_its_name_is_skipped(tmp_path: Path) -> None:
    """A stray file in an app's ``public/`` must not 500 the manifest."""
    (tmp_path / "pwa-icon-old.png").write_bytes(b"who knows")
    (tmp_path / "pwa-icon-192.png").write_bytes(b"small")

    icons = render_manifest(a_config(), tmp_path)["icons"]

    assert [icon["src"] for icon in icons] == ["/public/pwa-icon-192.png"]


# --------------------------------------------------------------------------
# The tags in the document
# --------------------------------------------------------------------------


def test_the_injected_pwa_tags_are_prefixed_exactly_once(tmp_path: Path) -> None:
    """The trap this pair of links is written to avoid.

    ``render_index`` rewrites every ``href="/`` in the finished document,
    so a tag injected with the prefix already on it comes out doubled --
    ``/app/app/manifest.webmanifest``, a 404 in a browser and no install
    prompt. The tags therefore go in bare and are prefixed by that pass,
    which is the opposite of the rule the manifest body follows.

    Scoped to these two links on purpose: the favicon line beside them
    embeds the prefix *and* is rewritten, so it really does come out
    ``/app/app/favicon``. That is a pre-existing defect, it is not this
    lane's, and an assertion over the whole document would fail on it.
    """
    shell = f"<html><head>{TAG_PLACEHOLDER}</head></html>"
    page = render_index(shell, a_config(root_path="/app"), tmp_path)

    assert 'href="/app/manifest.webmanifest"' in page
    assert 'href="/app/apple-touch-icon"' in page
    assert "/app/app/manifest.webmanifest" not in page
    assert "/app/app/apple-touch-icon" not in page


def test_without_a_root_path_the_tags_point_at_the_origin(tmp_path: Path) -> None:
    page = render_index(f"<html>{TAG_PLACEHOLDER}</html>", a_config(), tmp_path)

    assert '<link rel="manifest" href="/manifest.webmanifest" />' in page
    assert '<link rel="apple-touch-icon" href="/apple-touch-icon" />' in page


def test_the_ios_status_bar_follows_a_dark_theme(tmp_path: Path) -> None:
    """iOS paints the standalone status bar from this, once, at launch.

    It cannot be in the built shell: the theme it has to match belongs to
    the deployment's config, not to the build.
    """
    page = render_index(
        f"<html>{TAG_PLACEHOLDER}</html>", a_config(default_theme="dark"), tmp_path
    )

    assert (
        '<meta name="apple-mobile-web-app-status-bar-style" content="black" />' in page
    )


def test_the_ios_status_bar_follows_a_light_theme(tmp_path: Path) -> None:
    page = render_index(
        f"<html>{TAG_PLACEHOLDER}</html>", a_config(default_theme="light"), tmp_path
    )

    assert (
        '<meta name="apple-mobile-web-app-status-bar-style" content="default" />'
        in page
    )
    # Never "black-translucent": it lifts the web view under the bar and
    # the header loses its top.
    assert "black-translucent" not in page


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------


def test_the_pwa_routes_are_public_and_a_normal_route_is_not(
    client, app_root: Path, dist: Path
) -> None:
    """One app, no cookie, both answers.

    The install prompt is fetched before anybody has logged in, and a
    service worker that 401s never registers at all.
    """
    (dist / "sw.js").write_text("// worker", encoding="utf-8")
    (dist / "favicon.svg").write_bytes(b"<svg/>")

    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/sw.js").status_code == 200
    assert client.get("/apple-touch-icon").status_code == 200

    assert client.get("/private").status_code == 401


def test_the_manifest_route_renders_the_live_config(
    client, app_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per request from the config that is live now, like ``render_index``."""
    monkeypatch.setattr(chainlit.config.config.ui, "name", "Panda")
    monkeypatch.setattr(chainlit.config.config.run, "root_path", "/app")
    (app_root / "public" / "pwa-icon-192.png").write_bytes(b"small")

    response = client.get("/manifest.webmanifest")
    body = response.json()

    assert response.headers["content-type"].startswith("application/manifest+json")
    assert body["name"] == "Panda"
    assert body["start_url"] == "/app/"
    assert body["icons"][0]["src"] == "/app/public/pwa-icon-192.png"


def test_the_service_worker_is_served_from_the_root_without_caching(
    client, dist: Path
) -> None:
    """``no-cache``, because a cached worker is a stuck worker.

    The path matters as much as the header: a worker's default scope is the
    directory it came from, so one served under ``/public`` would control
    ``/public`` and nothing else.
    """
    (dist / "sw.js").write_text("// worker", encoding="utf-8")

    response = client.get("/sw.js")

    assert response.status_code == 200
    assert response.text == "// worker"
    assert response.headers["content-type"].startswith("text/javascript")
    assert "no-cache" in response.headers["cache-control"]


def test_the_served_worker_carries_this_release(client, dist: Path) -> None:
    """The stamp is what makes the update flow fire at all.

    A worker whose bytes match the installed copy is never reinstalled, so
    a release that changed only the assets around it would leave last
    release's worker serving last release's cache forever. Stamping the
    version into the cache name changes both: the bytes, which triggers the
    install, and the name, which lets the new worker's activate sweep
    recognise the old cache as stale.

    The version is read rather than written down -- a literal would go red
    on the next release bump for no reason.
    """
    (dist / "sw.js").write_text(
        f"const CACHE_NAME = 'chainlit-assets-{BUILD_PLACEHOLDER}';\n", encoding="utf-8"
    )

    body = client.get("/sw.js").text

    assert chainlit.version.__version__ in body
    assert BUILD_PLACEHOLDER not in body
    assert f"chainlit-assets-{chainlit.version.__version__}" in body


def test_a_worker_with_no_placeholder_is_served_unchanged(client, dist: Path) -> None:
    """The rewrite is a replacement, not a template pass over the file."""
    (dist / "sw.js").write_text("self.addEventListener('fetch', () => {});\n")

    assert client.get("/sw.js").text == "self.addEventListener('fetch', () => {});\n"


def test_a_build_without_a_service_worker_is_a_404(client, dist: Path) -> None:
    """404, not an invented body and not the 500 a stat over nothing gives."""
    response = client.get("/sw.js")

    assert response.status_code == 404


def test_the_apps_apple_touch_icon_wins_over_the_bundled_favicon(
    client, app_root: Path, dist: Path
) -> None:
    (dist / "favicon.svg").write_bytes(b"<svg/>")
    (app_root / "public" / "apple-touch-icon.png").write_bytes(b"the home screen icon")

    response = client.get("/apple-touch-icon")

    assert response.status_code == 200
    assert response.content == b"the home screen icon"
    assert response.headers["content-type"].startswith("image/png")


def test_without_an_app_icon_the_bundled_favicon_keeps_the_route_total(
    client, dist: Path
) -> None:
    (dist / "favicon.svg").write_bytes(b"<svg/>")

    response = client.get("/apple-touch-icon")

    assert response.status_code == 200
    assert response.content == b"<svg/>"
