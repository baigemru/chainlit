"""`POST /project/account/actions/{name}`: who runs, with what, answering what.

The route's whole job is three conversions and a dispatch, and each one has a
way to be wrong that the type system cannot see: the action name is a string
from the client, the `path` is a string from the client, and the `item` is a
JSON object from the client that has to become the Struct the *schema* says
sits at that path. So every refusal gets a test, and the handover gets two --
one that it is parked for the caller, one that it is not handed to anybody
else.

In memory, no database, like `test_account.py`. The transit store is the
plugin's own, handed in so the test can ask it what the route parked.
"""

import asyncio
from typing import Annotated, Any, List, Optional, Tuple

import msgspec
import pytest
from litestar.testing import create_test_client
from msgspec import Meta

import chainlit as cl
from chainlit.config import config
from chainlit.plugin import ChainlitPlugin
from chainlit.security import chainlit_auth
from chainlit.transit_store import TransitStore

SECRET = "test-secret-not-a-real-one-but-long-enough-for-hs256"
COOKIE = "access_token"


class WatchedItem(msgspec.Struct):
    title: Annotated[str, Meta(title="Название")] = ""
    price: Annotated[float, Meta(ge=0, title="Цена")] = 0
    watch: Annotated[bool, Meta(title="Следить")] = False


class Watch(msgspec.Struct):
    """Мои товары"""

    items: Annotated[
        List[WatchedItem],
        Meta(
            title="Товары",
            extra_json_schema={
                "x-widget": "cards",
                "x-actions": [{"name": "compare", "label": "Где дешевле"}],
            },
        ),
    ] = []


class Account(msgspec.Struct):
    watch: Annotated[Watch, Meta(title="Товары")] = msgspec.field(default_factory=Watch)
    plan: Annotated[str, Meta(title="Тариф")] = "free"


@pytest.fixture(autouse=True)
def restore_code():
    """`config.code` is process-global; every hook a test registers has to
    leave with it."""
    code = config.code
    before = (
        code.account,
        code.on_account_load,
        code.on_account_update,
        dict(code.account_actions),
    )
    yield
    (
        code.account,
        code.on_account_load,
        code.on_account_update,
        actions,
    ) = before
    code.account_actions.clear()
    code.account_actions.update(actions)


@pytest.fixture
def registered():
    config.code.account = Account
    return Account


def _auth():
    return chainlit_auth(token_secret=SECRET)


def _client(transit: Optional[TransitStore] = None, **kwargs):
    return create_test_client(
        route_handlers=[],
        plugins=[ChainlitPlugin(auth=_auth(), transit=transit)],
        debug=False,
        **kwargs,
    )


def _sign_in(client, identifier: str = "ada") -> None:
    client.cookies.set(COOKIE, _auth().create_token(identifier=identifier))


def _post(client, name: str, path: str, item: Any = None):
    return client.post(
        f"/project/account/actions/{name}", json={"path": path, "item": item}
    )


CARD = {"title": "Кружка", "price": 12.5, "watch": True}


# --- who may ask -------------------------------------------------------------


def test_without_a_cookie_the_action_route_is_401(registered):
    @cl.account_action("compare")
    async def compare(user, item):  # pragma: no cover - must never run
        raise AssertionError("an anonymous caller reached the hook")

    with _client() as client:
        response = _post(client, "compare", "watch.items.0", CARD)

    assert response.status_code == 401


def test_an_action_nobody_registered_is_a_404(registered):
    with _client() as client:
        _sign_in(client)
        response = _post(client, "nope", "watch.items.0", CARD)

    assert response.status_code == 404


# --- what the hook is handed -------------------------------------------------


def test_the_item_reaches_the_hook_as_the_struct_the_schema_names(registered):
    """The type comes from the registered Struct walked by `path`, so the hook
    is handed `WatchedItem` without annotating anything."""
    seen: List[Tuple[Any, Any]] = []

    @cl.account_action("compare")
    async def compare(user, item):
        seen.append((user, item))
        return cl.AccountToast(message="ok")

    with _client() as client:
        _sign_in(client)
        response = _post(client, "compare", "watch.items.3", CARD)

    assert response.status_code == 201
    [(user, item)] = seen
    assert isinstance(item, WatchedItem)
    assert (item.title, item.price, item.watch) == ("Кружка", 12.5, True)
    assert user.identifier == "ada"


def test_a_tab_action_hands_the_hook_none_rather_than_the_tab(registered):
    """`path` names the tab and `item` is null. Converting the tab would give
    the hook a copy of values the user never edited through this button."""
    seen: List[Any] = []

    @cl.account_action("export")
    async def export(user, item):
        seen.append(item)
        return cl.AccountToast(message="ok")

    with _client() as client:
        _sign_in(client)
        response = _post(client, "export", "watch", None)

    assert response.status_code == 201
    assert seen == [None]


