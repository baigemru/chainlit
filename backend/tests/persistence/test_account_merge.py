"""Two writers on one account document, against a real PostgreSQL.

The unit tests in ``tests/test_account_merge.py`` pin what the merge decides.
This one pins the thing a pure function cannot have: that the route's read and
its write are one transaction and that the read *holds the row*, so a writer
that commits between them cannot slip past unseen. It is the shape
``chainlit-panda`` runs in production, where the other writer is a measurement
arriving from a background graph.

A fake store has nothing to lock, so nothing in memory can stand in for this.
Three threads, because the test client is one: ``TestClientTransport`` hands
the request to a blocking portal and waits, so a request fired from the thread
that also has to release the lock would wait for itself. The arriving writer
therefore lives in a thread with a loop and an engine of its own -- asyncpg
connections belong to the loop that opened them -- the save lives in another,
and what the pytest thread does is order the two.

What proves the lock is the *document at the end*, not the timing assertion
along the way. Taking ``with_for_update`` off the route's read leaves
``assert not saved.is_set()`` green -- the save then reads the row straight
past the arrival and blocks on its ``UPDATE`` instead, which looks exactly the
same from outside -- and turns the last assertions red, because it merges onto
what it read before the arrival and the arriving entry is gone. That is the
production bug, and it is why the merge without the lock is not a fix.
"""

import asyncio
import threading
import time
from typing import Annotated, Any, Callable, Dict, Iterator, List, Optional

import msgspec
import pytest
from litestar.testing import create_test_client
from msgspec import Meta
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from chainlit.account import decode_stored
from chainlit.persistence import Persistence
from chainlit.persistence.models import SCHEMA_NAME
from chainlit.persistence.statements import user_account_query
from chainlit.plugin import ChainlitPlugin
from chainlit.security import chainlit_auth
from tests.persistence.conftest import TABLE_NAMES, database_url  # noqa: F401

SECRET = "test-secret-not-a-real-one-but-long-enough-for-hs256"
COOKIE = "access_token"
ADA = "ada"

KEY = Meta(extra_json_schema={"x-key": True})


class Entry(msgspec.Struct):
    """A feed row with no declared name -- the shape the consumer ships."""

    title: str = ""
    run_id: str = ""
    seen: bool = False


class Feed(msgspec.Struct):
    entries: List[Entry] = []


class Pair(msgspec.Struct):
    pair_id: Annotated[str, KEY] = ""
    note: str = ""
    last_measured: str = ""


class Items(msgspec.Struct):
    pairs: List[Pair] = []


class Calculation(msgspec.Struct):
    cny_rate: str = ""


class Account(msgspec.Struct):
    calculation: Calculation = msgspec.field(default_factory=Calculation)
    items: Items = msgspec.field(default_factory=Items)
    feed: Feed = msgspec.field(default_factory=Feed)


def _run(url: str, work: Callable[[Persistence], Any]) -> Any:
    """One piece of database work, on a loop and an engine of its own."""

    async def go() -> Any:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            return await work(Persistence.from_engine(engine))
        finally:
            await engine.dispose()

    return asyncio.run(go())


@pytest.fixture
def db_url(database_url: str) -> str:  # noqa: F811 - the imported fixture
    """The migrated database, emptied for this test."""

    async def truncate(persistence: Persistence) -> None:
        qualified = ", ".join(f'"{SCHEMA_NAME}".{name}' for name in TABLE_NAMES)
        async with persistence.uow() as unit:
            await unit.session.execute(
                text(f"TRUNCATE {qualified} RESTART IDENTITY CASCADE")
            )

    _run(database_url, truncate)
    return database_url


@pytest.fixture
def client(test_config: Any, db_url: str) -> Iterator[Any]:
    """The application, with an engine of its own behind the portal."""
    test_config.code.account = Account
    test_config.code.on_message = lambda message: None
    auth = chainlit_auth(token_secret=SECRET)
    plugin = ChainlitPlugin(
        test_config, persistence=Persistence.from_url(db_url), auth=auth
    )
    with create_test_client(route_handlers=[], plugins=[plugin]) as test_client:
        test_client.cookies.set(COOKIE, auth.create_token(identifier=ADA))
        yield test_client


