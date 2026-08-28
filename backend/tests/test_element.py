"""Elements: the dict they produce, the blob they spool, the frame they send."""

import json
import uuid
from unittest.mock import Mock

import pytest
import pytest_asyncio

from chainlit.element import (
    Audio,
    CustomElement,
    Dataframe,
    Element,
    File,
    Image,
    Pdf,
    Task,
    TaskList,
    TaskStatus,
    Text,
    Video,
)
from chainlit.persistence.writer import (
    DeleteElement,
    SaveElement,
    SessionWriter,
    WriterRegistry,
    _HeldUpload,
)
from chainlit.protocol.server import ElementRemove, ElementUpsert
from tests.conftest import bind_context


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


@pytest.fixture
def held_writer(session):
    persistence = Mock()
    persistence.storage = None
    writer = SessionWriter(
        persistence,
        session.thread_id,
        registry=WriterRegistry(),
        hold_until_interaction=True,
    )
    session.writer = writer
    return writer


class TestElementBase:
    async def test_element_initialization_with_url(self, ctx):
        element = File(name="test_file", url="https://example.com/file.pdf")
        uuid.UUID(element.id)
        assert element.persisted is False
        assert element.updatable is False
        assert element.thread_id == "test_thread_id"

    async def test_element_initialization_with_content_or_path(self, ctx):
        assert File(name="f", content=b"test content").url is None
        assert File(name="f", path="/path/to/file.txt").content is None

    async def test_element_requires_url_path_or_content(self, ctx):
        with pytest.raises(ValueError, match="Must provide url, path or content"):
            File(name="test_file")

    async def test_element_to_dict(self, ctx):
        element = File(
            name="test_file", url="https://example.com/file.pdf", display="side"
        )
        element_dict = element.to_dict()
        assert element_dict["name"] == "test_file"
        assert element_dict["url"] == "https://example.com/file.pdf"
        assert element_dict["type"] == "file"
        assert element_dict["id"] == element.id
        assert element_dict["threadId"] == "test_thread_id"
        assert element_dict["display"] == "side"

    async def test_element_send_puts_the_element_on_the_wire(
        self, ctx, session, frames
    ):
        element = File(name="test_file", url="https://example.com/file.pdf")
        await element.send(for_id="message_123")
        assert element.for_id == "message_123"
        [upsert] = frames(session, ElementUpsert)
        assert upsert.element.id == element.id
        assert upsert.element.for_id == "message_123"
        assert upsert.element.url == "https://example.com/file.pdf"

    async def test_element_send_spools_a_blob_and_names_the_key(
        self, ctx, session, frames
    ):
        element = File(name="notes.txt", content=b"hello")
        await element.send(for_id="message_123")
        assert element.chainlit_key in session.files
        assert session.files[element.chainlit_key]["path"].read_bytes() == b"hello"
        assert (
            frames(session, ElementUpsert)[0].element.chainlit_key
            == element.chainlit_key
        )

    async def test_str_content_is_spooled_as_utf8(self, ctx, session):
        element = Text(name="note", content="héllo")
        await element.send(for_id="m")
        assert (
            session.files[element.chainlit_key]["path"].read_bytes() == "héllo".encode()
        )

    async def test_element_remove(self, ctx, session, frames):
        element = File(name="test_file", url="https://example.com/file.pdf")
        await element.remove()
        assert [r.id for r in frames(session, ElementRemove)] == [element.id]

    async def test_element_from_dict_file_and_image(self, ctx):
        file = Element.from_dict(
            {
                "id": "x",
                "name": "f",
                "type": "file",
                "url": "https://example.com/file.pdf",
            }
        )
        assert isinstance(file, File)
        image = Element.from_dict({"type": "image", "url": "https://example.com/i.png"})
        assert isinstance(image, Image)
        assert image.name == ""

    async def test_element_infer_type_from_mime(self):
        assert Element.infer_type_from_mime("image/png") == "image"
        assert Element.infer_type_from_mime("application/pdf") == "pdf"
        assert Element.infer_type_from_mime("audio/mp3") == "audio"
        assert Element.infer_type_from_mime("video/mp4") == "video"
        assert Element.infer_type_from_mime("text/plain") == "file"


