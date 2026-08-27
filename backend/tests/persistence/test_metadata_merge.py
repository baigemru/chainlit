"""Thread metadata is merged in the database, not read-modify-written."""

from chainlit.persistence import ThreadPatch, UnitOfWork
from tests.persistence.conftest import make_thread


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


async def test_nested_values_survive_the_merge(uow: UnitOfWork) -> None:
    """user_session travels in here, and it is not flat."""
    thread_id = await make_thread(uow, metadata={"session": {"chat_profile": "gpt"}})

    await uow.threads.patch(thread_id, ThreadPatch(metadata={"tab": 2}))

    stored = await uow.threads.fetch(thread_id)
    assert stored is not None
    assert stored.metadata == {"session": {"chat_profile": "gpt"}, "tab": 2}
