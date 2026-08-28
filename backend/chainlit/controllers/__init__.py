"""The HTTP surface, as Litestar controllers.

Three modules, split by what they need rather than by URL prefix:

``auth``      login, logout, the OAuth entry points and callback, ``/user``.
``project``   threads, elements, feedback, actions, settings, health.
``files``     upload, download, and the branding images.

None of them is registered here. ``ChainlitPlugin`` mounts them, because
the two session-affine routes -- ``POST /project/action`` and
``POST /project/file`` -- reach a live websocket session through a
dependency the plugin binds, and a controller that mounted itself would
have to reach for that itself.

Each module states what it needs from that session as its own
``Protocol``, rather than importing the registry: the HTTP half of this
application does not depend on the transport half, and saying so in types
is what keeps that true.

The one thing defined here is :data:`FRONTEND_DIST`. The branding routes and
the plugin's static-files router both need it, and the plugin imports the
controllers -- so the package that is imported is where the constant lives,
and neither module has to recompute it.
"""

from pathlib import Path

__all__ = ("FRONTEND_DIST",)

#: The built React app inside the installed package, copied here by
#: ``pnpm build`` / the wheel build. Resolved, so a symlinked checkout serves
#: the same path the static-files router was handed.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
