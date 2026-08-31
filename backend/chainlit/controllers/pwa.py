"""What a browser needs before it will offer to install this app.

Three routes, and they are here together rather than folded into
``files`` or ``project`` because they answer one question between them --
"is this installable?" -- and a browser will only say yes if all three
agree. Splitting them by shape would put the manifest next to
``/project/settings``, the worker next to ``/favicon``, and the contract
between them nowhere.

The manifest is rendered per request from the live config, exactly as
``index`` renders the HTML shell, and for the same reason: name,
description and ``root_path`` are this deployment's, not the build's.

Two rules about ``root_path`` that pull in opposite directions:

* the **HTML** injected by :func:`chainlit.controllers.index.render_index`
  must *not* carry the prefix, because that function rewrites every
  ``href="/`` in the finished document;
* the **manifest body** must carry it explicitly, because nothing rewrites
  JSON. ``start_url``, ``scope`` and every icon ``src`` below embed it.

``/sw.js`` is served from the root and not from ``/public``: a service
worker's default scope is the directory it was served from, so a worker at
``/public/sw.js`` could only ever control ``/public/*`` -- which is the one
part of the app that does not need it.

All three are public. The manifest is fetched by the browser's install
flow, which carries no cookie, and a worker that 401s is a worker that never
registers.
"""

from __future__ import annotations

import glob
import mimetypes
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from litestar import Controller, get
from litestar.exceptions import NotFoundException
from litestar.response import File, Response

import chainlit.config
import chainlit.version
from chainlit.controllers import FRONTEND_DIST
from chainlit.controllers.files import favicon_file, public_dir, served_file

if TYPE_CHECKING:
    from chainlit.config import ChainlitConfig

__all__ = (
    "BUILD_PLACEHOLDER",
    "MANIFEST_MEDIA_TYPE",
    "PwaController",
    "apple_touch_icon_file",
    "manifest_icons",
    "render_manifest",
    "service_worker_file",
)

#: The media type the spec gives the manifest. Litestar serialises a dict
#: for anything matching ``application/…+json``, so this needs no encoder.
MANIFEST_MEDIA_TYPE = "application/manifest+json"

# ``pwa-icon-192.png``, ``pwa-icon-maskable-512.png``. The size is the one
# run of digits in the name; ``maskable`` anywhere in it marks the purpose.
# A file that matches the glob but carries no size is skipped rather than
# guessed at -- a stray ``pwa-icon-old.png`` in an app's public directory
# must not turn the manifest into a 500.
ICON_GLOB = "pwa-icon-*.png"
ICON_SIZE = re.compile(r"(?:^|-)(\d+)(?:-|$)")

#: The token the frontend build leaves in ``sw.js`` for the release to fill
#: in. Kept as a constant so the two halves -- the string the worker ships
#: and the string this route rewrites -- are named in one place.
BUILD_PLACEHOLDER = "__CHAINLIT_BUILD__"

# The splash the OS paints before the app has booted, one colour per theme.
# The dark value is ``--background`` from the ``.dark`` block of
# ``frontend/src/index.css`` (``0 0% 13%`` -> hsl(0, 0%, 13%) -> #212121);
# the light one is the same token from ``:root`` (``0 0% 100%``). When that
# palette moves, these move with it.
THEME_COLORS = {
    "dark": "#212121",
    "light": "#ffffff",
}


def normalized_root(config: "ChainlitConfig") -> str:
    """The deployment prefix, without its trailing slash. May be ``""``."""
    return (config.run.root_path or "").rstrip("/")


def manifest_icons(root_path: str, public: Path) -> List[Dict[str, Any]]:
    """The app's own PWA icons, or the one the bundle can already serve.

    Discovered off disk rather than configured: an app that ships icons
    should not also have to list them, and one that ships none still needs
    a non-empty ``icons`` array -- an empty one is not a valid manifest.

    The fallback points at ``/favicon`` and keeps the manifest *valid*; it
    does not make the app installable. Chrome's install prompt wants a
    192px and a 512px raster icon, and neither an SVG declared ``sizes:
    "any"`` nor a favicon of some other format counts towards that. An app
    that wants the prompt ships ``public/pwa-icon-192.png`` and
    ``public/pwa-icon-512.png``; until it does, the browser reads the
    manifest without complaint and simply never offers the install.
    """
    icons: List[Dict[str, Any]] = []
    for found in sorted(glob.glob(str(public / ICON_GLOB))):
        name = Path(found).name
        size = ICON_SIZE.search(Path(name).stem)
        if size is None:
            continue
        icon: Dict[str, Any] = {
            "src": f"{root_path}/public/{name}",
            "type": "image/png",
            "sizes": f"{size.group(1)}x{size.group(1)}",
        }
        if "maskable" in name:
            icon["purpose"] = "maskable"
        icons.append(icon)

    if icons:
        return icons

    # ``favicon_file()`` returns the first ``public/favicon.*`` there is, in
    # whatever format the app drew it -- so the type is guessed from that
    # name rather than asserted. A guess of ``None`` means no ``type`` key
    # at all: it is optional, and a wrong one is worse than a missing one.
    # ``sizes: "any"`` is the SVG spelling for "scales to anything" and is a
    # lie about a PNG, which has exactly one size we have not measured.
    # ``favicon_file()`` resolves its own directory off ``APP_ROOT`` rather
    # than reading ``public`` -- the route hands this the same directory, and
    # a test that hands it another has to set ``APP_ROOT`` to match.
    fallback: Dict[str, Any] = {"src": f"{root_path}/favicon"}
    guessed, _ = mimetypes.guess_type(favicon_file().name)
    if guessed is not None:
        fallback["type"] = guessed
    if guessed == "image/svg+xml":
        fallback["sizes"] = "any"
    return [fallback]