class TestPersistence:
    async def test_send_queues_the_row_after_the_key_is_known(
        self, ctx, session, held_writer
    ):
        element = File(name="notes.txt", content=b"hello")
        await element.send(for_id="message_123")
        [op] = [op for op in held_writer.held if isinstance(op, SaveElement)]
        assert op.record.id == element.id
        assert op.record.chainlit_key == element.chainlit_key
        assert op.record.for_id == "message_123"
        assert op.record.type == "file"

    async def test_blob_is_offered_for_upload_when_storage_exists(
        self, ctx, session, held_writer
    ):
        held_writer.persistence.storage = Mock()
        await File(name="notes.txt", content=b"hello").send(for_id="m")
        assert any(isinstance(op, _HeldUpload) for op in held_writer.held)

    async def test_url_elements_have_nothing_to_upload(self, ctx, session, held_writer):
        held_writer.persistence.storage = Mock()
        await File(name="f", url="https://example.com/f").send(for_id="m")
        assert all(isinstance(op, SaveElement) for op in held_writer.held)

    async def test_persist_false_spools_but_writes_no_row(
        self, ctx, session, held_writer
    ):
        element = File(name="notes.txt", content=b"hello")
        await element.send(for_id="m", persist=False)
        assert element.chainlit_key in session.files
        assert held_writer.held == ()

    async def test_remove_deletes_the_row(self, ctx, session, held_writer):
        element = File(name="f", url="https://example.com/f")
        await element.remove()
        assert held_writer.held == (DeleteElement(element.id, "test_thread_id"),)


class TestTypedElements:
    async def test_image(self, ctx, session, frames):
        image = Image(name="i", url="https://example.com/i.png", size="large")
        assert image.type == "image"
        await image.send(for_id="m")
        assert frames(session, ElementUpsert)[0].element.size == "large"

    async def test_text(self, ctx):
        text = Text(name="t", content="Some text", language="python")
        assert text.type == "text"
        assert text.to_dict()["language"] == "python"

    async def test_pdf(self, ctx, session, frames):
        pdf = Pdf(name="doc", url="https://example.com/doc.pdf", page=3)
        assert pdf.mime == "application/pdf"
        await pdf.send(for_id="m")
        assert frames(session, ElementUpsert)[0].element.page == 3

    async def test_audio(self, ctx, session, frames):
        audio = Audio(name="a", url="https://example.com/a.mp3", auto_play=True)
        await audio.send(for_id="m")
        assert frames(session, ElementUpsert)[0].element.auto_play is True

    async def test_video(self, ctx, session, frames):
        video = Video(name="v", url="https://example.com/v.mp4", player_config={"x": 1})
        await video.send(for_id="m")
        shown = frames(session, ElementUpsert)[0].element
        assert shown.player_config == {"x": 1}
        assert shown.size == "medium"

    async def test_file_mime_falls_back_to_filename(self, ctx):
        file = File(name="notes.md", content=b"# hello\nmarkdown content")
        await file.send(for_id="message_123")
        assert file.mime == "text/markdown"

    async def test_file_mime_falls_back_to_path_filename(self, ctx, tmp_path):
        csv_path = tmp_path / "export.csv"
        csv_path.write_text("a,b\n1,2\n")
        file = File(name="data", path=str(csv_path))
        await file.send(for_id="message_123")
        assert file.mime == "text/csv"

    async def test_url_mime_from_the_url(self, ctx):
        file = File(name="f", url="https://example.com/archive.zip")
        await file.send(for_id="m")
        assert file.mime == "application/zip"


