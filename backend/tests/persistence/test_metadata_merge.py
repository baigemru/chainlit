"""Thread metadata is merged in the database, not read-modify-written."""

import uuid

from chainlit.persistence import ThreadPatch, UnitOfWork
from chainlit.persistence.statements import merge_thread_metadata
from tests.persistence.conftest import at, iso, make_thread


async def test_keys_are_merged_over_the_stored_object(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow, metadata={"a": 1, "b": 2})

    await uow.threads.patch(thread_id, ThreadPatch(metadata={"b": 3, "c": 4}))

    stored = await uow.threads.fetch(thread_id)
    assert stored is not None
    assert stored.metadata == {"a": 1, "b": 3, "c": 4}


async def test_a_none_value_deletes_the_key(uow: UnitOfWork) -> None:
    """The only way to remove a key: the wire has no other spelling for it."""
    thread_id = await make_thread(uow, metadata={"a": 1, "secret": "x"})

    await uow.threads.patch(thread_id, ThreadPatch(metadata={"secret": None}))

    stored = await uow.threads.fetch(thread_id)
    assert stored is not None
    assert stored.metadata == {"a": 1}


async def test_an_unset_metadata_leaves_the_stored_object_alone(
    uow: UnitOfWork,
) -> None:
    thread_id = await make_thread(uow, metadata={"a": 1})

    await uow.threads.patch(thread_id, ThreadPatch(name="renamed"))

    stored = await uow.threads.fetch(thread_id)
    assert stored is not None
    assert stored.name == "renamed"
    assert stored.metadata == {"a": 1}


async def test_an_empty_patch_is_a_no_op_on_metadata(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow, metadata={"a": 1})

    await uow.threads.patch(thread_id, ThreadPatch(metadata={}))

    stored = await uow.threads.fetch(thread_id)
    assert stored is not None
    assert stored.metadata == {"a": 1}


async def test_a_key_the_patch_never_mentions_is_untouched(uow: UnitOfWork) -> None:
    """user_session travels in here, and it is not flat.

    This only exercises a sibling key; the two tests below are the ones that
    pin what happens to the nested object itself.
    """
    thread_id = await make_thread(uow, metadata={"session": {"chat_profile": "gpt"}})

    await uow.threads.patch(thread_id, ThreadPatch(metadata={"tab": 2}))

    stored = await uow.threads.fetch(thread_id)
    assert stored is not None
    assert stored.metadata == {"session": {"chat_profile": "gpt"}, "tab": 2}


async def test_a_nested_object_is_replaced_not_merged(uow: UnitOfWork) -> None:
    """The merge is shallow: a top-level key is written over, whole.

    A recursive merge would keep ``chat_profile`` here. That is what SQLite's
    ``json_patch()`` does — it is RFC 7396 — and it disagrees with production,
    where the merge is ``metadata - keys || incoming``, and with the legacy
    layer, which computed ``{**stored, **incoming}`` in Python. The wire has
    no way to say "drop one nested key", so leaving stale nested keys behind
    is a bug the caller cannot work around.
    """
    thread_id = await make_thread(
        uow, metadata={"session": {"chat_profile": "gpt", "tab": 1}}
    )

    await uow.threads.patch(
        thread_id, ThreadPatch(metadata={"session": {"chat_profile": "claude"}})
    )

    stored = await uow.threads.fetch(thread_id)
    assert stored is not None
    assert stored.metadata == {"session": {"chat_profile": "claude"}}


async def test_a_nested_null_is_stored_rather_than_deleted(uow: UnitOfWork) -> None:
    """Only a *top-level* None deletes; deeper down it is just a value.

    RFC 7396 strips nulls at every level, so ``json_patch()`` would silently
    drop ``{"session": {"secret": None}}`` down to ``{"session": {}}``.
    """
    thread_id = await make_thread(uow, metadata={"a": 1})

    await uow.threads.patch(
        thread_id, ThreadPatch(metadata={"session": {"secret": None}})
    )

    stored = await uow.threads.fetch(thread_id)
    assert stored is not None
    assert stored.metadata == {"a": 1, "session": {"secret": None}}


async def test_a_key_with_a_path_metacharacter_round_trips(uow: UnitOfWork) -> None:
    """Metadata keys are user data, and SQLite addresses them by JSON path."""
    thread_id = await make_thread(uow, metadata={"a.b": 1, "c[0]": 2, 'q"k': 3, "d": 4})

    await uow.threads.patch(
        thread_id, ThreadPatch(metadata={"a.b": 9, "c[0]": None, 'q"k': None, "e$f": 5})
    )

    stored = await uow.threads.fetch(thread_id)
    assert stored is not None
    assert stored.metadata == {"a.b": 9, "d": 4, "e$f": 5}


async def test_the_merge_marks_the_thread_active(uow: UnitOfWork) -> None:
    """The merge is an UPDATE of its own, and it carries ``updatedAt``.

    Asserting this through ``patch()`` proves nothing — the upsert it runs
    first already writes ``updatedAt`` — so the statement is driven directly.
    """
    thread_id = await make_thread(uow, metadata={"a": 1}, updated_at=at(hour=9))

    await uow.session.execute(
        merge_thread_metadata(
            thread_id=uuid.UUID(thread_id),
            patch={"b": 2},
            updated_at=at(hour=13),
            dialect_name=uow.threads.dialect,
        )
    )

    stored = await uow.threads.fetch(thread_id)
    assert stored is not None
    assert stored.metadata == {"a": 1, "b": 2}
    assert stored.updated_at == iso(at(hour=13))
