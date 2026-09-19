"""`/project/account`: who may ask, what a GET shows, what a PUT accepts.

In memory, no database. The store is a fake bound through the host app's own
`dependencies=` — the plugin binds `user_service` with `setdefault`, so a host
that has already named it keeps its own, and that is the seam these tests use.
"""

from typing import Annotated, Any, Dict, List, Literal, Optional

import msgspec
import pytest
from litestar.di import Provide
from litestar.testing import create_test_client
from msgspec import Meta

from chainlit.config import config
from chainlit.plugin import ChainlitPlugin
from chainlit.security import chainlit_auth

SECRET = "test-secret-not-a-real-one-but-long-enough-for-hs256"
COOKIE = "access_token"


class Calculation(msgspec.Struct):
    """Параметры расчёта"""

    margin: Annotated[float, Meta(ge=0, le=100, title="Маржа, %")] = 20
    currency: Annotated[Literal["USD", "CNY", "RUB"], Meta(title="Валюта")] = "USD"


class Account(msgspec.Struct):
    calculation: Annotated[Calculation, Meta(title="Расчёт")] = msgspec.field(
        default_factory=Calculation
    )
    notify: Annotated[bool, Meta(title="Уведомления")] = True
    plan: Annotated[str, Meta(title="Тариф", extra_json_schema={"readOnly": True})] = (
        "free"
    )


DEFAULTS = {
    "calculation": {"margin": 20, "currency": "USD"},
    "notify": True,
    "plan": "free",
}


class FakeUsers:
    """The two methods the controller asks `UserService` for."""

    def __init__(self, stored: Optional[Dict[str, Any]] = None) -> None:
        self.stored: Dict[str, Any] = dict(stored or {})
        self.written: List[Dict[str, Any]] = []

    async def get_account(self, identifier: str) -> Dict[str, Any]:
        return self.stored

    async def set_account(self, identifier: str, values: Dict[str, Any]) -> None:
        self.stored = values
        self.written.append(values)


@pytest.fixture(autouse=True)
def restore_code():
    """`config.code` is process-global; a test that registers an account page
    must not leave it registered for the next one."""
    code = config.code
    before = (code.account, code.on_account_load, code.on_account_update)
    yield
    code.account, code.on_account_load, code.on_account_update = before


@pytest.fixture
def registered():
    config.code.account = Account
    return Account


def _auth():
    return chainlit_auth(token_secret=SECRET)


def _client(users: Optional[FakeUsers] = None, **kwargs):
    dependencies = kwargs.pop("dependencies", {})
    if users is not None:
        dependencies["user_service"] = Provide(
            lambda: users, sync_to_thread=False, use_cache=True
        )
    return create_test_client(
        route_handlers=[],
        plugins=[ChainlitPlugin(auth=_auth())],
        dependencies=dependencies,
        debug=False,
        **kwargs,
    )


def _sign_in(client, identifier: str = "ada") -> None:
    client.cookies.set(COOKIE, _auth().create_token(identifier=identifier))


# --- who may ask -------------------------------------------------------------


def test_without_a_cookie_the_route_is_401(registered):
    with _client() as client:
        response = client.get("/project/account")

    assert response.status_code == 401


def test_nothing_registered_is_a_404_not_an_empty_page():
    """The 404 *is* the "not configured" state: the client asks this route
    rather than a flag in /project/settings, so there is one fact in one
    place."""
    with _client() as client:
        _sign_in(client)
        response = client.get("/project/account")

    assert response.status_code == 404


# --- reading -----------------------------------------------------------------


def test_the_page_carries_the_schema_the_defaults_and_readonly(registered):
    with _client() as client:
        _sign_in(client)
        response = client.get("/project/account")

    body = response.json()
    assert response.status_code == 200
    assert body["schema"]["$ref"] == "#/$defs/Account"
    assert set(body["schema"]["$defs"]) == {"Account", "Calculation"}
    assert body["values"] == DEFAULTS
    assert body["message"] is None


def test_with_no_store_and_no_hook_the_page_is_readonly(registered):
    with _client() as client:
        _sign_in(client)
        response = client.get("/project/account")

    assert response.json()["readonly"] is True


def test_with_a_store_the_page_is_writable(registered):
    with _client(FakeUsers()) as client:
        _sign_in(client)
        response = client.get("/project/account")

    assert response.json()["readonly"] is False


def test_stored_values_are_merged_over_the_defaults(registered):
    users = FakeUsers({"notify": False, "calculation": {"margin": 35}})
    with _client(users) as client:
        _sign_in(client)
        response = client.get("/project/account")

    assert response.json()["values"] == {
        "calculation": {"margin": 35, "currency": "USD"},
        "notify": False,
        "plan": "free",
    }


def test_a_stale_stored_key_is_dropped_and_the_rest_survives(registered):
    """The app retired a field. Refusing the whole document would take the
    page away from everyone who saved while it existed."""
    users = FakeUsers({"retired": "x", "notify": False})
    with _client(users) as client:
        _sign_in(client)
        response = client.get("/project/account")

    assert response.status_code == 200
    assert response.json()["values"]["notify"] is False
    assert "retired" not in response.json()["values"]


def test_on_account_load_wins_over_the_store(registered):
    async def load(user):
        assert user is not None
        assert user.identifier == "ada"
        return {"notify": False, "plan": "pro"}

    config.code.on_account_load = load
    users = FakeUsers({"plan": "free"})
    with _client(users) as client:
        _sign_in(client)
        response = client.get("/project/account")

    assert response.json()["values"]["plan"] == "pro"
    assert response.json()["values"]["notify"] is False


