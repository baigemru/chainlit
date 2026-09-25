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
from typing import Annotated, Any, Dict, List, Optional, Tuple

import msgspec
import pytest
from litestar.di import Provide
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
    rate: Annotated[
        float, Meta(title="Котировка", extra_json_schema={"readOnly": True})
    ] = 0.0


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
    balance: Annotated[
        float, Meta(title="Баланс", extra_json_schema={"readOnly": True})
    ] = 0.0


class FakeUsers:
    """The two methods the controller asks `UserService` for, as in
    `test_account.py`: an action may store now, and what it stored is the
    interesting half."""

    def __init__(self, stored: Optional[Dict[str, Any]] = None) -> None:
        self.stored: Dict[str, Any] = dict(stored or {})
        self.written: List[Dict[str, Any]] = []

    async def get_account(self, identifier: str) -> Dict[str, Any]:
        return self.stored

    async def locked_account(self, identifier: str) -> Dict[str, Any]:
        return self.stored

    async def set_account(self, identifier: str, values: Dict[str, Any]) -> None:
        self.stored = values
        self.written.append(values)


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


def _client(
    transit: Optional[TransitStore] = None,
    users: Optional[FakeUsers] = None,
    **kwargs,
):
    dependencies = kwargs.pop("dependencies", {})
    if users is not None:
        dependencies["user_service"] = Provide(
            lambda: users, sync_to_thread=False, use_cache=True
        )
    return create_test_client(
        route_handlers=[],
        plugins=[ChainlitPlugin(auth=_auth(), transit=transit)],
        dependencies=dependencies,
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
    async def compare(user, item, account):  # pragma: no cover - must never run
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
    async def compare(user, item, account):
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
    async def export(user, item, account):
        seen.append(item)
        return cl.AccountToast(message="ok")

    with _client() as client:
        _sign_in(client)
        response = _post(client, "export", "watch", None)

    assert response.status_code == 201
    assert seen == [None]


def test_a_path_the_struct_cannot_address_is_a_400(registered):
    @cl.account_action("compare")
    async def compare(user, item, account):  # pragma: no cover - must never run
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
    async def compare(user, item, account):  # pragma: no cover - must never run
        raise AssertionError("the hook ran on an item that does not fit")

    with _client() as client:
        _sign_in(client)
        response = _post(client, "compare", "watch.items.0", {**CARD, "price": "free"})

    assert response.status_code == 400
    assert "$.price" in response.json()["detail"]


# --- the three outcomes ------------------------------------------------------


def test_a_toast_outcome_is_the_hooks_message(registered):
    @cl.account_action("compare")
    async def compare(user, item, account):
        return cl.AccountToast(message="Нашли дешевле")

    with _client() as client:
        _sign_in(client)
        response = _post(client, "compare", "watch.items.0", CARD)

    assert response.status_code == 201
    assert response.json() == {"outcome": {"t": "toast", "message": "Нашли дешевле"}}


def test_a_refresh_outcome_carries_the_page_the_get_would_build(registered):
    """Built by `_render`, not echoed: the application changed something and
    the page has to show what it changed, without a reload."""

    @cl.on_account_load
    async def load(user, account, tab):
        return Account(watch=Watch(items=[WatchedItem(title="Кружка")]))

    @cl.account_action("compare")
    async def compare(user, item, account):
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


def test_a_refresh_draws_its_page_for_the_section_the_button_was_in(registered):
    """The rebuilt page is a load like any other, so the load hook hears
    the section `?tab=` names -- the dialog sends the one it is showing."""
    tabs: List[Optional[str]] = []

    @cl.on_account_load
    async def load(user, account, tab):
        tabs.append(tab)
        return account

    @cl.account_action("compare")
    async def compare(user, item, account):
        return cl.AccountRefresh()

    with _client() as client:
        _sign_in(client)
        response = client.post(
            "/project/account/actions/compare?tab=watch",
            json={"path": "watch.items.0", "item": CARD},
        )

    assert response.status_code == 201
    assert tabs == ["watch"]


def test_an_unknown_return_value_is_a_500(registered):
    @cl.account_action("compare")
    async def compare(user, item, account):
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
    async def compare(user, item, account):
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
    async def start(user, item, account):
        return cl.AccountOpenThread(chat_profile="Быстрый")

    with _client(transit) as client:
        _sign_in(client)
        response = _post(client, "start", "watch", None)

    outcome = response.json()["outcome"]
    assert outcome["thread_id"] is None
    assert outcome["chat_profile"] == "Быстрый"
    assert outcome["has_transit_message"] is False


# --- what an action stores ---------------------------------------------------


def test_a_refresh_with_an_account_stores_it_and_draws_the_page_from_it(registered):
    """«Убрать» on a notice. The action changes the values and hands them
    back; the engine stores them with the request's own session, so the hook
    no longer opens one of its own and the page cannot show the notice it has
    just retired."""

    @cl.account_action("dismiss")
    async def dismiss(user, item, account):
        return cl.AccountRefresh(message="Убрали", account=Account(plan="pro"))

    users = FakeUsers({"plan": "free", "watch": {"items": [{"title": "Кружка"}]}})
    with _client(users=users) as client:
        _sign_in(client)
        response = _post(client, "dismiss", "watch.items.0", CARD)

    outcome = response.json()["outcome"]
    assert outcome["t"] == "page"
    assert outcome["message"] == "Убрали"
    assert outcome["page"]["values"]["plan"] == "pro"
    assert outcome["page"]["values"]["watch"]["items"] == []
    assert users.written == [
        {"watch": {"items": []}, "plan": "pro", "balance": 0.0},
    ]


def test_a_refresh_stores_no_readonly_leaf(registered):
    """The same rule a PUT runs, at the one other door into the store."""

    @cl.account_action("dismiss")
    async def dismiss(user, item, account):
        return cl.AccountRefresh(
            account=Account(
                balance=1250.0,
                watch=Watch(items=[WatchedItem(title="Кружка", rate=91.4)]),
            )
        )

    users = FakeUsers()
    with _client(users=users) as client:
        _sign_in(client)
        response = _post(client, "dismiss", "watch.items.0", CARD)

    stored = users.written[0]
    assert stored["balance"] == 0.0
    assert stored["watch"]["items"][0] == {
        "title": "Кружка",
        "price": 0.0,
        "watch": False,
        "rate": 0.0,
    }
    # Shown, though: the page is built from what the hook returned.
    assert response.json()["outcome"]["page"]["values"]["balance"] == 1250.0


def test_a_refresh_without_an_account_stores_nothing(registered):
    """An action that only looked at something is not a write."""

    @cl.account_action("compare")
    async def compare(user, item, account):
        return cl.AccountRefresh(message="Смотрим")

    users = FakeUsers({"plan": "pro"})
    with _client(users=users) as client:
        _sign_in(client)
        response = _post(client, "compare", "watch.items.0", CARD)

    assert users.written == []
    assert response.json()["outcome"]["page"]["values"]["plan"] == "pro"


def test_the_load_hook_after_a_refresh_sees_what_the_action_stored(registered):
    """One way to produce a page: the action's values become the baseline the
    load hook is handed, so it enriches them rather than re-reading a row its
    own session cannot see yet."""
    seen: List[Any] = []

    @cl.on_account_load
    async def load(user, account, tab):
        seen.append(account)
        return msgspec.structs.replace(account, balance=1250.0)

    @cl.account_action("dismiss")
    async def dismiss(user, item, account):
        return cl.AccountRefresh(account=Account(plan="pro"))

    users = FakeUsers({"plan": "free"})
    with _client(users=users) as client:
        _sign_in(client)
        response = _post(client, "dismiss", "watch.items.0", CARD)

    assert seen[0].plan == "pro"
    assert response.json()["outcome"]["page"]["values"]["balance"] == 1250.0
    # Once, by the action. The load hook only added a derived field.
    assert len(users.written) == 1


def test_a_refresh_carrying_a_mapping_is_a_500(registered):
    """Refused, not converted: the values are stored, and a mapping would put
    the default of every key it omits into the row."""

    @cl.account_action("dismiss")
    async def dismiss(user, item, account):
        return cl.AccountRefresh(account={"plan": "pro"})

    with _client(users=FakeUsers(), raise_server_exceptions=False) as client:
        _sign_in(client)
        response = _post(client, "dismiss", "watch.items.0", CARD)

    assert response.status_code == 500


def test_a_refresh_with_an_account_and_nowhere_to_store_still_draws_it(registered):
    @cl.account_action("dismiss")
    async def dismiss(user, item, account):
        return cl.AccountRefresh(account=Account(plan="pro"))

    with _client() as client:
        _sign_in(client)
        response = _post(client, "dismiss", "watch.items.0", CARD)

    assert response.status_code == 201
    assert response.json()["outcome"]["page"]["values"]["plan"] == "pro"


# --- the third argument ------------------------------------------------------


def test_the_hook_is_handed_the_whole_account_as_the_store_holds_it(registered):
    """Because ``cl.AccountRefresh(account=...)`` wants the whole document.

    Handed one card and asked for the whole page back, every application had
    to read ``users.account`` through a session of its own -- racing the one
    the request is already in, and duplicating the engine's lazy decoder to
    make sense of what came back. The engine has the values; it passes them.
    """
    seen: List[Any] = []

    @cl.account_action("compare")
    async def compare(user, item, account):
        seen.append(account)
        return cl.AccountToast(message="ok")

    users = FakeUsers({"plan": "pro", "watch": {"items": [CARD]}})
    with _client(users=users) as client:
        _sign_in(client)
        response = _post(client, "compare", "watch.items.0", CARD)

    assert response.status_code == 201
    [account] = seen
    assert isinstance(account, Account)
    assert account.plan == "pro"
    assert [it.title for it in account.watch.items] == ["Кружка"]
    # Read, not locked: the lock a save holds spans its merge, and holding
    # one here would span whatever network the action talks to. See
    # ``_store_action_values``.
    assert users.written == []


def test_an_action_returns_what_it_was_handed_with_one_thing_changed(registered):
    """The shape the third argument makes possible, end to end."""

    @cl.account_action("dismiss")
    async def dismiss(user, item, account):
        return cl.AccountRefresh(
            account=msgspec.structs.replace(account, plan="free"), message="Сброшено"
        )

    users = FakeUsers({"plan": "pro", "watch": {"items": [CARD]}})
    with _client(users=users) as client:
        _sign_in(client)
        response = _post(client, "dismiss", "watch", None)

    assert response.status_code == 201
    values = response.json()["outcome"]["page"]["values"]
    assert values["plan"] == "free"
    # And the card it was not asked about is still there: the action changed
    # what it was given rather than rebuilding it.
    assert [it["title"] for it in values["watch"]["items"]] == ["Кружка"]


def test_a_hook_with_the_old_signature_is_refused_at_import(registered):
    """Where a mismatch is cheapest to see. Called with three arguments and
    written with two, it is a 500 on somebody's first click otherwise."""
    with pytest.raises(TypeError, match=r"@cl.account_action\('compare'\)"):

        @cl.account_action("compare")
        async def compare(user, item):  # pragma: no cover - never registered
            return cl.AccountToast(message="ok")

    assert "compare" not in config.code.account_actions
