import pytest

import chainlit.transit as transit


@pytest.fixture(autouse=True)
def clean_store():
    transit.clear()
    yield
    transit.clear()


def test_store_and_pop_roundtrip():
    transit.store("s1", "find a phone case", owner="user1")
    assert transit.pop("s1", "user1").value == "find a phone case"
    # A record can only be taken once.
    assert transit.pop("s1", "user1") is transit.NO_TRANSIT


def test_pop_missing_returns_sentinel():
    assert transit.pop("missing", "user1") is transit.NO_TRANSIT


def test_falsy_values_are_valid_transits():
    for value in ("", 0, False):
        transit.store("s1", value, owner=None)
        assert transit.pop("s1", None).value == value


def test_none_owner_matches_none_owner():
    # No-auth apps park and take records with owner=None.
    transit.store("s1", "hello", owner=None)
    assert transit.pop("s1", None).value == "hello"


def test_pop_rejects_foreign_owner():
    transit.store("s1", "secret", owner="user1")
    assert transit.pop("s1", "user2") is transit.NO_TRANSIT
    assert transit.pop("s1", None) is transit.NO_TRANSIT


def test_store_nothing_clears_record():
    transit.store("s1", "parked", owner="user1")
    transit.store("s1", None, owner="user1")
    assert transit.pop("s1", "user1") is transit.NO_TRANSIT


def test_store_accepts_arbitrary_objects():
    payload = {"query": "phone case", "product_id": 42}
    transit.store("s1", payload, owner="user1")
    assert transit.pop("s1", "user1").value == payload


def test_record_carries_parent_thread_id():
    transit.store("s1", "message", owner="user1", parent="thread-a")
    record = transit.pop("s1", "user1")
    assert record.value == "message"
    assert record.parent == "thread-a"


def test_parent_only_record_is_parked():
    # A switch without a transit message still hands over the parent link.
    transit.store("s1", None, owner="user1", parent="thread-a")
    record = transit.pop("s1", "user1")
    assert record is not transit.NO_TRANSIT
    assert record.value is None
    assert record.parent == "thread-a"


def test_parent_defaults_to_none():
    transit.store("s1", "message", owner=None)
    assert transit.pop("s1", None).parent is None


def test_reassign_moves_record():
    transit.store("old", "carried over", owner="user1", parent="thread-a")
    transit.reassign("old", "new")
    assert transit.pop("old", "user1") is transit.NO_TRANSIT
    record = transit.pop("new", "user1")
    assert record.value == "carried over"
    assert record.parent == "thread-a"


def test_reassign_missing_is_noop():
    transit.reassign("old", "new")
    assert transit.pop("new", None) is transit.NO_TRANSIT


def test_expired_record_is_gone(monkeypatch: pytest.MonkeyPatch):
    now = 1000.0
    monkeypatch.setattr(transit.time, "monotonic", lambda: now)
    transit.store("s1", "stale", owner="user1")

    monkeypatch.setattr(
        transit.time,
        "monotonic",
        lambda: now + transit.TRANSIT_TTL_SECONDS + 1,
    )
    assert transit.pop("s1", "user1") is transit.NO_TRANSIT


def test_full_store_rejects_new_records(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(transit, "MAX_TRANSIT_RECORDS", 2)
    transit.store("s1", "one", owner=None)
    transit.store("s2", "two", owner=None)
    # New key is rejected; nothing already parked is evicted.
    transit.store("s3", "three", owner=None)
    assert transit.pop("s3", None) is transit.NO_TRANSIT
    assert transit.pop("s1", None).value == "one"
    assert transit.pop("s2", None).value == "two"


def test_full_store_still_updates_existing_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(transit, "MAX_TRANSIT_RECORDS", 1)
    transit.store("s1", "one", owner=None)
    transit.store("s1", "updated", owner=None)
    assert transit.pop("s1", None).value == "updated"
