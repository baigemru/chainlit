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
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from litestar import Controller, get
from litestar.exceptions import NotFoundException
from litestar.response import File

import chainlit.config
from chainlit.controllers import FRONTEND_DIST
from chainlit.controllers.files import public_dir, served_file

if TYPE_CHECKING:
    from chainlit.config import ChainlitConfig

__all__ = (
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


def normalized_root(config: "ChainlitConfig") -> str:
    """The deployment prefix, without its trailing slash. May be ``""``."""
    return (config.run.root_path or "").rstrip("/")


def manifest_icons(root_path: str, public: Path) -> List[Dict[str, Any]]:
    """The app's own PWA icons, or the one the bundle can already serve.

    Discovered off disk rather than configured: an app that ships icons
    should not also have to list them, and one that ships none still needs
    a non-empty ``icons`` array or Chrome refuses the install prompt. The
    fallback points at ``/favicon``, which is an SVG -- accepted as a
    manifest icon, and the honest answer until real PNGs exist.
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
    return [
        {
            "src": f"{root_path}/favicon",
            "type": "image/svg+xml",
            "sizes": "any",
        }
    ]


def render_manifest(config: "ChainlitConfig", public: Path) -> Dict[str, Any]:
    """The web app manifest for this deployment.

    No theme.json read: the colours here are the splash screen the OS
    paints *before* the app has booted, and reading a per-app palette to
    guess at it buys a shade of white nobody sees for longer than a frame.
    """
    root_path = normalized_root(config)
    scope = f"{root_path}/"
    return {
        "name": config.ui.name,
        "short_name": config.ui.name,
        "description": config.ui.description,
        "start_url": scope,
        "scope": scope,
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#ffffff",
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
    async def get_service_worker(self) -> File:
        """The service worker, from the root so its scope is the app.

        ``no-cache`` because a cached worker is a stuck one: the browser
        would keep re-registering the copy it already has, and a shipped
        fix would reach nobody until the entry expired.

        The existence check is explicit and comes first. A ``File`` over a
        missing path fails when it stats, which is a 500 -- and a 500 here
        would read to a browser as "the worker is broken", not "there is
        no worker", which is what a build without one means.
        """
        path = service_worker_file()
        if not path.is_file():
            raise NotFoundException("No service worker in this build")
        return File(
            path=path,
            media_type="text/javascript",
            content_disposition_type="inline",
            headers={"Cache-Control": "no-cache"},
        )

    @get("/apple-touch-icon", opt={"exclude_from_auth": True})
    async def get_apple_touch_icon(self) -> File:
        """The home-screen icon Safari asks for by name."""
        path = apple_touch_icon_file()
        if path is None:
            raise NotFoundException("No apple touch icon")
        return served_file(path)
