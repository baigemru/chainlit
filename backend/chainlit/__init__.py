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

from chainlit.account import (
    OpenThread as AccountOpenThread,
    Refresh as AccountRefresh,
    Toast as AccountToast,
)
from chainlit.action import Action
from chainlit.background import run_in_background
from chainlit.chat_context import chat_context
from chainlit.context import context

# Renamed on the way out: inside the controller it is one of two spellings of
# the same blob (the other is the share route's), and the application only ever
# wants the one its own reader is allowed on.
from chainlit.controllers.project import author_file_url as element_file_url
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
from chainlit.host import (
    deliver_to_thread,
    persistence,
    refresh_account_badge,
    uow,
)
from chainlit.message import (
    AskActionMessage,
    AskElementMessage,
    AskFileMessage,
    AskUserMessage,
    ErrorMessage,
    Message,
)
from chainlit.sidebar import Sidebar
from chainlit.step import Step, step
from chainlit.types import (
    AskSlotBusyError,
    ChatProfile,
    Starter,
    StarterCategory,
    check_one_default_per_device,
    is_offered,
    matches_device,
    pick_default_profile,
)
from chainlit.user import PersistedUser, User
from chainlit.user_session import user_session
from chainlit.version import __version__

from .callbacks import (
    account,
    account_action,
    action_callback,
    author_rename,
    oauth_callback,
    on_account_badge,
    on_account_load,
    on_account_update,
    on_app_shutdown,
    on_app_startup,
    on_chat_end,
    on_chat_resume,
    on_chat_start,
    on_message,
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
    "AccountOpenThread",
    "AccountRefresh",
    "AccountToast",
    "Action",
    "AskActionMessage",
    "AskElementMessage",
    "AskFileMessage",
    "AskSlotBusyError",
    "AskUserMessage",
    "Audio",
    "ChatProfile",
    "CustomElement",
    "Dataframe",
    "ErrorMessage",
    "File",
    "Image",
    "Message",
    "Pdf",
    "PersistedUser",
    "Plotly",
    "Pyplot",
    "Sidebar",
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
    "account",
    "account_action",
    "action_callback",
    "author_rename",
    "chat_context",
    "check_one_default_per_device",
    "context",
    "deliver_to_thread",
    "element_file_url",
    "is_offered",
    "logger",
    "matches_device",
    "oauth_callback",
    "on_account_badge",
    "on_account_load",
    "on_account_update",
    "on_app_shutdown",
    "on_app_startup",
    "on_chat_end",
    "on_chat_resume",
    "on_chat_start",
    "on_message",
    "on_stop",
    "on_thread_ready",
    "password_auth_callback",
    "persistence",
    "pick_default_profile",
    "refresh_account_badge",
    "run_in_background",
    "set_chat_profiles",
    "set_starter_categories",
    "set_starters",
    "sleep",
    "step",
    "uow",
    "user_session",
]


def __dir__():
    return __all__
