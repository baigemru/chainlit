"""The profile-switch handover, on a Litestar store.

Each test states one rule the dict implementation had and the store has to
keep: one claim only, the owner is checked, an unclaimed record expires, and
"nothing to hand over" revokes rather than parks.
"""

import asyncio
import time
from datetime import timedelta

import pytest
from litestar import Litestar
from litestar.stores.memory import MemoryStore

from chainlit.plugin import ChainlitPlugin
from chainlit.transit_store import TRANSIT_STORE_NAME, TransitStore, transit_sweeper


async def test_a_parked_record_comes_back_to_its_owner():
    transit = TransitStore()

    await transit.park(
        "successor", {"content": "hi"}, owner="user-1", parent="thread-1"
    )
    record = await transit.claim("successor", owner="user-1")

    assert record is not None
    assert record.value == {"content": "hi"}
    assert record.parent == "thread-1"


async def test_a_record_can_be_claimed_once():
    """The successor's start hook runs once; a second claim is a bug reporting
    itself, not a second copy of the message."""
    transit = TransitStore()
    await transit.park("successor", "message", owner="user-1")

    assert await transit.claim("successor", owner="user-1") is not None
    assert await transit.claim("successor", owner="user-1") is None


async def test_concurrent_claims_do_not_both_win():
    """``get`` then ``delete`` is two awaits where ``dict.pop`` was one."""
    transit = TransitStore()
    await transit.park("successor", "message", owner="user-1")

    results = await asyncio.gather(
        *(transit.claim("successor", owner="user-1") for _ in range(20))
    )

    assert len([r for r in results if r is not None]) == 1


async def test_a_foreign_owner_gets_nothing():
    transit = TransitStore()
    await transit.park("successor", "message", owner="user-1")

    assert await transit.claim("successor", owner="user-2") is None


async def test_a_record_carrying_only_a_parent_is_still_a_record():
    """A switch may hand over a parent thread and no message at all."""
    transit = TransitStore()
    await transit.park("successor", None, owner="user-1", parent="thread-1")

    record = await transit.claim("successor", owner="user-1")
    assert record is not None
    assert record.value is None
    assert record.parent == "thread-1"


async def test_parking_nothing_revokes_what_was_parked():
    transit = TransitStore()
    await transit.park("successor", "message", owner="user-1")

    await transit.park("successor", None, owner="user-1", parent=None)

    assert await transit.claim("successor", owner="user-1") is None


async def test_an_unclaimed_record_expires():
    """The TTL is the whole defence against a dead socket's record leaking
    into an unrelated future session."""
    transit = TransitStore(ttl=timedelta(milliseconds=50))
    await transit.park("successor", "message", owner="user-1")

    await asyncio.sleep(0.1)

    assert await transit.claim("successor", owner="user-1") is None


async def test_expired_records_are_freed_by_the_sweep():
    """``MemoryStore`` reaps on ``get`` only, and nobody ever gets an
    unclaimed key -- so without the sweep the record is held for the life of
    the process. ``exists`` is not asked: it does not check expiry."""
    store = MemoryStore()
    transit = TransitStore(store, ttl=timedelta(milliseconds=50))
    await transit.park("successor", "message", owner="user-1")
    await asyncio.sleep(0.1)

    assert store._store, "precondition: the expired entry is still held"
    await transit.sweep()

    assert not store._store


async def test_the_sweeper_runs_on_its_schedule():
    transit = TransitStore(ttl=timedelta(milliseconds=20))
    async with transit_sweeper(transit, interval=0.01):
        await transit.park("successor", "message", owner="user-1")
        await asyncio.sleep(0.1)
        assert not transit.store._store  # type: ignore[attr-defined]


async def test_the_sweeper_task_stops_with_the_app():
    transit = TransitStore()
    async with transit_sweeper(transit, interval=0.01):
        running = [
            t for t in asyncio.all_tasks() if t.get_name() == "chainlit-transit-sweeper"
        ]
        assert len(running) == 1
    assert running[0].cancelled() or running[0].done()


def test_the_app_lifespan_runs_the_sweep():
    """The sweeper is wired into the app, not merely written.

    Without it, every record that was never claimed -- a dead socket, an
    outdated frontend bundle -- is held for the life of the process:
    ``MemoryStore`` reaps on ``get`` and nobody ever gets these keys.
    """
    from functools import partial

    from litestar.testing import TestClient

    transit = TransitStore(ttl=timedelta(milliseconds=10))
    app = Litestar(
        plugins=[ChainlitPlugin(transit=transit, transit_sweep_interval=0.01)]
    )

    with TestClient(app) as client:
        client.blocking_portal.call(
            partial(transit.park, "successor", "message", owner="user-1")
        )
        assert transit.store._store  # type: ignore[attr-defined]
        time.sleep(0.2)
        assert not transit.store._store  # type: ignore[attr-defined]


def test_the_plugin_registers_the_store_under_its_name():
    transit = TransitStore()
    app = Litestar(plugins=[ChainlitPlugin(transit=transit)])

    assert app.stores.get(TRANSIT_STORE_NAME) is transit.store


def test_the_plugin_keeps_a_host_registry_it_did_not_create():
    """A host application's own stores must survive Chainlit being added."""
    host_store = MemoryStore()
    app = Litestar(
        stores={"sessions": host_store},
        plugins=[ChainlitPlugin(transit=TransitStore())],
    )

    assert app.stores.get("sessions") is host_store
    assert app.stores.get(TRANSIT_STORE_NAME) is not host_store


@pytest.mark.parametrize("value", [None, "", 0, False, {"a": 1}, [1, 2]])
async def test_falsy_values_survive_the_round_trip(value):
    """``""``, ``0`` and ``False`` are transit messages; only the absence of a
    record means "nothing to hand over"."""
    transit = TransitStore()
    await transit.park("successor", value, owner="user-1", parent="thread-1")

    record = await transit.claim("successor", owner="user-1")
    assert record is not None
    assert record.value == value