def render_manifest(config: "ChainlitConfig", public: Path) -> Dict[str, Any]:
    """The web app manifest for this deployment.

    Still no ``theme.json`` read -- a per-app palette would be one more file
    to stat per install fetch -- but the colours do follow
    ``ui.default_theme``, which defaults to dark. They are not a frame of
    splash screen: ``theme_color`` is the Android status bar for the whole
    session, so a white one over a dark app is a white bar until the user
    closes it, and ``background_color`` is what the OS paints while the
    bundle boots. See :data:`THEME_COLORS` for where the two hexes come
    from.
    """
    root_path = normalized_root(config)
    scope = f"{root_path}/"
    color = THEME_COLORS["dark" if config.ui.default_theme == "dark" else "light"]
    return {
        "name": config.ui.name,
        # ~12 characters is the home-screen launcher's budget; ``name`` is
        # written for a title bar and often overruns it.
        "short_name": config.ui.short_name or config.ui.name,
        "description": config.ui.description,
        "start_url": scope,
        "scope": scope,
        "display": "standalone",
        "background_color": color,
        "theme_color": color,
        "icons": manifest_icons(root_path, public),
    }


def service_worker_file() -> Path:
    """The worker the frontend build put at the root of ``dist``."""
    return FRONTEND_DIST / "sw.js"


def apple_touch_icon_file() -> Optional[Path]:
    """The app's home-screen icon for iOS, or the bundled favicon.

    iOS reads no manifest icon for the home screen; it wants this link and
    it wants a PNG. The SVG fallback keeps the route total -- Safari
    ignores what it cannot use, and the alternative is a 404 in every
    deployment that has not drawn one yet.
    """
    candidate = public_dir() / "apple-touch-icon.png"
    if candidate.is_file():
        return candidate
    fallback = FRONTEND_DIST / "favicon.svg"
    return fallback if fallback.is_file() else None


class PwaController(Controller):
    """The manifest, the service worker, and the iOS home-screen icon."""

    path = "/"

    @get(
        "/manifest.webmanifest",
        media_type=MANIFEST_MEDIA_TYPE,
        opt={"exclude_from_auth": True},
    )
    async def get_manifest(self) -> Dict[str, Any]:
        """The install descriptor, from the config that is live now."""
        return render_manifest(chainlit.config.config, public_dir())

    @get("/sw.js", opt={"exclude_from_auth": True})
    async def get_service_worker(self) -> Response[str]:
        """The service worker, from the root so its scope is the app.

        Read and rewritten rather than streamed, because the release
        version has to reach the worker's source and there is nowhere else
        to put it. The frontend build ships ``__CHAINLIT_BUILD__`` inside
        its cache name; this stamps the running version over it, and that
        does two things at once. The worker's *bytes* differ from the ones
        the browser installed last release, which is the only signal that
        starts the update flow -- a byte-identical worker is never
        reinstalled, no matter what the assets around it did. And the cache
        name the new worker computes differs from the old one, so its
        activate-time sweep can recognise the previous release's cache as
        stale and delete it. A static worker gets neither: it would keep
        serving last release's bundle out of a cache nothing retires.

        ``no-cache`` because a cached worker is a stuck one: the browser
        would keep re-registering the copy it already has, and a shipped
        fix would reach nobody until the entry expired.

        The existence check is explicit and comes first. Reading a missing
        path raises, which is a 500 -- and a 500 here would read to a
        browser as "the worker is broken", not "there is no worker", which
        is what a build without one means.
        """
        path = service_worker_file()
        if not path.is_file():
            raise NotFoundException("No service worker in this build")
        source = path.read_text(encoding="utf-8")
        return Response(
            source.replace(BUILD_PLACEHOLDER, chainlit.version.__version__),
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @get("/apple-touch-icon", opt={"exclude_from_auth": True})
    async def get_apple_touch_icon(self) -> File:
        """The home-screen icon Safari asks for by name."""
        path = apple_touch_icon_file()
        if path is None:
            raise NotFoundException("No apple touch icon")
        return served_file(path)