def test_a_load_hook_returning_none_falls_back_to_the_store(registered):
    async def load(user):
        return None

    config.code.on_account_load = load
    with _client(FakeUsers({"plan": "pro"})) as client:
        _sign_in(client)
        response = client.get("/project/account")

    assert response.json()["values"]["plan"] == "pro"


# --- writing -----------------------------------------------------------------


def test_a_valid_put_reaches_the_hook_as_a_struct_and_the_store_as_builtins(
    registered,
):
    seen: List[Any] = []

    async def save(user, account):
        seen.append((user.identifier, account))
        return "Сохранено"

    config.code.on_account_update = save
    users = FakeUsers()
    with _client(users) as client:
        _sign_in(client)
        response = client.put(
            "/project/account",
            json={"calculation": {"margin": 35}, "notify": False, "plan": "free"},
        )

    identifier, account = seen[0]
    assert identifier == "ada"
    assert isinstance(account, Account)
    assert account.calculation.margin == 35
    assert users.written == [
        {
            "calculation": {"margin": 35, "currency": "USD"},
            "notify": False,
            "plan": "free",
        }
    ]
    body = response.json()
    assert response.status_code == 200
    assert body["message"] == "Сохранено"
    # Freshly built, not echoed: the page shows what is now stored.
    assert body["values"]["calculation"]["margin"] == 35
    assert body["readonly"] is False


def test_a_save_hook_that_raises_fails_the_request_and_stores_nothing(registered):
    """The hook is registered unwrapped on purpose. `wrap_user_function`
    would log the exception and answer None, and the route would then store
    the values and say "saved" about a save the application refused."""

    async def save(user, account):
        raise RuntimeError("the app's own store is down")

    config.code.on_account_update = save
    users = FakeUsers()
    with _client(users) as client:
        _sign_in(client)
        response = client.put("/project/account", json={"notify": False})

    assert response.status_code == 500
    assert users.written == []


def test_a_load_hook_that_raises_is_a_500_not_the_stored_values(registered):
    async def load(user):
        raise RuntimeError("no")

    config.code.on_account_load = load
    with _client(FakeUsers({"notify": False})) as client:
        _sign_in(client)
        response = client.get("/project/account")

    assert response.status_code == 500


def test_a_sync_hook_is_accepted_too(registered):
    def save(user, account):
        return "ok"

    config.code.on_account_update = save
    with _client(FakeUsers()) as client:
        _sign_in(client)
        response = client.put("/project/account", json={"notify": False})

    assert response.status_code == 200
    assert response.json()["message"] == "ok"


def test_a_put_with_no_hook_still_stores(registered):
    users = FakeUsers()
    with _client(users) as client:
        _sign_in(client)
        response = client.put("/project/account", json={"notify": False})

    assert response.status_code == 200
    assert users.written[0]["notify"] is False
    assert response.json()["message"] is None


def test_an_out_of_range_value_is_a_400_naming_the_field(registered):
    """The msgspec path is the only field addressing the client has, so it
    has to survive into the response body."""
    with _client(FakeUsers()) as client:
        _sign_in(client)
        response = client.put("/project/account", json={"calculation": {"margin": 300}})

    assert response.status_code == 400
    assert "$.calculation.margin" in response.json()["detail"]


def test_a_wrong_type_is_a_400_too(registered):
    with _client(FakeUsers()) as client:
        _sign_in(client)
        response = client.put("/project/account", json={"notify": "maybe"})

    assert response.status_code == 400
    assert "$.notify" in response.json()["detail"]


def test_nothing_is_stored_when_validation_fails(registered):
    users = FakeUsers()
    with _client(users) as client:
        _sign_in(client)
        client.put("/project/account", json={"calculation": {"margin": 300}})

    assert users.written == []


def test_a_put_with_nowhere_to_store_is_a_405(registered):
    with _client() as client:
        _sign_in(client)
        response = client.put("/project/account", json={"notify": False})

    assert response.status_code == 405


def test_a_put_without_a_cookie_is_a_401(registered):
    with _client(FakeUsers()) as client:
        response = client.put("/project/account", json={"notify": False})

    assert response.status_code == 401


def test_the_answer_is_rebuilt_not_echoed(registered):
    """The app normalises what it was handed. A PUT that echoed its own body
    back would show the user a value the server does not actually hold."""

    async def load(user):
        return {"notify": False, "plan": "pro"}

    async def save(user, account):
        return None

    config.code.on_account_load = load
    config.code.on_account_update = save
    with _client() as client:
        _sign_in(client)
        response = client.put("/project/account", json={"notify": True, "plan": "free"})

    assert response.json()["values"] == {
        "calculation": {"margin": 20, "currency": "USD"},
        "notify": False,
        "plan": "pro",
    }


def test_the_decorators_wire_the_same_routes(registered):
    """Through `@cl.on_account_*`, not by assigning the hooks: the decorators
    go through `wrap_user_function`, which binds the arguments by position
    onto the hook's own parameter names."""
    import chainlit as cl

    seen: List[Any] = []

    @cl.on_account_update
    async def save(user, account):
        seen.append(account)
        return "ok"

    users = FakeUsers()
    with _client(users) as client:
        _sign_in(client)
        response = client.put("/project/account", json={"notify": False})

    assert response.json()["message"] == "ok"
    assert isinstance(seen[0], Account)
    assert seen[0].notify is False


def test_a_hook_alone_makes_the_page_writable(registered):
    """No data layer, but the application said it would take the values."""
    seen: List[Any] = []

    async def save(user, account):
        seen.append(account)
        return None

    config.code.on_account_update = save
    with _client() as client:
        _sign_in(client)
        response = client.put("/project/account", json={"notify": False})

    assert response.status_code == 200
    assert seen
    assert seen[0].notify is False
