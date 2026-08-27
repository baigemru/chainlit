"""The conditional step upsert.

Every case here is one the legacy ``COALESCE(NULLIF(...))`` upsert got wrong
or could not express at all.
"""

from chainlit.persistence import FeedbackRecord, StepRecord, UnitOfWork
from tests.persistence.conftest import at, iso, make_thread, new_id


async def test_omitted_fields_are_left_alone(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(
            id=step_id,
            type="assistant_message",
            thread_id=thread_id,
            name="assistant",
            input="the prompt",
            output="hel",
            metadata={"favorite": True},
            created_at=iso(at(hour=12)),
            start=iso(at(hour=12)),
            show_input="json",
        )
    )

    # A streaming token: output and nothing else.
    await uow.steps.save(
        StepRecord(
            id=step_id, type="assistant_message", thread_id=thread_id, output="hello"
        )
    )

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.output == "hello"
    assert stored.input == "the prompt"
    assert stored.name == "assistant"
    assert stored.metadata == {"favorite": True}


async def test_a_provided_empty_value_clears_the_column(uow: UnitOfWork) -> None:
    """The distinction UNSET buys: "" is a value, not a missing field.

    The legacy upsert wrapped every string in NULLIF(x, '') and so could never
    empty one.
    """
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(
            id=step_id, type="assistant_message", thread_id=thread_id, output="draft"
        )
    )
    await uow.steps.save(
        StepRecord(id=step_id, type="assistant_message", thread_id=thread_id, output="")
    )

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.output == ""


async def test_start_keeps_the_earliest_value(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(
            id=step_id,
            type="run",
            thread_id=thread_id,
            start=iso(at(hour=12)),
            end=iso(at(hour=12, minute=30)),
        )
    )
    # A later write must not move the step's beginning forward.
    await uow.steps.save(
        StepRecord(
            id=step_id,
            type="run",
            thread_id=thread_id,
            start=iso(at(hour=13)),
            end=iso(at(hour=13, minute=5)),
        )
    )

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.start == iso(at(hour=12))
    assert stored.end == iso(at(hour=13, minute=5))


async def test_start_moves_backwards_when_an_earlier_value_arrives(
    uow: UnitOfWork,
) -> None:
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(id=step_id, type="run", thread_id=thread_id, start=iso(at(hour=13)))
    )
    await uow.steps.save(
        StepRecord(id=step_id, type="run", thread_id=thread_id, start=iso(at(hour=12)))
    )

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.start == iso(at(hour=12))


async def test_start_survives_a_null_stored_value(uow: UnitOfWork) -> None:
    """SQLite's min() returns NULL if either argument is NULL.

    A step created without a start — the common case for a placeholder parent
    — would lose the first real start it is ever given if the fallback were a
    plain min().
    """
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(StepRecord(id=step_id, type="run", thread_id=thread_id))
    assert (await uow.steps.fetch(step_id)) is not None

    await uow.steps.save(
        StepRecord(id=step_id, type="run", thread_id=thread_id, start=iso(at(hour=12)))
    )

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.start == iso(at(hour=12))


async def test_a_placeholder_type_does_not_overwrite_a_real_one(
    uow: UnitOfWork,
) -> None:
    """A late placeholder parent must not demote a typed step.

    Steps arrive out of order: a child can be written before its parent, and
    the parent is then created as a stub with type 'run'. If the real parent
    was already stored, the stub must lose.
    """
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(id=step_id, type="assistant_message", thread_id=thread_id)
    )

    await uow.steps.save(StepRecord(id=step_id, type="run", thread_id=thread_id))

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.type == "assistant_message"


async def test_a_real_type_overwrites_a_placeholder(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(StepRecord(id=step_id, type="run", thread_id=thread_id))

    await uow.steps.save(StepRecord(id=step_id, type="tool", thread_id=thread_id))

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.type == "tool"


async def test_hidden_input_is_not_returned(uow: UnitOfWork) -> None:
    """showInput="false" means the input never reaches the client."""
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(
            id=step_id,
            type="tool",
            thread_id=thread_id,
            input="secret prompt",
            show_input=False,
        )
    )

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert stored.input == ""
    assert stored.show_input == "false"


async def test_feedback_comes_back_with_the_step(uow: UnitOfWork) -> None:
    thread_id = await make_thread(uow)
    step_id = new_id()
    await uow.steps.save(
        StepRecord(id=step_id, type="assistant_message", thread_id=thread_id)
    )
    feedback_id = await uow.feedbacks.save(
        FeedbackRecord(for_id=step_id, thread_id=thread_id, value=1, comment="good")
    )

    stored = await uow.steps.fetch(step_id)
    assert stored is not None
    assert isinstance(stored.feedback, FeedbackRecord)
    assert stored.feedback.id == feedback_id
    assert stored.feedback.value == 1
    assert stored.feedback.comment == "good"
