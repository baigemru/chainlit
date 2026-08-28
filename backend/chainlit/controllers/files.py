"""Uploads, session downloads and the branding assets.

Two very different jobs share this module because they share a shape: every
route here answers with a *file*, and none of them touches the database.

The upload and download routes are **session-affine**. They act on a live
websocket session — its spool directory, its per-ask file specs, the user it
belongs to — and none of that is persisted anywhere a handler could read it
back. The live registry is another package's; this module only says what it
needs from it, as :class:`SessionRegistry` and :class:`LiveSession`, and takes
it as the ``sessions`` dependency. The application binds the real one.

The branding routes (``/favicon``, ``/logo``, ``/avatars``) are public. They
are fetched by the login page, so putting them behind the authentication
middleware would render an unbranded, half-broken login screen to exactly the
users who cannot get past it. ``exclude_from_auth`` is an *opt key*, and on an
excluded route ``connection.user`` raises rather than returning ``None`` (see
:data:`chainlit.security.AuthedRequest`) — so nothing below reads the user on
one.

A session that is not the caller's is a ``404``, not a ``401``: the rule is
:func:`chainlit.controllers.caller.assert_session_owner`, and the reason is
written there.
"""

from __future__ import annotations

import fnmatch
import glob
import mimetypes
import re
from pathlib import Path
from typing import (
    Any,
    Literal,
    Mapping,
    Optional,
    Tuple,
)

from litestar import Controller, get, post
from litestar.datastructures import UploadFile
from litestar.di import NamedDependency
from litestar.exceptions import ClientException, NotFoundException
from litestar.params import FromPath, FromQuery, MultipartBody
from litestar.response import File

import chainlit.config
from chainlit._utils import is_path_inside
from chainlit.controllers import FRONTEND_DIST
from chainlit.controllers.caller import assert_session_owner
from chainlit.controllers.sessions import SessionRegistry
from chainlit.security import AuthedRequest

__all__ = (
    "FilesController",
    "served_file",
)

# The avatar id is interpolated into a glob against a public directory, so it
# is validated before it is used, not after.
AVATAR_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_ .-]+$")

Theme = Literal["light", "dark"]


def served_file(path: Path, media_type: Optional[str] = None) -> File:
    """Stream a file off disk, without reading it into memory first.

    ``inline``, not the ``attachment`` Litestar defaults to: everything
    served here — an avatar, a logo, the image an element renders — is drawn
    *in* the page, and an attachment disposition turns every one of them into
    a download prompt.
    """
    if media_type is None:
        media_type, _ = mimetypes.guess_type(str(path))
    return File(path=path, media_type=media_type, content_disposition_type="inline")


def public_dir() -> Path:
    """The app's own ``public/`` directory, resolved at call time.

    ``APP_ROOT`` is read per request rather than captured at import: it is an
    environment variable, and a test — or an embedded host — that changes it
    should not have to have imported this module afterwards.
    """
    return Path(chainlit.config.APP_ROOT) / "public"


def favicon_file() -> Path:
    """The app's custom favicon, or the one shipped in the bundle."""
    if files := sorted(glob.glob(str(public_dir() / "favicon.*"))):
        return Path(files[0])
    return FRONTEND_DIST / "favicon.svg"


def logo_file(theme: str) -> Path:
    """The app's custom logo for this theme, or the bundled one."""
    for pattern in (
        public_dir() / f"logo_{theme}.*",
        FRONTEND_DIST / "assets" / f"logo_{theme}*.*",
    ):
        if files := sorted(glob.glob(str(pattern))):
            return Path(files[0])
    return FRONTEND_DIST / f"logo_{theme}.svg"


def upload_limits(spec: Optional[Any]) -> Tuple[Any, Optional[float], bool]:
    """What this upload is allowed to be: ``(accept, max_size_mb, enabled)``.

    An ``ask`` carries its own spec and answers entirely for itself. Without
    one the answer comes from ``[features.spontaneous_file_upload]``, whose
    absence has always meant "no restrictions at all".
    """
    if spec is not None:
        return getattr(spec, "accept", None), getattr(spec, "max_size_mb", None), True

    feature = getattr(chainlit.config.config.features, "spontaneous_file_upload", None)
    if feature is None:
        return None, None, True
    return (
        getattr(feature, "accept", None),
        getattr(feature, "max_size_mb", None),
        bool(getattr(feature, "enabled", False)),
    )


