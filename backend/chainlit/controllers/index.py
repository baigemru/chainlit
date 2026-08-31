"""The document that bootstraps the SPA, rendered for this deployment.

The built ``index.html`` is a shell with three placeholders. What goes into
them is the application's identity -- its name and description as the tab
title and the Open Graph tags a link preview reads, its favicon, its theme
variables from ``public/theme.json``, and whatever CSS or JS the config
points at. The shell itself never changes; the fill-in is per config, so it
is computed per request from the config that is live at that moment.

Placeholders, not a template engine: the frontend build owns the document,
and a Jinja pass over a Vite bundle would be a second build step for three
string replacements.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from chainlit._utils import is_path_inside

if TYPE_CHECKING:
    from chainlit.config import ChainlitConfig

__all__ = ["render_index"]

TAG_PLACEHOLDER = "<!-- TAG INJECTION PLACEHOLDER -->"
JS_PLACEHOLDER = "<!-- JS INJECTION PLACEHOLDER -->"
CSS_PLACEHOLDER = "<!-- CSS INJECTION PLACEHOLDER -->"
FONT_START = "<!-- FONT START -->"
FONT_END = "<!-- FONT END -->"

DEFAULT_META_URL = "https://github.com/Chainlit/chainlit"
DEFAULT_META_IMAGE_URL = (
    "https://chainlit-cloud.s3.eu-west-3.amazonaws.com/logo/chainlit_banner.png"
)


def _custom_theme(public_dir: Path) -> Optional[Dict[str, Any]]:
    theme_file = public_dir / "theme.json"
    if not is_path_inside(theme_file, public_dir) or not theme_file.is_file():
        return None
    loaded = json.loads(theme_file.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


def _escape(value: Any) -> str:
    """Attribute-safe text. The config is trusted; a stray quote is not."""
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
    )


def render_index(shell: str, config: "ChainlitConfig", public_dir: Path) -> str:
    """Fill the built shell in for this deployment."""
    ui = config.ui
    root_path = (config.run.root_path or "").rstrip("/")
    name = _escape(ui.name)
    description = _escape(ui.description)
    meta_url = _escape(ui.custom_meta_url or DEFAULT_META_URL)
    meta_image = _escape(ui.custom_meta_image_url or DEFAULT_META_IMAGE_URL)
    # iOS reads this once, at launch, and paints the standalone status bar
    # from it for the whole session: "black" gives white glyphs over a dark
    # bar, "default" dark glyphs over a light one. It lives here rather than
    # in the built shell because the theme it has to match is this
    # deployment's config, not the build's. Not "black-translucent" -- that
    # one moves the web view up under the bar and the header loses its top.
    status_bar = "black" if ui.default_theme == "dark" else "default"

    tags = (
        f"<title>{name}</title>\n"
        f'    <link rel="icon" href="{root_path}/favicon" />\n'
        # No ``{root_path}`` on these two, deliberately: the trailing block
        # below rewrites every ``href="/`` in the finished document, so a
        # pre-prefixed href would come out doubled (``/app/app/…``).
        '    <link rel="manifest" href="/manifest.webmanifest" />\n'
        '    <link rel="apple-touch-icon" href="/apple-touch-icon" />\n'
        # A meta carries no href, so the rewrite below cannot touch it.
        f'    <meta name="apple-mobile-web-app-status-bar-style" content="{status_bar}" />\n'
        f'    <meta name="description" content="{description}">\n'
        f'    <meta property="og:type" content="website">\n'
        f'    <meta property="og:title" content="{name}">\n'
        f'    <meta property="og:description" content="{description}">\n'
        f'    <meta property="og:image" content="{meta_image}">\n'
        f'    <meta property="og:url" content="{meta_url}">\n'
        f'    <meta property="og:root_path" content="{root_path}">'
    )

    theme = _custom_theme(public_dir)
    scripts = []
    if theme and theme.get("variables"):
        scripts.append(
            f"<script>window.theme = {json.dumps(theme['variables'])};</script>"
        )
    if ui.custom_js:
        scripts.append(
            f'<script src="{_escape(ui.custom_js)}" {ui.custom_js_attributes or ""}></script>'
        )

    css = ""
    if ui.custom_css:
        css = (
            f'<link rel="stylesheet" type="text/css" href="{_escape(ui.custom_css)}" '
            f"{ui.custom_css_attributes or ''}>"
        )

    content = shell.replace(TAG_PLACEHOLDER, tags)
    content = content.replace(JS_PLACEHOLDER, "\n    ".join(scripts))
    content = content.replace(CSS_PLACEHOLDER, css)

    if theme and "custom_fonts" in theme:
        fonts = "\n".join(
            f'<link rel="stylesheet" href="{_escape(f)}">'
            for f in theme["custom_fonts"]
        )
        start = content.find(FONT_START)
        end = content.find(FONT_END)
        if start != -1 and end != -1:
            content = content[: start + len(FONT_START)] + fonts + content[end:]

    if root_path:
        # The bundle is built for "/"; a deployment under a prefix has to
        # see it in every absolute asset reference, or the app loads a
        # white page from the wrong origin path.
        content = content.replace('href="/', f'href="{root_path}/')
        content = content.replace('src="/', f'src="{root_path}/')
    return content
