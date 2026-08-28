import os

from dotenv import load_dotenv

# ruff: noqa: E402
# Keep this here to ensure imports have environment available.
env_file = os.getenv("CHAINLIT_ENV_FILE", ".env")
env_found = load_dotenv(dotenv_path=os.path.join(os.getcwd(), env_file))

from chainlit.logger import logger

if env_found:
    logger.info(f"Loaded {env_file} file")

import asyncio

from literalai import ChatGeneration, CompletionGeneration, GenerationMessage

import chainlit.input_widget as input_widget
from chainlit.action import Action
from chainlit.chat_context import chat_context
from chainlit.context import context
from chainlit.element import (
    Audio,
    CustomElement,
    Dataframe,
    File,
    Image,
    Pdf,
    Plotly,
    Pyplot,
    Task,
    TaskList,
    TaskStatus,
    Text,
    Video,
)
from chainlit.message import (
    AskActionMessage,
    AskElementMessage,
    AskFileMessage,
    AskUserMessage,
    ErrorMessage,
    Message,
)
from chainlit.mode import Mode, ModeOption
from chainlit.sidebar import ElementSidebar
from chainlit.step import Step, step
from chainlit.types import (
    AskSlotBusyError,
    ChatProfile,
    Starter,
    StarterCategory,
)
from chainlit.user import PersistedUser, User
from chainlit.user_session import user_session
from chainlit.version import __version__

from .callbacks import (
    action_callback,
    author_rename,
    oauth_callback,
    on_app_shutdown,
    on_app_startup,
    on_chat_end,
    on_chat_resume,
    on_chat_start,
    on_feedback,
    on_message,
    on_profile_start,
    on_shared_thread_view,
    on_stop,
    on_thread_ready,
    password_auth_callback,
    set_chat_profiles,
    set_starter_categories,
    set_starters,
)


def sleep(duration: int):
    """
    Sleep for a given duration.
    Args:
        duration (int): The duration in seconds.
    """
    return asyncio.sleep(duration)


__all__ = [
    "Action",
    "AskActionMessage",
    "AskElementMessage",
    "AskFileMessage",
    "AskSlotBusyError",
    "AskUserMessage",
    "Audio",
    "ChatGeneration",
    "ChatProfile",
    "CompletionGeneration",
    "CustomElement",
    "Dataframe",
    "ElementSidebar",
    "ErrorMessage",
    "File",
    "GenerationMessage",
    "Image",
    "Message",
    "Mode",
    "ModeOption",
    "Pdf",
    "PersistedUser",
    "Plotly",
    "Pyplot",
    "Starter",
    "StarterCategory",
    "Step",
    "Task",
    "TaskList",
    "TaskStatus",
    "Text",
    "User",
    "Video",
    "__version__",
    "action_callback",
    "author_rename",
    "chat_context",
    "context",
    "input_widget",
    "logger",
    "oauth_callback",
    "on_app_shutdown",
    "on_app_startup",
    "on_chat_end",
    "on_chat_resume",
    "on_chat_start",
    "on_feedback",
    "on_message",
    "on_profile_start",
    "on_shared_thread_view",
    "on_stop",
    "on_thread_ready",
    "password_auth_callback",
    "set_chat_profiles",
    "set_starter_categories",
    "set_starters",
    "sleep",
    "step",
    "user_session",
]


def __dir__():
    return __all__