def mime_is_allowed(accept: Any, content_type: str, filename: str) -> bool:
    """Whether ``accept`` admits this file.

    A list of glob patterns matches on the mime type alone. A dict maps a
    mime pattern to the extensions allowed under it, and an empty list of
    extensions means "any extension with this mime type".
    """
    if accept is None:
        return True
    if isinstance(accept, list):
        return any(fnmatch.fnmatch(content_type, pattern) for pattern in accept)
    if isinstance(accept, dict):
        for pattern, extensions in accept.items():
            if not fnmatch.fnmatch(content_type, pattern):
                continue
            if not extensions:
                return True
            lowered = filename.lower()
            if any(lowered.endswith(str(ext).lower()) for ext in extensions):
                return True
        return False
    raise ValueError(
        "Invalid configuration for spontaneous_file_upload: "
        "accept must be a list or a dict"
    )


def validate_upload(
    *, filename: str, content_type: str, size: int, spec: Optional[Any]
) -> None:
    """Refuse an upload the app did not ask for, or asked for differently.

    Raises ``ValueError``; the route turns that into a 400. Size is the
    number of bytes actually received, never an advertised one — Litestar's
    ``UploadFile`` carries no ``size`` attribute at all, so reading one would
    have quietly turned this into a no-op while the mime check went on
    passing.
    """
    accept, max_size_mb, enabled = upload_limits(spec)
    if not enabled:
        raise ValueError("File upload is not enabled")
    if not mime_is_allowed(accept, content_type, filename):
        raise ValueError("File type not allowed")
    if max_size_mb is not None and size > max_size_mb * 1024 * 1024:
        raise ValueError("File size too large")


class FilesController(Controller):
    """Uploads, session downloads, and the branding assets."""

    path = "/"

    @post("/project/file", status_code=200)
    async def upload_file(
        self,
        request: AuthedRequest,
        data: MultipartBody[UploadFile],
        sessions: NamedDependency[SessionRegistry],
        session_id: FromQuery[str],
        ask_parent_id: FromQuery[Optional[str]] = None,
    ) -> Mapping[str, Any]:
        """Spool an uploaded file into the session's directory.

        ``200``, not the ``201`` Litestar gives a POST by default: the
        uploader is an ``XMLHttpRequest`` that compares ``status`` to 200
        exactly, so the created-status default would read as a failed upload
        on the client while the file sat safely on disk.
        """
        session = sessions.find(session_id)
        if session is None:
            raise NotFoundException("Session not found")
        assert_session_owner(session, request)

        spec = session.files_spec.get(ask_parent_id) if ask_parent_id else None
        if ask_parent_id and spec is None:
            raise NotFoundException("Parent message not found")

        filename = data.filename
        content_type = data.content_type
        if not filename or not content_type:
            raise ClientException("The uploaded file has no name or no content type")

        content = await data.read()
        try:
            validate_upload(
                filename=filename,
                content_type=content_type,
                size=len(content),
                spec=spec,
            )
        except ValueError as error:
            raise ClientException(str(error)) from error

        session.files_dir.mkdir(parents=True, exist_ok=True)
        return await session.persist_file(
            name=filename, content=content, mime=content_type
        )

    @get("/project/file/{file_id:str}")
    async def get_file(
        self,
        request: AuthedRequest,
        sessions: NamedDependency[SessionRegistry],
        file_id: FromPath[str],
        session_id: FromQuery[str],
    ) -> File:
        """Serve a file back out of the session that uploaded it."""
        session = sessions.find(session_id)
        if session is None:
            raise NotFoundException("Session not found")
        assert_session_owner(session, request)

        entry = session.files.get(file_id)
        if entry is None:
            raise NotFoundException("File not found")

        return served_file(Path(entry["path"]), entry.get("type"))

    @get("/favicon", opt={"exclude_from_auth": True})
    async def get_favicon(self) -> File:
        """The favicon, custom if the app ships one."""
        return served_file(favicon_file())

    @get("/logo", opt={"exclude_from_auth": True})
    async def get_logo(self, theme: FromQuery[Theme] = "light") -> File:
        """The logo for a theme, custom if the app ships one."""
        return served_file(logo_file(theme))

    @get("/avatars/{avatar_id:str}", opt={"exclude_from_auth": True})
    async def get_avatar(self, avatar_id: FromPath[str]) -> File:
        """An author's avatar, falling back to the favicon.

        The id is interpolated into a glob under ``public/avatars``, so it is
        checked against a whitelist pattern first, and the resolved path is
        then checked to be inside that directory anyway, because a glob is
        not a promise.
        """
        if not AVATAR_ID_PATTERN.match(avatar_id):
            raise ClientException("Invalid avatar_id")

        name = avatar_id
        if name == "default":
            name = chainlit.config.config.ui.name
        name = name.strip().lower().replace(" ", "_").replace(".", "_")

        base = public_dir() / "avatars"
        if (match := next(iter(sorted(base.glob(f"{name}.*"))), None)) is not None:
            if not is_path_inside(match, base):
                raise ClientException("Invalid filename")
            return served_file(match)

        return served_file(favicon_file())
