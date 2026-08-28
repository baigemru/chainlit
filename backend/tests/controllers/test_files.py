"""What the upload, download and branding routes actually enforce.

No database anywhere in this file: none of these routes touches one. What
they do touch is a live websocket session, which is another package's, so the
seam ``chainlit.controllers.files`` declares — ``SessionRegistry`` and
``LiveSession`` — is what the fixtures below implement. A stub is the right
double here: the questions are all about the *route*, and a real registry
would answer none of them differently.

The authentication middleware is installed in every client. It has to be: a
test that asserts ``/favicon`` is public in an app with no authentication is
asserting nothing at all, and neither is one that checks a session belongs to
the caller when there is no caller.
"""

import os
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional

import pytest
from litestar.di import Provide
from litestar.testing import create_test_client

import chainlit.config
from chainlit.controllers.files import FilesController
from chainlit.security import chainlit_auth

# Long enough that PyJWT does not warn about it, which pytest turns into an
# error under this repo's -W settings.
TEST_SECRET = "a-test-secret-that-is-long-enough-for-hs256"

ALICE = "alice@example.com"
BOB = "bob@example.com"


class Spec:
    """An ``ask``'s upload constraints, as the seam describes them."""

    def __init__(self, accept: Any = None, max_size_mb: Optional[float] = None) -> None:
        self.accept = accept
        self.max_size_mb = max_size_mb


class Identity:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier


