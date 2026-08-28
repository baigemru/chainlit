"""The profile-switch handover, on a :mod:`litestar.stores` store.

``chainlit.transit`` is a module-global dict with a hand-written sweep. What
it actually is, is a TTL'd one-shot key-value handoff: a profile switch tears
the session down, mints the id the successor will connect with, and parks a
record for that successor's start hook to claim exactly once. Litestar has a
store abstraction for precisely that, and putting the record there is what
makes a multi-process deployment possible later — swap the ``MemoryStore``
for a ``RedisStore`` and the handover survives the switch landing on another
worker.

Traps this module exists to absorb, all verified against litestar 2.24 source:

* ``MemoryStore`` is not a ``NamespacedStore`` — only ``FileStore`` and
  ``RedisStore`` implement ``with_namespace`` — so the keys are prefixed
  here rather than by the store.
* ``MemoryStore.exists()`` is ``key in self._store`` and
  ``MemoryStore.expires_in()`` reads the stored object without checking it:
  neither notices expiry (``litestar/stores/memory.py:105-115``). Only
  ``get()`` reaps. So liveness is never asked with ``exists``, and
  ``delete_expired()`` has to be driven on a schedule or unclaimed records
  live as long as the process.
* ``get`` + ``delete`` is two awaits where ``dict.pop`` was one. A lock makes
  the claim atomic again, so two racing claims cannot both win.

Dropped from the dict implementation: ``MAX_TRANSIT_RECORDS``. A ``Store``
exposes no key count, so the backstop is not expressible; the TTL plus the
scheduled sweep is what bounds growth now.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import Any, AsyncIterator, Optional, Union

import msgspec
from litestar.stores.base import Store
from litestar.stores.memory import MemoryStore

# A record that was never claimed (dead socket, outdated frontend bundle)
# must not survive long enough to leak into an unrelated future session.
TRANSIT_TTL_SECONDS = 120

__all__ = (
    "TRANSIT_STORE_NAME",
    "TransitRecord",
    "TransitStore",
    "transit_sweeper",
)

# The name the store is registered under in the app's ``StoreRegistry``;
# ``app.stores.get(TRANSIT_STORE_NAME)`` is how anything else reaches it.
TRANSIT_STORE_NAME = "chainlit_transit"

# ``MemoryStore`` has no namespacing of its own, and a registry entry may be
# shared with something else in a host application.
KEY_PREFIX = "transit:"

# One sweep per TTL: a record can outlive its expiry by at most that again,
# which is memory, not correctness -- ``claim`` never returns an expired one.
SWEEP_INTERVAL_SECONDS = TRANSIT_TTL_SECONDS


class TransitRecord(msgspec.Struct):
    """What one session hands to its successor.

    ``value`` is the transit message and may legitimately be ``None`` — a
    record can carry only a ``parent``. ``owner`` is the session that parked
    it, and only that owner may claim it.
    """

    value: Any = None
    owner: Optional[str] = None
    parent: Optional[str] = None


class TransitStore:
    """One-shot, owner-checked, TTL'd handover over a Litestar ``Store``."""

    def __init__(
        self,
        store: Optional[Store] = None,
        ttl: Union[int, timedelta] = TRANSIT_TTL_SECONDS,
    ) -> None:
        self.store = store if store is not None else MemoryStore()
        self.ttl = ttl
        # ``claim`` is a read followed by a delete; without this two
        # concurrent claims of the same id could both come back with the
        # record. ``dict.pop`` used to make that impossible for free. The
        # lock is per-process: on a shared store across workers the pair is
        # still racy and wants an atomic primitive (Redis ``GETDEL``).
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(session_id: str) -> str:
        return f"{KEY_PREFIX}{session_id}"

    async def park(
        self,
        session_id: str,
        value: Any,
        owner: Optional[str],
        parent: Optional[str] = None,
    ) -> None:
        """Park a record for ``session_id``.

        With nothing to hand over — no value and no parent — whatever an
        earlier call parked is revoked instead. That is the contract the
        emitter is written against.
        """
        if value is None and parent is None:
            await self.discard(session_id)
            return
        record = TransitRecord(value=value, owner=owner, parent=parent)
        await self.store.set(
            self._key(session_id), msgspec.json.encode(record), expires_in=self.ttl
        )

    async def claim(
        self, session_id: str, owner: Optional[str]
    ) -> Optional[TransitRecord]:
        """Take the record for ``session_id``, or ``None``.

        ``None`` covers all four ways there is nothing to hand over: never
        parked, already claimed, expired, or parked by a different owner. A
        foreign record is dropped rather than left behind — the id was minted
        for this successor and nobody else will ever come for it.
        """
        key = self._key(session_id)
        async with self._lock:
            raw = await self.store.get(key)
            if raw is None:
                return None
            await self.store.delete(key)
        record = msgspec.json.decode(raw, type=TransitRecord)
        if record.owner != owner:
            return None
        return record

    async def discard(self, session_id: str) -> None:
        """Drop the record parked for ``session_id``, if any."""
        await self.store.delete(self._key(session_id))

    async def sweep(self) -> None:
        """Reap expired records.

        Nothing else does: ``MemoryStore`` only drops an expired entry when
        someone reads that exact key, and an unclaimed record is by
        definition never read.

        ``delete_expired`` is not on the ``Store`` ABC -- ``MemoryStore``,
        ``FileStore`` and ``RedisStore`` each define their own -- so a store
        that does not have it is one that reaps for itself, and the sweep is
        a no-op against it.
        """
        reap = getattr(self.store, "delete_expired", None)
        if reap is not None:
            await reap()


@asynccontextmanager
async def transit_sweeper(
    transit: TransitStore, interval: float = SWEEP_INTERVAL_SECONDS
) -> AsyncIterator[TransitStore]:
    """Lifespan manager running :meth:`TransitStore.sweep` on a schedule."""

    async def loop() -> None:
        while True:
            await asyncio.sleep(interval)
            await transit.sweep()

    task = asyncio.create_task(loop(), name="chainlit-transit-sweeper")
    try:
        yield transit
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
