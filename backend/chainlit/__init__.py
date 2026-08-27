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
from typing import TYPE_CHECKING, Any, Dict

from literalai import ChatGeneration, CompletionGeneration, GenerationMessage
from pydantic.dataclasses import dataclass

import chainlit.input_widget as input_widget
from chainlit.action import Action
from chainlit.auth import current_user
from chainlit.cache import cache
from chainlit.chat_context import chat_context
from chainlit.chat_settings import ChatSettings
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
from chainlit.sync import make_async, run_sync
from chainlit.types import (
    AskSlotBusyError,
    ChatProfile,
    InputAudioChunk,
    OutputAudioChunk,
    Starter,
    StarterCategory,
)
from chainlit.user import PersistedUser, User
from chainlit.user_session import user_session
from chainlit.utils import make_module_getattr
from chainlit.version import __version__

from .callbacks import (
    action_callback,
    author_rename,
    data_layer,
    header_auth_callback,
    oauth_callback,
    on_app_shutdown,
    on_app_startup,
    on_audio_chunk,
    on_audio_end,
    on_audio_start,
    on_chat_end,
    on_chat_resume,
    on_chat_start,
    on_feedback,
    on_logout,
    on_mcp_connect,
    on_mcp_disconnect,
    on_message,
    on_profile_start,
    on_settings_edit,
    on_settings_update,
    on_shared_thread_view,
    on_slack_reaction_added,
    on_stop,
    on_thread_ready,
    on_window_message,
    password_auth_callback,
    send_window_message,
    server_route,
    set_chat_profiles,
    set_starter_categories,
    set_starters,
)

if TYPE_CHECKING:
    from chainlit.langchain.callbacks import (
        AsyncLangchainCallbackHandler,
        LangchainCallbackHandler,
    )
    from chainlit.llama_index.callbacks import LlamaIndexCallbackHandler
    from chainlit.mistralai import instrument_mistralai
    from chainlit.openai import instrument_openai
    from chainlit.semantic_kernel import SemanticKernelFilter


def sleep(duration: int):
    """
    Sleep for a given duration.
    Args:
        duration (int): The duration in seconds.
    """
    return asyncio.sleep(duration)


@dataclass()
class CopilotFunction:
    name: str
    args: Dict[str, Any]

    def acall(self):
        return context.emitter.send_call_fn(self.name, self.args)


__getattr__ = make_module_getattr(
    {
        "LangchainCallbackHandler": "chainlit.langchain.callbacks",
        "AsyncLangchainCallbackHandler": "chainlit.langchain.callbacks",
        "LlamaIndexCallbackHandler": "chainlit.llama_index.callbacks",
        "instrument_openai": "chainlit.openai",
        "instrument_mistralai": "chainlit.mistralai",
        "SemanticKernelFilter": "chainlit.semantic_kernel",
        "server": "chainlit.server",
    }
)

__all__ = [
    "Action",
    "AskActionMessage",
    "AskElementMessage",
    "AskFileMessage",
    "AskSlotBusyError",
    "AskUserMessage",
    "AsyncLangchainCallbackHandler",
    "Audio",
    "ChatGeneration",
    "ChatProfile",
    "ChatSettings",
    "CompletionGeneration",
    "CopilotFunction",
    "CustomElement",
    "Dataframe",
    "ElementSidebar",
    "ErrorMessage",
    "File",
    "GenerationMessage",
    "Image",
    "InputAudioChunk",
    "LangchainCallbackHandler",
    "LlamaIndexCallbackHandler",
    "Message",
    "Mode",
    "ModeOption",
    "OutputAudioChunk",
    "Pdf",
    "PersistedUser",
    "Plotly",
    "Pyplot",
    "SemanticKernelFilter",
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
    "cache",
    "chat_context",
    "context",
    "current_user",
    "data_layer",
    "header_auth_callback",
    "input_widget",
    "instrument_mistralai",
    "instrument_openai",
    "make_async",
    "oauth_callback",
    "on_app_shutdown",
    "on_app_startup",
    "on_audio_chunk",
    "on_audio_end",
    "on_audio_start",
    "on_chat_end",
    "on_chat_resume",
    "on_chat_start",
    "on_feedback",
    "on_logout",
    "on_mcp_connect",
    "on_mcp_disconnect",
    "on_message",
    "on_profile_start",
    "on_settings_edit",
    "on_settings_update",
    "on_shared_thread_view",
    "on_slack_reaction_added",
    "on_stop",
    "on_thread_ready",
    "on_window_message",
    "password_auth_callback",
    "run_sync",
    "send_window_message",
    "server_route",
    "set_chat_profiles",
    "set_starter_categories",
    "set_starters",
    "sleep",
    "step",
    "switch_chat_profile",
    "user_session",
]


def __dir__():
    return __all__


async def switch_chat_profile(name: str, payload: Any = None) -> bool:
    """Switch this session's chat profile in place.

    Same session, same thread, transcript kept; the app's
    ``@cl.on_profile_start`` hook runs instead of ``on_chat_start``.
    Callable from ``on_message``, ``on_chat_start`` and action callbacks —
    the context is already established there.

    Requires the ``hot_swap_chat_profile`` feature flag. Returns False when
    the flag is off, the session is not a websocket session, or the name is
    empty or not among this user's profiles.
    """
    from chainlit.context import ChainlitContextException
    from chainlit.session import WebsocketSession
    from chainlit.socket import perform_profile_switch

    # §3.3 promises False, not an exception, so a call from background code
    # with no context must not raise.
    try:
        session = context.session
    except ChainlitContextException:
        return False
    if not isinstance(session, WebsocketSession):
        return False
    return await perform_profile_switch(session, name, payload=payload, source="server")
