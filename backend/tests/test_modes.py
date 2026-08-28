"""Tests for Modes system functionality."""

import pytest
import pytest_asyncio

import chainlit as cl
from chainlit.mode import Mode, ModeOption
from chainlit.protocol.server import StepUpsert
from tests.conftest import bind_context


@pytest_asyncio.fixture
async def ctx(session):
    async with bind_context(session) as bound:
        yield bound


@pytest.fixture
def mock_modes():
    """Fixture providing sample modes for testing."""
    return [
        Mode(
            id="model",
            name="Model",
            options=[
                ModeOption(
                    id="gpt-4",
                    name="GPT-4",
                    description="Most capable model",
                    icon="sparkles",
                    default=True,
                ),
                ModeOption(
                    id="gpt-3.5-turbo",
                    name="GPT-3.5 Turbo",
                    description="Fast and efficient",
                    icon="bolt",
                    default=False,
                ),
            ],
        ),
        Mode(
            id="reasoning",
            name="Reasoning",
            options=[
                ModeOption(id="high", name="High", description="Maximum depth"),
                ModeOption(
                    id="medium", name="Medium", description="Balanced", default=True
                ),
                ModeOption(id="low", name="Low", description="Quick responses"),
            ],
        ),
    ]


@pytest.mark.asyncio
class TestModeOption:
    """Test suite for ModeOption dataclass."""

    def test_mode_option_required_fields(self):
        """Test ModeOption with required fields only."""
        option = ModeOption(id="test", name="Test Option")

        assert option.id == "test"
        assert option.name == "Test Option"
        assert option.description is None
        assert option.icon is None
        assert option.default is False

    def test_mode_option_all_fields(self):
        """Test ModeOption with all fields."""
        option = ModeOption(
            id="gpt-4",
            name="GPT-4",
            description="Most capable model",
            icon="sparkles",
            default=True,
        )

        assert option.id == "gpt-4"
        assert option.name == "GPT-4"
        assert option.description == "Most capable model"
        assert option.icon == "sparkles"
        assert option.default is True

    def test_mode_option_to_dict(self):
        """Test ModeOption serialization."""
        option = ModeOption(
            id="test",
            name="Test",
            description="Test desc",
            icon="star",
            default=True,
        )

        option_dict = option.to_dict()

        assert option_dict["id"] == "test"
        assert option_dict["name"] == "Test"
        assert option_dict["description"] == "Test desc"
        assert option_dict["icon"] == "star"
        assert option_dict["default"] is True


@pytest.mark.asyncio
class TestMode:
    """Test suite for Mode dataclass."""

    def test_mode_creation(self, mock_modes):
        """Test Mode creation with options."""
        mode = mock_modes[0]

        assert mode.id == "model"
        assert mode.name == "Model"
        assert len(mode.options) == 2

    def test_mode_to_dict(self, mock_modes):
        """Test Mode serialization."""
        mode = mock_modes[0]
        mode_dict = mode.to_dict()

        assert mode_dict["id"] == "model"
        assert mode_dict["name"] == "Model"
        assert len(mode_dict["options"]) == 2
        assert mode_dict["options"][0]["id"] == "gpt-4"

    def test_mode_default_option(self, mock_modes):
        """Test finding default option in mode."""
        mode = mock_modes[0]

        default_option = next(
            (opt for opt in mode.options if opt.default), mode.options[0]
        )

        assert default_option.id == "gpt-4"

    def test_mode_fallback_to_first(self, mock_modes):
        """Test fallback to first option when no default set."""
        mode = Mode(
            id="test",
            name="Test",
            options=[
                ModeOption(id="opt1", name="Option 1"),
                ModeOption(id="opt2", name="Option 2"),
            ],
        )

        default_option = next(
            (opt for opt in mode.options if opt.default),
            mode.options[0] if mode.options else None,
        )

        assert default_option is not None
        assert default_option.id == "opt1"


class TestMessageWithModes:
    """Test suite for Message with modes field."""

    async def test_message_with_modes(self, ctx):
        modes = {"model": "gpt-4", "reasoning": "high"}
        message = cl.Message(content="Test message", modes=modes)
        assert message.modes == modes
        assert message.to_dict()["modes"] == modes

    async def test_message_from_dict_with_modes(self, ctx):
        message = cl.Message.from_dict(
            {
                "id": "test-id",
                "modes": {"model": "gpt-3.5-turbo", "reasoning": "low"},
                "type": "user_message",
                "createdAt": "2024-01-01T00:00:00",
                "output": "Test message",
            }
        )
        assert message.modes == {"model": "gpt-3.5-turbo", "reasoning": "low"}

    async def test_message_without_modes(self, ctx):
        message = cl.Message(content="Test message")
        assert message.modes is None
        assert message.to_dict()["modes"] is None

    async def test_message_send_with_modes(self, ctx, session, frames, monkeypatch):
        monkeypatch.setattr("chainlit.message.config.code.author_rename", None)
        modes = {"model": "gpt-4", "reasoning": "high"}
        message = cl.Message(content="Test", modes=modes)
        assert await message.send() is message
        assert frames(session, StepUpsert)[0].step.modes == modes


@pytest.mark.asyncio
class TestModeExports:
    """Test that Mode and ModeOption are properly exported."""

    def test_mode_exported_from_chainlit(self):
        """Test Mode is exported from chainlit package."""
        assert hasattr(cl, "Mode")
        assert cl.Mode is Mode

    def test_mode_option_exported_from_chainlit(self):
        """Test ModeOption is exported from chainlit package."""
        assert hasattr(cl, "ModeOption")
        assert cl.ModeOption is ModeOption