def test_a_path_the_struct_cannot_address_is_a_400(registered):
    @cl.account_action("compare")
    async def compare(user, item):  # pragma: no cover - must never run
        raise AssertionError("the hook ran on an unresolvable path")

    with _client() as client:
        _sign_in(client)
        response = _post(client, "compare", "watch.baskets.0", CARD)

    assert response.status_code == 400
    assert "baskets" in response.json()["detail"]


def test_an_item_that_does_not_fit_is_a_400_naming_the_field(registered):
    """msgspec's message is passed through: the path in it is the only field
    addressing the client has."""

    @cl.account_action("compare")
    async def compare(user, item):  # pragma: no cover - must never run
        raise AssertionError("the hook ran on an item that does not fit")

    with _client() as client:
        _sign_in(client)
        response = _post(client, "compare", "watch.items.0", {**CARD, "price": "free"})

    assert response.status_code == 400
    assert "$.price" in response.json()["detail"]


# --- the three outcomes ------------------------------------------------------


def test_a_toast_outcome_is_the_hooks_message(registered):
    @cl.account_action("compare")
    async def compare(user, item):
        return cl.AccountToast(message="Нашли дешевле")

    with _client() as client:
        _sign_in(client)
        response = _post(client, "compare", "watch.items.0", CARD)

    assert response.status_code == 201
    assert response.json() == {"outcome": {"t": "toast", "message": "Нашли дешевле"}}


def test_a_refresh_outcome_carries_the_page_the_get_would_build(registered):
    """Built by `_current`, not echoed: the application changed something and
    the page has to show what it changed, without a reload."""

    @cl.on_account_load
    async def load(user):
        return Account(watch=Watch(items=[WatchedItem(title="Кружка")]))

    @cl.account_action("compare")
    async def compare(user, item):
        return cl.AccountRefresh(message="Обновлено")

    with _client() as client:
        _sign_in(client)
        response = _post(client, "compare", "watch.items.0", CARD)

    assert response.status_code == 201
    outcome = response.json()["outcome"]
    assert outcome["t"] == "page"
    assert outcome["message"] == "Обновлено"
    assert outcome["page"]["values"]["watch"]["items"][0]["title"] == "Кружка"
    assert outcome["page"]["schema"]["$ref"] == "#/$defs/Account"


def test_an_unknown_return_value_is_a_500(registered):
    @cl.account_action("compare")
    async def compare(user, item):
        return "please open a chat"

    with _client(raise_server_exceptions=False) as client:
        _sign_in(client)
        response = _post(client, "compare", "watch.items.0", CARD)

    assert response.status_code == 500


# --- the handover ------------------------------------------------------------


def test_open_thread_parks_the_message_for_the_caller_and_nobody_else(registered):
    """The same handover a profile switch performs: the engine mints the
    thread, parks the value under it, and only the caller's own socket may
    claim it. The client is told a thread id and a boolean, never the value."""
    transit = TransitStore()

    @cl.account_action("compare")
    async def compare(user, item):
        return cl.AccountOpenThread(
            chat_profile="Быстрый",
            transit_message={"compare": item.title},
            parent="thread-a",
        )

    with _client(transit) as client:
        _sign_in(client)
        response = _post(client, "compare", "watch.items.0", CARD)

    assert response.status_code == 201
    outcome = response.json()["outcome"]
    assert outcome["t"] == "open_thread"
    assert outcome["chat_profile"] == "Быстрый"
    assert outcome["has_transit_message"] is True
    assert "compare" not in response.text

    thread_id = outcome["thread_id"]

    async def claims():
        # Both in one loop: the store's claim lock binds to the loop that
        # first awaits it.
        foreign = await transit.claim(thread_id, "bob")
        mine = await transit.claim(thread_id, "ada")
        return foreign, mine

    foreign, mine = asyncio.run(claims())
    assert foreign is None, "a foreign owner claimed the record"
    assert mine is not None
    assert mine.value == {"compare": "Кружка"}
    assert mine.owner == "ada"
    assert mine.parent == "thread-a"


def test_open_thread_with_nothing_to_hand_over_names_no_thread(registered):
    """No message and no parent is a plain profile switch, and the route
    says so with a null id rather than minting a thread of its own: the one
    function that mints is `mint_handover`, and an id minted beside it
    would be a conversation nothing was parked under."""
    transit = TransitStore()

    @cl.account_action("start")
    async def start(user, item):
        return cl.AccountOpenThread(chat_profile="Быстрый")

    with _client(transit) as client:
        _sign_in(client)
        response = _post(client, "start", "watch", None)

    outcome = response.json()["outcome"]
    assert outcome["thread_id"] is None
    assert outcome["chat_profile"] == "Быстрый"
    assert outcome["has_transit_message"] is False