class StubSession:
    """A ``LiveSession``: the five members the file routes read."""

    def __init__(
        self,
        files_dir: Path,
        user: Optional[Identity] = None,
        files_spec: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self.user = user
        self.files_dir = files_dir
        self.files_spec = files_spec or {}
        self.files = files or {}
        self.persisted: list[Dict[str, Any]] = []

    async def persist_file(
        self, name: str, mime: str, content: bytes
    ) -> Mapping[str, Any]:
        self.files_dir.mkdir(parents=True, exist_ok=True)
        path = self.files_dir / name
        path.write_bytes(content)
        self.persisted.append({"name": name, "mime": mime, "size": len(content)})
        return {"id": name}


class StubRegistry:
    """A ``SessionRegistry`` over a plain dict."""

    def __init__(self, sessions: Optional[Dict[str, StubSession]] = None) -> None:
        self.sessions = sessions or {}

    def get(self, session_id: str) -> Optional[StubSession]:
        return self.sessions.get(session_id)


@pytest.fixture
def auth():
    return chainlit_auth(token_secret=TEST_SECRET)


@pytest.fixture
def app_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty app directory, so ``public/`` is whatever a test puts there."""
    root = tmp_path / "app"
    (root / "public").mkdir(parents=True)
    monkeypatch.setattr(chainlit.config, "APP_ROOT", str(root))
    return root


@pytest.fixture
def registry(tmp_path: Path) -> StubRegistry:
    """Alice's session and Bob's, each with its own spool directory."""
    return StubRegistry(
        {
            "alice-session": StubSession(
                files_dir=tmp_path / "alice",
                user=Identity(ALICE),
                files_spec={"ask-1": Spec(max_size_mb=1)},
            ),
            "bob-session": StubSession(files_dir=tmp_path / "bob", user=Identity(BOB)),
        }
    )


@pytest.fixture
def client(auth, registry: StubRegistry, app_root: Path) -> Iterator[Any]:
    with create_test_client(
        route_handlers=[FilesController],
        dependencies={"sessions": Provide(lambda: registry, sync_to_thread=False)},
        on_app_init=[auth.on_app_init],
    ) as test_client:
        yield test_client


def login(client: Any, auth: Any, identifier: str) -> None:
    client.cookies.set(auth.key, auth.create_token(identifier))


def upload(client: Any, session_id: str, content: bytes = b"hello", **query: Any):
    params = "".join(f"&{key}={value}" for key, value in query.items())
    return client.post(
        f"/project/file?session_id={session_id}{params}",
        files={"file": ("note.txt", content, "text/plain")},
    )


# --------------------------------------------------------------------------
# Uploads
# --------------------------------------------------------------------------


def test_an_upload_lands_in_the_session_that_asked_for_it(
    client, auth, registry: StubRegistry
) -> None:
    login(client, auth, ALICE)
    response = upload(client, "alice-session", b"the file body")

    # 200, not the 201 a POST defaults to in Litestar: the uploader is an
    # XMLHttpRequest comparing status to 200 exactly.
    assert response.status_code == 200
    assert response.json() == {"id": "note.txt"}
    session = registry.sessions["alice-session"]
    assert session.persisted == [
        {"name": "note.txt", "mime": "text/plain", "size": len(b"the file body")}
    ]
    assert (session.files_dir / "note.txt").read_bytes() == b"the file body"


def test_an_upload_to_an_unknown_session_is_refused(client, auth) -> None:
    login(client, auth, ALICE)
    assert upload(client, "no-such-session").status_code == 404


def test_an_upload_to_another_users_session_is_refused(
    client, auth, registry: StubRegistry
) -> None:
    """The one that matters. Bob's session id is not a capability.

    Session ids travel in a query string, so they end up in logs, referrers
    and shared URLs. Without this check anyone holding one can write files
    into somebody else's conversation.
    """
    login(client, auth, ALICE)
    response = upload(client, "bob-session")

    assert response.status_code == 401
    assert registry.sessions["bob-session"].persisted == []


def test_an_upload_over_the_asks_size_limit_is_refused(
    client, auth, registry: StubRegistry
) -> None:
    login(client, auth, ALICE)
    response = upload(
        client, "alice-session", b"x" * (1024 * 1024 + 1), ask_parent_id="ask-1"
    )

    assert response.status_code == 400
    assert "size" in response.json()["detail"].lower()
    assert registry.sessions["alice-session"].persisted == []


def test_an_upload_inside_the_asks_size_limit_is_accepted(
    client, auth, registry: StubRegistry
) -> None:
    """The other half: the limit is a limit, not a ban."""
    login(client, auth, ALICE)
    response = upload(client, "alice-session", b"x" * 1024, ask_parent_id="ask-1")

    assert response.status_code == 200
    assert registry.sessions["alice-session"].persisted[0]["size"] == 1024


def test_an_upload_naming_an_ask_that_is_not_pending_is_refused(client, auth) -> None:
    login(client, auth, ALICE)
    response = upload(client, "alice-session", ask_parent_id="ask-never-asked")

    assert response.status_code == 404


def test_a_mime_type_the_ask_did_not_accept_is_refused(
    client, auth, registry: StubRegistry
) -> None:
    registry.sessions["alice-session"].files_spec["ask-2"] = Spec(accept=["image/*"])
    login(client, auth, ALICE)
    response = upload(client, "alice-session", b"hello", ask_parent_id="ask-2")

    assert response.status_code == 400
    assert "type" in response.json()["detail"].lower()


def test_the_spontaneous_upload_switch_is_obeyed(
    client, auth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no ask, the answer comes from the feature flag."""
    monkeypatch.setattr(
        chainlit.config.config.features,
        "spontaneous_file_upload",
        Spec(accept=None),
        raising=False,
    )
    # Spec has no ``enabled``; ``upload_limits`` reads it as switched off.
    login(client, auth, ALICE)
    assert upload(client, "alice-session").status_code == 400


# --------------------------------------------------------------------------
# Downloads
# --------------------------------------------------------------------------


@pytest.fixture
def stored(tmp_path: Path, registry: StubRegistry) -> Path:
    path = tmp_path / "stored.txt"
    path.write_bytes(b"stored bytes")
    registry.sessions["alice-session"].files["file-1"] = {
        "path": path,
        "type": "text/plain",
    }
    return path


def test_a_stored_file_is_served_inline(client, auth, stored: Path) -> None:
    login(client, auth, ALICE)
    response = client.get("/project/file/file-1?session_id=alice-session")

    assert response.status_code == 200
    assert response.content == b"stored bytes"
    # Inline: these are the images and PDFs the chat renders in place.
    assert "inline" in response.headers["content-disposition"]


def test_a_stored_file_is_not_served_to_another_user(
    client, auth, stored: Path
) -> None:
    login(client, auth, BOB)
    response = client.get("/project/file/file-1?session_id=alice-session")

    assert response.status_code == 401
    assert b"stored bytes" not in response.content


def test_an_unknown_file_id_is_a_404(client, auth, stored: Path) -> None:
    login(client, auth, ALICE)
    assert (
        client.get("/project/file/file-2?session_id=alice-session").status_code == 404
    )


# --------------------------------------------------------------------------
# Branding
# --------------------------------------------------------------------------


def test_the_branding_routes_are_public_and_the_session_routes_are_not(
    client,
) -> None:
    """One app, no cookie, both answers.

    Split in two this proves nothing: "public" only means something next to a
    route that refuses the same anonymous caller.
    """
    assert client.get("/favicon").status_code == 200
    assert client.get("/logo?theme=dark").status_code == 200
    assert client.get("/avatars/somebody").status_code == 200

    assert (
        client.get("/project/file/file-1?session_id=alice-session").status_code == 401
    )
    assert upload(client, "alice-session").status_code == 401


def test_the_app_favicon_wins_over_the_bundled_one(client, app_root: Path) -> None:
    (app_root / "public" / "favicon.png").write_bytes(b"custom favicon")
    response = client.get("/favicon")

    assert response.status_code == 200
    assert response.content == b"custom favicon"
    assert response.headers["content-type"].startswith("image/png")


def test_the_app_logo_wins_over_the_bundled_one(client, app_root: Path) -> None:
    (app_root / "public" / "logo_dark.png").write_bytes(b"custom dark logo")

    assert client.get("/logo?theme=dark").content == b"custom dark logo"
    # The light theme has no custom file, so it falls through to the bundle.
    assert client.get("/logo?theme=light").content != b"custom dark logo"


def test_an_unknown_theme_is_refused_at_the_signature(client) -> None:
    """``theme`` is a Literal, so a value outside it never reaches a glob."""
    assert client.get("/logo?theme=../../etc").status_code == 400


def test_an_avatar_is_served_from_the_apps_public_directory(
    client, app_root: Path
) -> None:
    avatars = app_root / "public" / "avatars"
    avatars.mkdir()
    (avatars / "my_bot.png").write_bytes(b"the bot avatar")

    assert client.get("/avatars/My Bot").content == b"the bot avatar"


def test_an_unknown_avatar_falls_back_to_the_favicon(client, app_root: Path) -> None:
    (app_root / "public" / "favicon.png").write_bytes(b"custom favicon")

    assert client.get("/avatars/nobody").content == b"custom favicon"


@pytest.mark.parametrize("avatar_id", ["..%2F..%2Fetc%2Fpasswd", "a%2Fb", "name%00"])
def test_an_avatar_id_that_is_not_a_name_is_refused(client, avatar_id: str) -> None:
    """The id is interpolated into a glob, so it is a whitelist or nothing."""
    response = client.get(f"/avatars/{avatar_id}")
    assert response.status_code in (400, 404)
    assert b"root:" not in response.content


def test_an_avatar_symlinked_out_of_the_public_directory_is_refused(
    client, app_root: Path, tmp_path: Path
) -> None:
    """A glob is not a promise: the match still has to be inside the base."""
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"not yours")
    avatars = app_root / "public" / "avatars"
    avatars.mkdir()
    os.symlink(secret, avatars / "leak.png")

    response = client.get("/avatars/leak")
    assert response.status_code == 400
    assert response.content != b"not yours"
