"""The authentication routes, on Litestar.

The shape of these tests follows from two things the port changed. The
credential is now checked by middleware, so "is this route public" is a
property of the route rather than of a dependency and has to be asserted per
route. And a handler returns its response rather than mutating an injected
one, so every cookie assertion is made against the ``Set-Cookie`` headers of
the response the handler returned -- which is also the only way to tell a
denied login from a successful one, since both answer with a 302.

The data layer is faked rather than stood up: what these tests own is the
controller's contract with it (what is read, what is written, and that the
write is committed even though the response is a redirect). That the upsert
itself is durable is ``tests/persistence/test_user_upsert.py``.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

import jwt as pyjwt
import pytest
from litestar.di import Provide
from litestar.testing import create_test_client

from chainlit.config import config
from chainlit.oauth_providers import OAuthProvider
from chainlit.persistence.records import UserRecord
from chainlit.plugin import ChainlitPlugin
from chainlit.security import chainlit_auth
from chainlit.user import User

SECRET = "test-secret-not-a-real-one-but-long-enough-for-hs256"
COOKIE = "access_token"
STATE_COOKIE = "oauth_state"
PROVIDER_ID = "keycloak"


# --- doubles ----------------------------------------------------------------


class FakeProvider(OAuthProvider):
    """Keycloak, without Keycloak.

    Records what it was asked for so a test can assert the network call did
    *not* happen on the paths that must refuse before reaching it.
    """

    id = PROVIDER_ID
    env: List[str] = []
    registration_url = "https://idp.example.com/register"
    forgot_password_url = "https://idp.example.com/forgot"

    def __init__(
        self,
        *,
        direct_grant: bool = False,
        vk: bool = False,
        yandex: bool = False,
        registration: bool = False,
        raises: bool = False,
    ) -> None:
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.authorize_url = "https://idp.example.com/auth"
        self.authorize_params = {"scope": "openid"}
        self._direct_grant = direct_grant
        self._vk = vk
        self._yandex = yandex
        self._registration = registration
        self._raises = raises
        self.code_exchanges: List[Tuple[str, str]] = []
        self.password_calls: List[Tuple[str, str]] = []

    def is_configured(self) -> bool:
        return True

    def is_direct_grant_enabled(self) -> bool:
        return self._direct_grant

    def is_registration_button_enabled(self) -> bool:
        return self._registration

    def get_vk_idp_hint(self) -> Optional[str]:
        return "vk" if self._vk else None

    def is_vk_button_enabled(self) -> bool:
        return self._vk

    def get_yandex_idp_hint(self) -> Optional[str]:
        return "yandex" if self._yandex else None

    def is_yandex_button_enabled(self) -> bool:
        return self._yandex

    async def get_token(self, code: str, url: str) -> str:
        if self._raises:
            raise RuntimeError("the provider is down")
        self.code_exchanges.append((code, url))
        return "provider-token"

    async def get_token_with_password(self, username: str, password: str) -> str:
        self.password_calls.append((username, password))
        return "provider-token"

    async def get_user_info(self, token: str):
        return ({"email": "ada@example.com"}, User(identifier="ada@example.com"))


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session


class FakeUserService:
    """The two calls the controller makes, and the commit it must issue."""

    def __init__(self, stored: Optional[UserRecord] = None) -> None:
        self.session = FakeSession()
        self.repository = FakeRepository(self.session)
        self.stored = stored
        self.saves: List[Tuple[str, Dict[str, Any]]] = []
        self.reads: List[str] = []

    async def get_by_identifier(self, identifier: str) -> Optional[UserRecord]:
        self.reads.append(identifier)
        return self.stored

    async def save(
        self, identifier: str, metadata: Optional[Dict[str, Any]] = None
    ) -> UserRecord:
        self.saves.append((identifier, dict(metadata or {})))
        self.stored = UserRecord(
            id="row-id",
            identifier=identifier,
            created_at="2026-01-01T00:00:00Z",
            metadata=dict(metadata or {}),
        )
        return self.stored


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    """The routes read the environment at call time; freeze it per test."""
    for name in (
        "CHAINLIT_URL",
        "CHAINLIT_ROOT_PATH",
        "CHAINLIT_CUSTOM_AUTH",
        "CHAINLIT_FORGOT_PASSWORD_URL",
        "CHAINLIT_STATE_COOKIE_LIFETIME",
        "CHAINLIT_AUTH_COOKIE_NAME",
        "CHAINLIT_AUTH_COOKIE_PATH",
        "CHAINLIT_COOKIE_SAMESITE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHAINLIT_AUTH_SECRET", SECRET)


@pytest.fixture(autouse=True)
def _no_callbacks(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.code, "password_auth_callback", None, raising=False)
    monkeypatch.setattr(config.code, "oauth_callback", None, raising=False)
    monkeypatch.setattr(config.code, "on_logout", None, raising=False)
    monkeypatch.setattr(config.code, "header_auth_callback", None, raising=False)


def use_provider(monkeypatch: pytest.MonkeyPatch, provider: OAuthProvider) -> None:
    monkeypatch.setattr("chainlit.oauth_providers.providers", [provider])


def use_oauth_callback(monkeypatch: pytest.MonkeyPatch, callback) -> None:
    monkeypatch.setattr(config.code, "oauth_callback", callback, raising=False)


def client(user_service: Optional[FakeUserService] = None, **kwargs):
    """A test client for the controller alone, on the real auth middleware."""

    async def provide_fake() -> Any:
        return user_service

    # Bound at the application layer, not by subclassing the controller.
    # The plugin brings the routes now, and contributes its own bindings
    # with `setdefault`, so one passed here simply wins.
    return create_test_client(
        route_handlers=[],
        plugins=[ChainlitPlugin(auth=chainlit_auth(token_secret=SECRET))],
        dependencies={"user_service": Provide(provide_fake)},
        debug=False,
        **kwargs,
    )


def set_cookies(response) -> List[str]:
    return response.headers.get_list("set-cookie")


def issued(response, name: str = COOKIE) -> Optional[str]:
    """The value a ``Set-Cookie`` assigns, or None if it is not set at all.

    A deletion (``Max-Age=0``) reads as not issued -- which is the point: on
    every denial path the assertion is that no session was handed out.
    """
    for header in set_cookies(response):
        key, _, rest = header.partition("=")
        if key.strip() != name:
            continue
        if "Max-Age=0" in header:
            return None
        return rest.split(";", 1)[0]
    return None


def token_for(**claims) -> str:
    from datetime import UTC, datetime, timedelta

    payload = {
        "sub": "ada@example.com",
        "identifier": "ada@example.com",
        "display_name": "Ada",
        "metadata": {"role": "admin"},
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "iat": datetime.now(UTC),
    }
    payload.update(claims)
    return pyjwt.encode(payload, SECRET, algorithm="HS256")


# --- /auth/config -----------------------------------------------------------


def test_auth_config_lists_the_configured_provider(monkeypatch: pytest.MonkeyPatch):
    use_provider(monkeypatch, FakeProvider(vk=True, registration=True))
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        body = c.get("/auth/config").json()

    assert body["requireLogin"] is True
    assert body["oauthProviders"] == [PROVIDER_ID]
    assert body["oauthProviderDetails"][0]["id"] == PROVIDER_ID
    assert body["oauthProviderDetails"][0]["vkEnabled"] is True
    assert body["oauthProviderDetails"][0]["registrationEnabled"] is True
    assert body["ui"]["forgot_password_url"] == "https://idp.example.com/forgot"


def test_auth_config_hides_providers_when_no_oauth_callback_is_registered(
    monkeypatch: pytest.MonkeyPatch,
):
    """A provider with no ``@cl.oauth_callback`` cannot log anybody in."""
    use_provider(monkeypatch, FakeProvider())

    with client() as c:
        body = c.get("/auth/config").json()

    assert body["requireLogin"] is False
    assert body["oauthProviders"] == []
    assert body["oauthProviderDetails"] == []


def test_auth_config_advertises_password_auth_only_for_direct_grant(
    monkeypatch: pytest.MonkeyPatch,
):
    use_provider(monkeypatch, FakeProvider(direct_grant=False))
    use_oauth_callback(monkeypatch, lambda *a: None)
    with client() as c:
        assert c.get("/auth/config").json()["passwordAuth"] is False

    use_provider(monkeypatch, FakeProvider(direct_grant=True))
    with client() as c:
        assert c.get("/auth/config").json()["passwordAuth"] is True


def test_auth_config_never_advertises_header_auth(monkeypatch: pytest.MonkeyPatch):
    """The route is not ported, so the flag that would trigger it is off."""

    async def header_auth(headers):  # pragma: no cover - must not be reached
        return User(identifier="ada")

    monkeypatch.setattr(config.code, "header_auth_callback", header_auth)
    with client() as c:
        assert c.get("/auth/config").json()["headerAuth"] is False
        assert c.post("/auth/header", json={}).status_code == 404


def test_auth_config_is_public():
    """It is what the login page reads before it has any credential."""
    with client() as c:
        assert c.get("/auth/config").status_code == 200


# --- the oauth entry points -------------------------------------------------


def test_oauth_login_redirects_and_plants_the_state(monkeypatch: pytest.MonkeyPatch):
    use_provider(monkeypatch, FakeProvider())
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        response = c.get(f"/auth/oauth/{PROVIDER_ID}", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://idp.example.com/auth?")
    assert "client_id=client-id" in location
    assert "scope=openid" in location
    assert (
        "redirect_uri=http%3A%2F%2Ftestserver.local%2Fauth%2Foauth%2Fkeycloak%2Fcallback"
        in location
    )

    state = issued(response, STATE_COOKIE)
    assert state, "the callback has nothing to compare against without this"
    assert f"state={state}" in location


def test_oauth_login_forwards_a_login_hint(monkeypatch: pytest.MonkeyPatch):
    use_provider(monkeypatch, FakeProvider())
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        response = c.get(
            f"/auth/oauth/{PROVIDER_ID}?login_hint=ada%40example.com",
            follow_redirects=False,
        )

    assert "login_hint=ada%40example.com" in response.headers["location"]


@pytest.mark.parametrize(
    ("suffix", "kwargs", "expected"),
    [
        ("/vk", {"vk": True}, "kc_idp_hint=vk"),
        ("/yandex", {"yandex": True}, "kc_idp_hint=yandex"),
    ],
)
def test_the_per_idp_entry_points_return_to_the_shared_callback(
    monkeypatch: pytest.MonkeyPatch, suffix: str, kwargs: dict, expected: str
):
    use_provider(monkeypatch, FakeProvider(**kwargs))
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        response = c.get(f"/auth/oauth/{PROVIDER_ID}{suffix}", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert expected in location
    assert "keycloak%2Fcallback" in location
    assert suffix.strip("/") not in location.split("redirect_uri=")[1].split("&")[0]


def test_a_disabled_per_idp_entry_point_is_a_404(monkeypatch: pytest.MonkeyPatch):
    use_provider(monkeypatch, FakeProvider(vk=False))
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        assert c.get(f"/auth/oauth/{PROVIDER_ID}/vk").status_code == 404


def test_the_register_entry_point_uses_the_registration_url(
    monkeypatch: pytest.MonkeyPatch,
):
    use_provider(monkeypatch, FakeProvider(registration=True))
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        response = c.get(f"/auth/oauth/{PROVIDER_ID}/register", follow_redirects=False)

    assert response.headers["location"].startswith("https://idp.example.com/register?")
    assert "keycloak%2Fcallback" in response.headers["location"]


def test_an_unknown_provider_is_a_404(monkeypatch: pytest.MonkeyPatch):
    use_provider(monkeypatch, FakeProvider())
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        assert c.get("/auth/oauth/nope").status_code == 404


# --- the oauth callback -----------------------------------------------------


def _callback_url(state: str, code: str = "the-code") -> str:
    return f"/auth/oauth/{PROVIDER_ID}/callback?code={code}&state={state}"


def test_the_oauth_callback_issues_a_session(monkeypatch: pytest.MonkeyPatch):
    provider = FakeProvider()
    use_provider(monkeypatch, provider)
    seen = []

    async def callback(provider_id, token, raw, default_user):
        seen.append((provider_id, token, raw, default_user))
        return default_user

    use_oauth_callback(monkeypatch, callback)

    with client() as c:
        c.cookies.set(STATE_COOKIE, "the-state")
        response = c.get(_callback_url("the-state"), follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login/callback?success=True"
    assert issued(response), "the browser was not given a session"
    assert seen[0][0] == PROVIDER_ID
    assert seen[0][1] == "provider-token"
    assert provider.code_exchanges == [
        ("the-code", "http://testserver.local/auth/oauth/keycloak/callback")
    ]


def test_a_denying_oauth_callback_issues_no_session(monkeypatch: pytest.MonkeyPatch):
    """``None`` from ``@cl.oauth_callback`` must deny.

    Both this and the happy path answer 302, so the discriminating assertion
    is the absence of a session cookie, not the status.
    """
    use_provider(monkeypatch, FakeProvider())

    async def deny(provider_id, token, raw, default_user):
        return None

    use_oauth_callback(monkeypatch, deny)
    user_service = FakeUserService()

    with client(user_service) as c:
        c.cookies.set(STATE_COOKIE, "the-state")
        response = c.get(_callback_url("the-state"), follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=oauthSignin"
    assert issued(response) is None
    assert user_service.saves == [], "a denied login wrote a user row"


def test_the_oauth_callback_refuses_a_mismatched_state(
    monkeypatch: pytest.MonkeyPatch,
):
    """The CSRF check. Without it a forged callback logs the victim in."""
    provider = FakeProvider()
    use_provider(monkeypatch, provider)
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        c.cookies.set(STATE_COOKIE, "the-state-this-app-wrote")
        response = c.get(
            _callback_url("a-state-the-attacker-chose"), follow_redirects=False
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=oauthSignin"
    assert issued(response) is None
    assert provider.code_exchanges == [], "the code was exchanged before the check"


def test_the_oauth_callback_refuses_a_missing_state_cookie(
    monkeypatch: pytest.MonkeyPatch,
):
    """A callback arriving in a browser this app never sent to the provider."""
    provider = FakeProvider()
    use_provider(monkeypatch, provider)
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        response = c.get(_callback_url("any-state"), follow_redirects=False)

    assert response.headers["location"] == "/login?error=oauthSignin"
    assert issued(response) is None
    assert provider.code_exchanges == []


def test_the_oauth_callback_refuses_an_empty_state(monkeypatch: pytest.MonkeyPatch):
    """Empty against empty must not compare equal."""
    provider = FakeProvider()
    use_provider(monkeypatch, provider)
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        c.cookies.set(STATE_COOKIE, "")
        response = c.get(
            f"/auth/oauth/{PROVIDER_ID}/callback?code=the-code&state=",
            follow_redirects=False,
        )

    assert response.headers["location"] == "/login?error=oauthSignin"
    assert issued(response) is None
    assert provider.code_exchanges == []


def test_the_oauth_callback_reports_a_provider_error_without_calling_it(
    monkeypatch: pytest.MonkeyPatch,
):
    provider = FakeProvider()
    use_provider(monkeypatch, provider)
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        c.cookies.set(STATE_COOKIE, "the-state")
        response = c.get(
            f"/auth/oauth/{PROVIDER_ID}/callback?error=access_denied",
            follow_redirects=False,
        )

    assert response.headers["location"] == "/login?error=oauthSignin"
    assert issued(response) is None
    assert provider.code_exchanges == []


def test_a_failing_provider_does_not_500(monkeypatch: pytest.MonkeyPatch):
    """Slow or broken network in the callback is a login failure, not a crash."""
    use_provider(monkeypatch, FakeProvider(raises=True))
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        c.cookies.set(STATE_COOKIE, "the-state")
        response = c.get(_callback_url("the-state"), follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=oauthSignin"
    assert issued(response) is None


def test_the_oauth_callback_clears_the_state_it_consumed(
    monkeypatch: pytest.MonkeyPatch,
):
    """One state, one login: the cookie must not survive to be replayed."""
    use_provider(monkeypatch, FakeProvider())

    async def callback(provider_id, token, raw, default_user):
        return default_user

    use_oauth_callback(monkeypatch, callback)

    with client() as c:
        c.cookies.set(STATE_COOKIE, "the-state")
        response = c.get(_callback_url("the-state"), follow_redirects=False)

    assert any(
        header.startswith(f"{STATE_COOKIE}=") and "Max-Age=0" in header
        for header in set_cookies(response)
    )


# --- persisting the user ----------------------------------------------------


def test_the_login_persists_the_metadata_the_callback_wrote(
    monkeypatch: pytest.MonkeyPatch,
):
    use_provider(monkeypatch, FakeProvider())

    async def callback(provider_id, token, raw, default_user):
        return User(
            identifier="ada@example.com",
            display_name="Ada",
            metadata={"role": "admin", "tenant": "acme"},
        )

    use_oauth_callback(monkeypatch, callback)
    user_service = FakeUserService()

    with client(user_service) as c:
        c.cookies.set(STATE_COOKIE, "the-state")
        c.get(_callback_url("the-state"), follow_redirects=False)

    assert user_service.saves == [
        ("ada@example.com", {"role": "admin", "tenant": "acme"})
    ]


def test_the_login_does_not_commit_by_hand(monkeypatch: pytest.MonkeyPatch):
    """The redirect's commit is the config's job, and only the config's.

    The callback answers 302, and advanced_alchemy's plain ``autocommit``
    handler commits only inside ``range(200, 300)`` -- so this write does
    need a decision. That decision is
    ``before_send_handler="autocommit_include_redirects"`` in
    ``persistence/config.py``, pinned against a real database by
    ``test_a_redirecting_handler_still_commits``.

    Committing here as well would be a second mechanism for one guarantee:
    the two would drift, and the handler's would be the one nobody
    remembers to repeat in the next route that redirects after a write.
    """
    use_provider(monkeypatch, FakeProvider())

    async def callback(provider_id, token, raw, default_user):
        return default_user

    use_oauth_callback(monkeypatch, callback)
    user_service = FakeUserService()

    with client(user_service) as c:
        c.cookies.set(STATE_COOKIE, "the-state")
        c.get(_callback_url("the-state"), follow_redirects=False)

    assert user_service.saves, "the login did not write the user at all"
    assert user_service.session.commits == 0


def test_a_failing_data_layer_does_not_block_the_login(
    monkeypatch: pytest.MonkeyPatch,
):
    use_provider(monkeypatch, FakeProvider())

    async def callback(provider_id, token, raw, default_user):
        return default_user

    use_oauth_callback(monkeypatch, callback)

    class Broken(FakeUserService):
        async def save(self, identifier, metadata=None):
            raise RuntimeError("the database is down")

    with client(Broken()) as c:
        c.cookies.set(STATE_COOKIE, "the-state")
        response = c.get(_callback_url("the-state"), follow_redirects=False)

    assert issued(response), "an unwritable row must not cost the user a session"


# --- the cookie -------------------------------------------------------------


def _fat_metadata() -> Dict[str, Any]:
    """Enough claims to push the JWT past the 3000-character chunk size."""
    return {"groups": [f"group-{i:04d}" for i in range(400)]}


def test_a_large_token_is_chunked_and_reads_back(monkeypatch: pytest.MonkeyPatch):
    """The chunking is not an edge case: real IdP metadata blows past 4KB."""
    use_provider(monkeypatch, FakeProvider())

    async def callback(provider_id, token, raw, default_user):
        return User(identifier="ada@example.com", metadata=_fat_metadata())

    use_oauth_callback(monkeypatch, callback)

    with client() as c:
        c.cookies.set(STATE_COOKIE, "the-state")
        response = c.get(_callback_url("the-state"), follow_redirects=False)

        assert issued(response, f"{COOKIE}_0"), "the token was not chunked"
        assert issued(response, f"{COOKIE}_1"), "one chunk is not chunking"
        assert issued(response, COOKIE) is None, (
            "an unchunked cookie alongside the chunks would win and be truncated"
        )

        # The client keeps the cookies the response set; the reader in
        # chainlit.security has to put them back together.
        me = c.get("/user")

    assert me.status_code == 200
    assert me.json()["identifier"] == "ada@example.com"


def test_a_short_token_deletes_the_chunks_it_replaces(
    monkeypatch: pytest.MonkeyPatch,
):
    """Stale chunks are undeletable garbage that count against the jar limit."""
    use_provider(monkeypatch, FakeProvider())

    async def callback(provider_id, token, raw, default_user):
        return default_user

    use_oauth_callback(monkeypatch, callback)

    with client() as c:
        c.cookies.set(STATE_COOKIE, "the-state")
        c.cookies.set(f"{COOKIE}_0", "left-over-from-a-bigger-token")
        c.cookies.set(f"{COOKIE}_1", "and-its-second-half")
        response = c.get(_callback_url("the-state"), follow_redirects=False)

    assert issued(response), "the short token was not written"
    for stale in (f"{COOKIE}_0", f"{COOKIE}_1"):
        assert any(
            header.startswith(f"{stale}=") and "Max-Age=0" in header
            for header in set_cookies(response)
        ), f"{stale} was left behind"


def test_the_session_cookie_is_not_readable_from_javascript(
    monkeypatch: pytest.MonkeyPatch,
):
    use_provider(monkeypatch, FakeProvider())

    async def callback(provider_id, token, raw, default_user):
        return default_user

    use_oauth_callback(monkeypatch, callback)

    with client() as c:
        c.cookies.set(STATE_COOKIE, "the-state")
        response = c.get(_callback_url("the-state"), follow_redirects=False)

    header = next(h for h in set_cookies(response) if h.startswith(f"{COOKIE}="))
    assert "HttpOnly" in header


# --- /login (the direct grant) ----------------------------------------------


def _form() -> Dict[str, Tuple[None, str]]:
    """``FormData`` from the browser is multipart, which is what the route takes."""
    return {"username": (None, "ada@example.com"), "password": (None, "hunter2")}


def test_login_runs_the_direct_grant_through_the_oauth_callback(
    monkeypatch: pytest.MonkeyPatch,
):
    """The second entry shape: a password form against the provider.

    It is the same ``@cl.oauth_callback``, so an app that denies there denies
    both flows.
    """
    provider = FakeProvider(direct_grant=True)
    use_provider(monkeypatch, provider)
    seen = []

    async def callback(provider_id, token, raw, default_user):
        seen.append((provider_id, token))
        return default_user

    use_oauth_callback(monkeypatch, callback)

    with client() as c:
        response = c.post("/login", files=_form())

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert issued(response)
    assert provider.password_calls == [("ada@example.com", "hunter2")]
    assert seen == [(PROVIDER_ID, "provider-token")]


def test_login_denied_by_the_oauth_callback_is_a_401(
    monkeypatch: pytest.MonkeyPatch,
):
    use_provider(monkeypatch, FakeProvider(direct_grant=True))

    async def deny(provider_id, token, raw, default_user):
        return None

    use_oauth_callback(monkeypatch, deny)

    with client() as c:
        response = c.post("/login", files=_form())

    assert response.status_code == 401
    assert response.json()["detail"] == "credentialssignin"
    assert issued(response) is None


def test_login_prefers_an_explicit_password_callback(
    monkeypatch: pytest.MonkeyPatch,
):
    use_provider(monkeypatch, FakeProvider(direct_grant=True))
    use_oauth_callback(monkeypatch, lambda *a: None)

    async def password_auth(username, password):
        return User(identifier=username)

    monkeypatch.setattr(config.code, "password_auth_callback", password_auth)

    with client() as c:
        response = c.post("/login", files=_form())

    assert response.status_code == 200
    assert issued(response)


def test_login_without_any_callback_is_a_400():
    with client() as c:
        response = c.post("/login", files=_form())

    assert response.status_code == 400
    assert response.json()["detail"] == "No auth_callback defined"


# --- /auth/jwt --------------------------------------------------------------


def test_jwt_auth_exchanges_a_bearer_token_for_a_cookie():
    with client() as c:
        response = c.post(
            "/auth/jwt", headers={"Authorization": f"Bearer {token_for()}"}
        )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert issued(response)


def test_jwt_auth_refuses_a_token_signed_with_another_secret():
    other = pyjwt.encode(
        {"sub": "mallory", "identifier": "mallory", "exp": 9999999999},
        "a-different-secret-of-a-perfectly-adequate-length",
        algorithm="HS256",
    )
    with client() as c:
        response = c.post("/auth/jwt", headers={"Authorization": f"Bearer {other}"})

    assert response.status_code == 401
    assert issued(response) is None


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}],
    ids=["missing", "wrong-scheme", "no-token"],
)
def test_jwt_auth_refuses_a_malformed_header(headers: dict):
    with client() as c:
        response = c.post("/auth/jwt", headers=headers)

    assert response.status_code == 401
    assert issued(response) is None


# --- /user ------------------------------------------------------------------


def test_user_requires_a_credential():
    """The one route here the middleware still guards."""
    with client() as c:
        assert c.get("/user").status_code == 401


def test_user_answers_from_the_token_without_a_data_layer():
    with client() as c:
        c.cookies.set(COOKIE, token_for())
        body = c.get("/user").json()

    assert body["identifier"] == "ada@example.com"
    assert body["display_name"] == "Ada"
    assert body["metadata"] == {"role": "admin"}
    assert "id" not in body


def test_user_prefers_the_stored_metadata_over_the_token(
    monkeypatch: pytest.MonkeyPatch,
):
    """The token froze the metadata at login; the row has moved on since."""
    stored = UserRecord(
        id="row-id",
        identifier="ada@example.com",
        created_at="2026-01-01T00:00:00Z",
        metadata={"role": "owner"},
    )
    user_service = FakeUserService(stored)

    with client(user_service) as c:
        c.cookies.set(COOKIE, token_for())
        body = c.get("/user").json()

    assert body["id"] == "row-id"
    assert body["createdAt"] == "2026-01-01T00:00:00Z"
    assert body["metadata"] == {"role": "owner"}
    # Ephemeral, and carried only by the token.
    assert body["display_name"] == "Ada"
    assert user_service.saves == [], "reading the user rewrote the row"


def test_user_creates_the_row_when_it_is_missing():
    user_service = FakeUserService(stored=None)

    with client(user_service) as c:
        c.cookies.set(COOKIE, token_for())
        body = c.get("/user").json()

    assert user_service.saves == [("ada@example.com", {"role": "admin"})]
    assert body["id"] == "row-id"


# --- /logout ----------------------------------------------------------------


def test_logout_clears_every_chunk():
    with client() as c:
        c.cookies.set(f"{COOKIE}_0", "first")
        c.cookies.set(f"{COOKIE}_1", "second")
        response = c.post("/logout")

    assert response.status_code == 200
    for name in (f"{COOKIE}_0", f"{COOKIE}_1"):
        assert any(
            header.startswith(f"{name}=") and "Max-Age=0" in header
            for header in set_cookies(response)
        ), f"{name} survived the logout"


def test_logout_works_without_a_credential():
    """An expired cookie is exactly the one that most needs clearing."""
    with client() as c:
        response = c.post("/logout")

    assert response.status_code == 200
    assert any(header.startswith(f"{COOKIE}=") for header in set_cookies(response))


def test_logout_hands_the_response_to_the_on_logout_callback(
    monkeypatch: pytest.MonkeyPatch,
):
    seen = []

    async def on_logout(request, response):
        seen.append((request, response))
        return response

    monkeypatch.setattr(config.code, "on_logout", on_logout)

    with client() as c:
        c.cookies.set(COOKIE, "whatever")
        response = c.post("/logout")

    assert seen, "the callback was not called"
    assert any(
        header.startswith(f"{COOKIE}=") and "Max-Age=0" in header
        for header in set_cookies(response)
    )


# --- /set-session-cookie ----------------------------------------------------


def test_logout_clears_the_cookie_when_on_logout_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    """A callback used as a notification hook returns ``None``.

    The old stack merged the injected response's headers in whatever the
    callback returned; here the return value is the response, so a ``None``
    that replaced it would silently leave the browser logged in.
    """
    called = []

    async def on_logout(request, response):
        called.append(True)

    monkeypatch.setattr(config.code, "on_logout", on_logout)

    with client() as c:
        c.cookies.set(COOKIE, "whatever")
        response = c.post("/logout")

    assert called
    assert response.status_code == 200
    assert any(
        header.startswith(f"{COOKIE}=") and "Max-Age=0" in header
        for header in set_cookies(response)
    )


def test_set_session_cookie():
    with client() as c:
        response = c.post("/set-session-cookie", json={"session_id": "session-1"})

    assert response.status_code == 200
    assert response.json() == {"message": "Session cookie set"}
    header = next(
        h for h in set_cookies(response) if h.startswith("X-Chainlit-Session-id=")
    )
    assert "session-1" in header
    assert "HttpOnly" in header


def test_set_session_cookie_needs_a_session_id():
    with client() as c:
        assert c.post("/set-session-cookie", json={}).status_code == 400


# --- routing ----------------------------------------------------------------


def test_the_azure_hybrid_callback_is_post_only(monkeypatch: pytest.MonkeyPatch):
    """Its literal path shadows ``{provider_id}`` for every method it declares.

    The provider posts (``response_mode=form_post``), so this is only a
    change for a GET that never happens -- but it is a change, and it is
    asserted rather than discovered.
    """
    use_provider(monkeypatch, FakeProvider())
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        assert c.get("/auth/oauth/azure-ad-hybrid/callback").status_code == 405


def test_the_root_path_prefixes_the_redirects(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHAINLIT_ROOT_PATH", "/chat")
    use_provider(monkeypatch, FakeProvider())
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        response = c.get(_callback_url("nope"), follow_redirects=False)

    assert response.headers["location"] == "/chat/login?error=oauthSignin"


def test_chainlit_url_wins_for_the_redirect_uri(monkeypatch: pytest.MonkeyPatch):
    """Behind a proxy the internal URL is not what the provider was given."""
    monkeypatch.setenv("CHAINLIT_URL", "https://chat.example.com/")
    use_provider(monkeypatch, FakeProvider())
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        response = c.get(f"/auth/oauth/{PROVIDER_ID}", follow_redirects=False)

    assert (
        "redirect_uri=https%3A%2F%2Fchat.example.com%2Fauth%2Foauth%2Fkeycloak%2Fcallback"
        in response.headers["location"]
    )


def test_the_public_routes_do_not_need_a_credential(monkeypatch: pytest.MonkeyPatch):
    """Every one of these is reached by a browser that has no session yet."""
    use_provider(monkeypatch, FakeProvider(direct_grant=True))
    use_oauth_callback(monkeypatch, lambda *a: None)

    with client() as c:
        assert c.get("/auth/config").status_code != 401
        assert c.get(f"/auth/oauth/{PROVIDER_ID}").status_code != 401
        assert c.get(_callback_url("nope")).status_code != 401
        assert c.post("/logout").status_code != 401
        assert (
            c.post("/set-session-cookie", json={"session_id": "s"}).status_code != 401
        )


def test_os_environ_is_not_read_at_import_time():
    """The cookie settings are read per call, so a test can change them."""
    assert "CHAINLIT_AUTH_COOKIE_NAME" not in os.environ
