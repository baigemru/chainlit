"""A step's buttons, written beside the application's metadata.

The buttons live under one engine key of ``steps.metadata``, and the column
is written whole by every save of the step -- ``Message.update()`` included,
whose metadata has never heard of them. These are the two writes that have to
leave each other alone.
"""

from chainlit.persistence import StepRecord, UnitOfWork
from chainlit.persistence.records import STEP_ACTIONS_KEY
from tests.persistence.conftest import make_thread, new_id

ROW = [{"id": "a1", "name": "again", "forId": "m"}]


async def _message(uow: UnitOfWork, metadata: dict) -> tuple[str, str]:
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(
            id=step_id,
            type="assistant_message",
            thread_id=thread_id,
            output="done",
            metadata=metadata,
        )
    )
    return thread_id, step_id


async def test_a_later_save_of_the_step_keeps_its_buttons(uow: UnitOfWork) -> None:
    """The edit of a message used to be where its buttons would have gone."""
    thread_id, step_id = await _message(uow, {"anchor": "none"})
    await uow.steps.patch_metadata(step_id, {STEP_ACTIONS_KEY: ROW})

    await uow.steps.save(
        StepRecord(
            id=step_id,
            type="assistant_message",
            thread_id=thread_id,
            output="done, edited",
            metadata={"anchor": "top"},
        )
    )

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.metadata == {"anchor": "top", STEP_ACTIONS_KEY: ROW}


async def test_a_save_that_names_the_buttons_writes_them(uow: UnitOfWork) -> None:
    """A repeated delivery of the same id replaces its row, not appends to it."""
    thread_id, step_id = await _message(uow, {STEP_ACTIONS_KEY: ROW})
    replaced = [{"id": "a2", "name": "other", "forId": step_id}]

    await uow.steps.save(
        StepRecord(
            id=step_id,
            type="assistant_message",
            thread_id=thread_id,
            metadata={STEP_ACTIONS_KEY: replaced},
        )
    )

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.metadata == {STEP_ACTIONS_KEY: replaced}


async def test_the_patch_leaves_the_application_keys_alone(uow: UnitOfWork) -> None:
    _, step_id = await _message(uow, {"anchor": "none", "resume_policy": "keep"})

    await uow.steps.patch_metadata(step_id, {STEP_ACTIONS_KEY: ROW})
    await uow.steps.patch_metadata(step_id, {STEP_ACTIONS_KEY: None})

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.metadata == {"anchor": "none", "resume_policy": "keep"}


async def test_a_patch_for_a_step_never_written_writes_nothing(
    uow: UnitOfWork,
) -> None:
    step_id = new_id()
    await uow.steps.patch_metadata(step_id, {STEP_ACTIONS_KEY: ROW})
    assert await uow.steps.fetch(step_id) is None


def test_a_stored_button_the_wire_refuses_is_dropped_not_fatal() -> None:
    """One retired variant must not cost the person the whole conversation."""
    from chainlit.runner import _stored_actions

    rows = [
        {"id": "a1", "name": "go", "variant": "retired"},
        {"id": "a2", "name": "stay", "forId": "elsewhere"},
        "not a row",
    ]
    assert [(b.id, b.for_id) for b in _stored_actions(rows, "m1")] == [("a2", "m1")]
