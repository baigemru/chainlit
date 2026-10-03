"""``cl.Composer`` as a model, the frame it sends, and the record it leaves.

Each case asserts on the model and on the wire or the writer together,
because the point of the primitive is that they cannot disagree: the frame is
read off ``session.composer``, the thread row is written from it, and a
resume rebuilds it from the row.
"""

from unittest.mock import Mock

import pytest_asyncio

import chainlit as cl
from chainlit import persist
from chainlit.persistence.writer import PatchThread, SessionWriter, WriterRegistry
from chainlit.protocol.codec import encode_server
from chainlit.protocol.server import ComposerState
from chainlit.ws.composer import COMPOSER_META_KEY, composer_meta, state_from_meta
from tests.conftest import bind_context


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


def frames(session) -> list[ComposerState]:
    return [f for f in session.outbound.pending_frames if isinstance(f, ComposerState)]


def writer_for(session) -> SessionWriter:
    """A writer with its gate shut, so what it was handed is readable in order."""
    writer = SessionWriter(
        Mock(storage=None),
        session.thread_id,
        registry=WriterRegistry(),
        hold_until_interaction=True,
    )
    session.writer = writer
    return writer


def records(writer: SessionWriter) -> list:
    return [
        op.patch.metadata[COMPOSER_META_KEY]
        for op in writer.held
        if isinstance(op, PatchThread)
        and isinstance(op.patch.metadata, dict)
        and COMPOSER_META_KEY in op.patch.metadata
    ]


class TestTheModel:
    async def test_set_states_the_composer_and_sends_it(self, ctx, session):
        await cl.Composer.set(placeholder="Add a correction", hint="41 of 60 left")

        assert session.composer == ComposerState(
            placeholder="Add a correction", hint="41 of 60 left"
        )
        assert frames(session) == [session.composer]
        assert cl.Composer.state() is session.composer

    async def test_set_replaces_the_whole_state(self, ctx, session):
        # Not a merge: a client that reconnects between two calls is told
        # what the composer is, and a placeholder kept by a merge would be a
        # state no single call ever asked for.
        await cl.Composer.set(placeholder="Add a correction")
        await cl.Composer.set(hint="done")

        assert session.composer == ComposerState(hint="done")
        assert frames(session)[-1] == ComposerState(hint="done")

    async def test_clear_is_the_default_said_out_loud(self, ctx, session):
        await cl.Composer.set(placeholder="x", hint="y")
        await cl.Composer.clear()

        assert session.composer == ComposerState()
        # A bare frame, not silence: the client may be holding the old hint.
        assert encode_server(frames(session)[-1]) == b'{"t":"composer.state"}'

    async def test_an_empty_string_is_an_answer(self, ctx, session):
        # "" takes a profile's hint away; it must reach the client as "",
        # not be folded into the default by omit_defaults.
        await cl.Composer.set(hint="")

        assert encode_server(frames(session)[-1]) == b'{"t":"composer.state","hint":""}'


class TestTheRecord:
    async def test_every_call_writes_the_thread_row(self, ctx, session):
        writer = writer_for(session)

        await cl.Composer.set(placeholder="Add a correction")
        await cl.Composer.clear()

        # The default is written as None, which the metadata merge reads as
        # "delete the key": a cleared composer leaves no record behind.
        assert records(writer) == [{"placeholder": "Add a correction"}, None]

    async def test_no_writer_no_write(self, ctx, session):
        session.writer = None
        await cl.Composer.set(placeholder="x")
        assert frames(session) == [ComposerState(placeholder="x")]

    async def test_thread_state_carries_it(self, ctx, session):
        await cl.Composer.set(hint="41 of 60 left")
        assert persist.thread_state(session)[COMPOSER_META_KEY] == {
            "hint": "41 of 60 left"
        }

    async def test_an_application_key_cannot_shadow_it(self, ctx, session):
        # It shares the row's metadata with user_session; the engine's own
        # record wins on the way out ...
        session.state[COMPOSER_META_KEY] = {"placeholder": "forged"}
        await cl.Composer.set(placeholder="real")
        assert persist.thread_state(session)[COMPOSER_META_KEY] == {
            "placeholder": "real"
        }

    def test_and_never_comes_back_into_the_applications_half(self):
        engine, app = persist.split_engine_metadata(
            {COMPOSER_META_KEY: {"hint": "h"}, "mine": 1}
        )
        assert engine == {COMPOSER_META_KEY: {"hint": "h"}}
        assert app == {"mine": 1}

    def test_a_shared_thread_does_not_carry_it(self):
        from chainlit.controllers.project import PRIVATE_METADATA_KEYS

        assert COMPOSER_META_KEY in PRIVATE_METADATA_KEYS


class TestTheRoundTrip:
    def test_meta_reads_back_as_the_state_it_was_written_from(self):
        state = ComposerState(placeholder="p", hint="h")
        assert state_from_meta(composer_meta(state)) == state

    def test_the_default_has_no_record(self):
        assert composer_meta(ComposerState()) is None
        assert state_from_meta(None) == ComposerState()

    def test_a_record_that_does_not_parse_is_the_default(self):
        # Inside the handshake a raise would make the thread unresumable.
        assert state_from_meta({"hint": 3}) == ComposerState()
        assert state_from_meta("garbage") == ComposerState()  # type: ignore[arg-type]