class TestTaskListElement:
    async def test_tasklist_initialization(self, ctx):
        tasklist = TaskList(name="test_tasklist")
        assert tasklist.type == "tasklist"
        assert tasklist.tasks == []
        assert tasklist.status == "Ready"
        assert tasklist.updatable is True

    async def test_tasklist_send_serialises_the_tasks(self, ctx, session, frames):
        tasklist = TaskList(status="In Progress")
        await tasklist.add_task(
            Task(title="Test Task", status=TaskStatus.DONE, forId="s1")
        )
        await tasklist.send()
        payload = json.loads(tasklist.content)
        assert payload == {
            "status": "In Progress",
            "tasks": [{"title": "Test Task", "status": "done", "forId": "s1"}],
        }
        assert (
            session.files[tasklist.chainlit_key]["path"].read_text() == tasklist.content
        )
        assert frames(session, ElementUpsert)[0].element.for_id == ""

    async def test_tasklist_update_respools(self, ctx, session, frames):
        tasklist = TaskList()
        await tasklist.send()
        first = tasklist.chainlit_key
        await tasklist.add_task(Task(title="later"))
        await tasklist.update()
        assert tasklist.chainlit_key != first
        assert len(frames(session, ElementUpsert)) == 2

    def test_task_status_enum(self):
        assert [s.value for s in TaskStatus] == ["ready", "running", "failed", "done"]


class TestCustomElement:
    async def test_custom_element_initialization(self, ctx):
        custom = CustomElement(name="test_custom", props={"key1": "value1", "key2": 42})
        assert custom.type == "custom"
        assert custom.mime == "application/json"
        assert custom.updatable is True
        assert json.loads(custom.content) == {"key1": "value1", "key2": 42}

    async def test_custom_element_update_resends(self, ctx, session, frames):
        custom = CustomElement(name="test_custom", props={"key": "value"})
        await custom.send(for_id="message_123")
        custom.props["key"] = "changed"
        custom.content = json.dumps(custom.props)
        await custom.update()
        shown = frames(session, ElementUpsert)
        assert [e.element.props["key"] for e in shown] == ["value", "changed"]
        assert shown[1].element.for_id == "message_123"


class TestElementEdgeCases:
    async def test_element_with_custom_id_and_keys(self, ctx):
        custom_id = str(uuid.uuid4())
        element = File(
            id=custom_id,
            name="f",
            url="https://example.com/f",
            object_key="s3://bucket/key",
            chainlit_key="chainlit_key_123",
        )
        assert element.id == custom_id
        assert element.to_dict()["objectKey"] == "s3://bucket/key"
        assert element.to_dict()["chainlitKey"] == "chainlit_key_123"

    async def test_element_send_without_url_or_key_raises_error(self, ctx, session):
        async def no_key(**kwargs):
            return {"id": None}

        session.persist_file = no_key
        element = File(name="test_file", content=b"test content")
        with pytest.raises(ValueError, match="Must provide url or chainlit key"):
            await element.send(for_id="message_123", persist=False)

    async def test_element_id_uniqueness(self, ctx):
        ids = {File(name="f", url="https://example.com/f").id for _ in range(3)}
        assert len(ids) == 3


class TestDataframeElement:
    async def test_dataframe_with_pandas(self, ctx):
        pandas = pytest.importorskip("pandas")
        df = pandas.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        element = Dataframe(name="df", data=df)
        assert element.type == "dataframe"
        assert element.size == "large"
        assert json.loads(element.content)["columns"] == ["a", "b"]

    async def test_dataframe_with_polars(self, ctx):
        polars = pytest.importorskip("polars")
        df = polars.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        element = Dataframe(name="df", data=df)
        assert json.loads(element.content) == {
            "columns": ["a", "b"],
            "index": [0, 1],
            "data": [[1, "x"], [2, "y"]],
        }

    async def test_dataframe_with_invalid_data(self, ctx):
        with pytest.raises(
            TypeError, match=r"must be a pandas\.DataFrame or polars\.DataFrame"
        ):
            Dataframe(name="df", data={"a": [1]})


async def test_from_dict_pdf_reconstructs_pdf(ctx):
    element = Element.from_dict(
        {"type": "pdf", "url": "https://example.com/doc.pdf", "page": 2, "name": "doc"}
    )
    assert isinstance(element, Pdf)
    assert element.page == 2