def seed(url: str, account: Account) -> None:
    async def write(persistence: Persistence) -> None:
        async with persistence.uow() as unit:
            await unit.users.save(ADA)
            await unit.users.set_account(ADA, msgspec.to_builtins(account))

    _run(url, write)


def stored(url: str) -> Account:
    async def read(persistence: Persistence) -> Account:
        async with persistence.uow() as unit:
            return decode_stored(await unit.users.get_account(ADA), Account)

    return _run(url, read)


class Arrival(threading.Thread):
    """The other writer: locks the row, waits to be told, then commits.

    The lock is taken before the save is fired and released only when
    ``release`` is set, which is what lets the test say when the save was
    allowed to see the document.
    """

    def __init__(self, url: str) -> None:
        super().__init__(daemon=True)
        self.url = url
        self.locked = threading.Event()
        self.release = threading.Event()
        self.failure: Optional[BaseException] = None

    def run(self) -> None:
        try:
            _run(self.url, self._write)
        except BaseException as error:  # reported to the test, not swallowed
            self.failure = error
            self.locked.set()

    async def _write(self, persistence: Persistence) -> None:
        async with persistence.uow() as unit:
            row = await unit.session.execute(user_account_query(ADA).with_for_update())
            account = decode_stored(row.scalar_one_or_none(), Account)
            self.locked.set()
            await asyncio.to_thread(self.release.wait, 30)
            account.feed.entries = [Entry(title="замер", run_id="r1")] + list(
                account.feed.entries
            )
            account.items.pairs[0].last_measured = "T1"
            await unit.users.set_account(ADA, msgspec.to_builtins(account))


def test_a_save_and_an_arrival_both_land(db_url: str, client: Any) -> None:
    """The user is on the page when a measurement arrives, and then saves.

    Everything is in the row afterwards: the feed entry that arrived, the
    measurement on the card, the rate the user typed, and the flag they set --
    on the entry they were looking at, which the arrival had pushed down by
    one.
    """
    seed(
        db_url,
        Account(
            calculation=Calculation(cny_rate="11.0"),
            items=Items(pairs=[Pair(pair_id="p1", note="кружки")]),
            feed=Feed(entries=[Entry(title="старое", run_id="r0")]),
        ),
    )

    shown = client.get("/project/account").json()["values"]
    edited: Dict[str, Any] = msgspec.to_builtins(msgspec.convert(shown, Account))
    edited["calculation"]["cny_rate"] = "12.0"
    edited["feed"]["entries"][0]["seen"] = True

    arrival = Arrival(db_url)
    arrival.start()
    assert arrival.locked.wait(10), "the arriving writer never took the row"
    assert arrival.failure is None, arrival.failure

    saved = threading.Event()
    answer: Dict[str, Any] = {}

    def save() -> None:
        response = client.put(
            "/project/account", json={"values": edited, "base": shown}
        )
        answer["status"] = response.status_code
        answer["values"] = response.json().get("values")
        saved.set()

    saving = threading.Thread(target=save, daemon=True)
    saving.start()
    time.sleep(0.5)
    # In flight while the arrival holds the row: whether it is waiting to read
    # it or to write it, it is inside the window this test is about.
    assert not saved.is_set(), "the save finished before the arrival committed"

    arrival.release.set()
    assert saved.wait(20), "the save never finished after the lock was released"
    saving.join(5)
    assert arrival.failure is None, arrival.failure
    assert answer["status"] == 200

    final = stored(db_url)
    assert [(entry.run_id, entry.seen) for entry in final.feed.entries] == [
        ("r1", False),
        ("r0", True),
    ]
    assert final.calculation.cny_rate == "12.0"
    assert final.items.pairs[0].last_measured == "T1"
    assert final.items.pairs[0].note == "кружки"
    # And the page the save answered with is the merged document, so the form
    # that saved over the arrival adopts it instead of holding yesterday's.
    assert [entry["run_id"] for entry in answer["values"]["feed"]["entries"]] == [
        "r1",
        "r0",
    ]
