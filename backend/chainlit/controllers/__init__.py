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
"""
