"""The decorators an application registers its hooks with.

Each one stores a wrapped function on ``config.code``; nothing here runs
them. Who runs what -- and on which task -- is ``chainlit.runner``, which
also owns the task indicator. That is why no decorator asks for a task any
more: the hook is launched as the session's task by whoever launches it,
and a second counter inside the wrapper would be the old socket.io
bookkeeping under a new name.
"""

import inspect
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union, overload

from chainlit.action import Action
from chainlit.config import config
from chainlit.message import Message
from chainlit.oauth_providers import get_configured_oauth_providers
from chainlit.step import Step, step
from chainlit.types import (
    ChatProfile,
    ProfileStartInfo,
    Starter,
    StarterCategory,
    ThreadDict,
)
from chainlit.user import User
from chainlit.utils import wrap_user_function


def on_app_startup(func: Callable[[], Union[Awaitable[None], None]]) -> Callable:
    """
    Hook to run code when the Chainlit application starts.
    Useful for initializing resources, loading models, setting up database connections, etc.
    The function can be synchronous or asynchronous.

    Args:
        func (Callable[[], Union[Awaitable[None], None]]): The startup hook to execute. Takes no arguments.

    Example:
        @cl.on_app_startup
        async def startup():
            print("Application is starting!")
            # Initialize resources here

    Returns:
        Callable[[], Union[Awaitable[None], None]]: The decorated startup hook.
    """
    config.code.on_app_startup = wrap_user_function(func)
    return func


def on_app_shutdown(func: Callable[[], Union[Awaitable[None], None]]) -> Callable:
    """
    Hook to run code when the Chainlit application shuts down.
    Useful for cleaning up resources, closing connections, saving state, etc.
    The function can be synchronous or asynchronous.

    Args:
        func (Callable[[], Union[Awaitable[None], None]]): The shutdown hook to execute. Takes no arguments.

    Example:
        @cl.on_app_shutdown
        async def shutdown():
            print("Application is shutting down!")
            # Clean up resources here

    Returns:
        Callable[[], Union[Awaitable[None], None]]: The decorated shutdown hook.
    """
    config.code.on_app_shutdown = wrap_user_function(func)
    return func


def password_auth_callback(
    func: Callable[[str, str], Awaitable[Optional[User]]],
) -> Callable:
    """
    Framework agnostic decorator to authenticate the user.

    Args:
        func (Callable[[str, str], Awaitable[Optional[User]]]): The authentication callback to execute. Takes the email and password as parameters.

    Example:
        @cl.password_auth_callback
        async def password_auth_callback(username: str, password: str) -> Optional[User]:

    Returns:
        Callable[[str, str], Awaitable[Optional[User]]]: The decorated authentication callback.
    """

    config.code.password_auth_callback = wrap_user_function(func)
    return func


def oauth_callback(
    func: Callable[
        [str, str, Dict[str, str], User, Optional[str]], Awaitable[Optional[User]]
    ],
) -> Callable:
    """
    Framework agnostic decorator to authenticate the user via oauth

    Args:
        func (Callable[[str, str, Dict[str, str], User, Optional[str]], Awaitable[Optional[User]]]): The authentication callback to execute.

    Example:
        @cl.oauth_callback
        async def oauth_callback(provider_id: str, token: str, raw_user_data: Dict[str, str], default_app_user: User, id_token: Optional[str]) -> Optional[User]:

    Returns:
        Callable[[str, str, Dict[str, str], User, Optional[str]], Awaitable[Optional[User]]]: The decorated authentication callback.
    """

    if len(get_configured_oauth_providers()) == 0:
        raise ValueError(
            "You must set the environment variable for at least one oauth provider to use oauth authentication."
        )

    config.code.oauth_callback = wrap_user_function(func)
    return func


def on_message(func: Callable) -> Callable:
    """
    Framework agnostic decorator to react to messages coming from the UI.
    The decorated function is called every time a new message is received.

    Args:
        func (Callable[[Message], Any]): The function to be called when a new message is received. Takes a cl.Message.

    Returns:
        Callable[[str], Any]: The decorated on_message function.
    """

    async def with_parent_id(message: Message):
        async with Step(name="on_message", type="run", parent_id=message.id) as s:
            s.input = message.content
            if len(inspect.signature(func).parameters) > 0:
                await func(message)
            else:
                await func()

    config.code.on_message = wrap_user_function(with_parent_id)
    return func


def on_chat_start(func: Callable) -> Callable:
    """
    Hook to react to the user websocket connection event.

    Args:
        func (Callable[], Any]): The connection hook to execute.

    Returns:
        Callable[], Any]: The decorated hook.
    """

    config.code.on_chat_start = wrap_user_function(
        step(func, name="on_chat_start", type="run")
    )
    return func


def on_chat_resume(func: Callable[[ThreadDict], Any]) -> Callable:
    """
    Hook to react to resume websocket connection event.

    Args:
        func (Callable[], Any]): The connection hook to execute.

    Returns:
        Callable[], Any]: The decorated hook.
    """

    config.code.on_chat_resume = wrap_user_function(func)
    return func


def on_thread_ready(func: Callable[[ThreadDict], Any]) -> Callable:
    """
    Hook running as a background task once a thread resume has completed.

    on_chat_resume stays the fast inline handshake stage; this hook gets
    the on_chat_start physics: launched as the session's own task (the
    handshake never waits for it), shows the task indicator, is cancelled
    by the stop button and keeps the session alive across page reloads.
    Runs at most once per session. Requires blocking work (long asks,
    pipelines) to live here instead of a bare asyncio.create_task.

    Registered like on_chat_resume — without a step() wrapper on purpose:
    "on_thread_ready" is not in CL_RUN_NAMES (backend and frontend), so a
    run step would render as a visible collapsible step and accumulate a
    run row in persistence per launch.

    Args:
        func (Callable[[ThreadDict], Any]): The hook to execute.

    Returns:
        Callable[[ThreadDict], Any]: The decorated hook.
    """

    config.code.on_thread_ready = wrap_user_function(func)
    return func


def on_profile_start(func: Callable[[ProfileStartInfo], Any]) -> Callable:
    """
    Hook running as a background task on every chat profile switch.

    Requires the `hot_swap_chat_profile` feature flag. Unlike on_chat_start
    it fires on EVERY switch, not once per session, and unlike the legacy
    `set_chat_profile` hand-off the session, the thread and the transcript
    all survive — so blocking work belongs here: AskUserMessage,
    AskActionMessage and AskFileMessage all work inside it.

    Gets the on_chat_start physics: shows the task indicator, is cancelled
    by the stop button, keeps the session alive across page reloads. The
    switch procedure cancels a previous instance before launching a new
    one, so at most one is live per session.

    Registered without a step() wrapper for the same reason as
    on_thread_ready: "on_profile_start" is not in CL_RUN_NAMES.

    Args:
        func (Callable[[ProfileStartInfo], Any]): The hook to execute.

    Returns:
        Callable[[ProfileStartInfo], Any]: The decorated hook.
    """

    config.code.on_profile_start = wrap_user_function(func)
    return func


@overload
def set_chat_profiles(
    func: Callable[[Optional["User"]], Awaitable[List["ChatProfile"]]],
) -> Callable[[Optional["User"]], Awaitable[List["ChatProfile"]]]: ...


@overload
def set_chat_profiles(
    func: Callable[[Optional["User"], Optional["str"]], Awaitable[List["ChatProfile"]]],
) -> Callable[[Optional["User"], Optional["str"]], Awaitable[List["ChatProfile"]]]: ...


def set_chat_profiles(func):
    """
    Programmatic declaration of the available chat profiles (can depend on the User from the session if authentication is setup).

    Args:
        func (Callable[[Optional["User"]], Awaitable[List["ChatProfile"]]]): The function declaring the chat profiles.

    Returns:
        Callable[[Optional["User"]], Awaitable[List["ChatProfile"]]]: The decorated function.
    """

    config.code.set_chat_profiles = wrap_user_function(func)
    return func


@overload
def set_starters(
    func: Callable[[Optional["User"]], Awaitable[List["Starter"]]],
) -> Callable[[Optional["User"]], Awaitable[List["Starter"]]]: ...


@overload
def set_starters(
    func: Callable[[Optional["User"], Optional["str"]], Awaitable[List["Starter"]]],
) -> Callable[[Optional["User"], Optional["str"]], Awaitable[List["Starter"]]]: ...


def set_starters(func):
    """
    Programmatic declaration of the available starter (can depend on the User from the session if authentication is setup).

    Args:
        func (Callable[[Optional["User"], Optional["str"]], Awaitable[List["Starter"]]]): The function declaring the starters with optional user and language arguments.

    Returns:
        Callable[[Optional["User"], Optional["str"]], Awaitable[List["Starter"]]]: The decorated function.
    """

    config.code.set_starters = wrap_user_function(func)
    return func


@overload
def set_starter_categories(
    func: Callable[[Optional["User"]], Awaitable[List["StarterCategory"]]],
) -> Callable[[Optional["User"]], Awaitable[List["StarterCategory"]]]: ...


@overload
def set_starter_categories(
    func: Callable[
        [Optional["User"], Optional["str"]], Awaitable[List["StarterCategory"]]
    ],
) -> Callable[
    [Optional["User"], Optional["str"]], Awaitable[List["StarterCategory"]]
]: ...


@overload
def set_starter_categories(
    func: Callable[
        [Optional["User"], Optional["str"], Optional["str"]],
        Awaitable[List["StarterCategory"]],
    ],
) -> Callable[
    [Optional["User"], Optional["str"], Optional["str"]],
    Awaitable[List["StarterCategory"]],
]: ...


def set_starter_categories(func):
    """
    Programmatic declaration of starter categories with grouped starters.

    Args:
        func (Callable[[Optional["User"], Optional["str"], Optional["str"]], Awaitable[List["StarterCategory"]]]): The function declaring the starter categories with optional user, language, and chat profile arguments.

    Returns:
        Callable[[Optional["User"], Optional["str"], Optional["str"]], Awaitable[List["StarterCategory"]]]: The decorated function.
    """

    config.code.set_starter_categories = wrap_user_function(func)
    return func


def on_chat_end(func: Callable) -> Callable:
    """
    Hook to react to the user websocket disconnect event.

    Args:
        func (Callable[], Any]): The disconnect hook to execute.

    Returns:
        Callable[], Any]: The decorated hook.
    """

    config.code.on_chat_end = wrap_user_function(func)
    return func


def author_rename(
    func: Callable[[str], Awaitable[str]],
) -> Callable[[str], Awaitable[str]]:
    """
    Useful to rename the author of message to display more friendly author names in the UI.
    Args:
        func (Callable[[str], Awaitable[str]]): The function to be called to rename an author. Takes the original author name as parameter.

    Returns:
        Callable[[Any, str], Awaitable[Any]]: The decorated function.
    """

    config.code.author_rename = wrap_user_function(func)
    return func


def on_stop(func: Callable) -> Callable:
    """
    Hook to react to the user stopping a thread.

    Args:
        func (Callable[[], Any]): The stop hook to execute.

    Returns:
        Callable[[], Any]: The decorated stop hook.
    """

    config.code.on_stop = wrap_user_function(func)
    return func


def action_callback(name: str) -> Callable:
    """
    Callback to call when an action is clicked in the UI.

    Args:
        func (Callable[[Action], Any]): The action callback to execute. First parameter is the action.
    """

    def decorator(func: Callable[[Action], Any]):
        config.code.action_callbacks[name] = wrap_user_function(func)
        return func

    return decorator


def data_layer(func: Callable[[], Any]) -> Callable[[], Any]:
    """
    Hook to configure custom data layer.

    Not wrapped: the factory is called synchronously by whoever builds the
    persistence, and a wrapper would make it a coroutine.
    """
    config.code.data_layer = func
    return func


def on_feedback(func: Callable) -> Callable:
    """
    Hook to react to user feedback events from the UI.
    The decorated function is called every time feedback is received.

    Args:
        func (Callable[[Feedback], Any]): The function to be called when feedback is received. Takes a cl.Feedback object.

    Example:
        @cl.on_feedback
        async def on_feedback(feedback: Feedback):
            print(f"Received feedback: {feedback.value} for step {feedback.forId}")
            # Handle feedback here

    Returns:
        Callable[[Feedback], Any]: The decorated on_feedback function.
    """
    config.code.on_feedback = wrap_user_function(func)
    return func


def on_shared_thread_view(
    func: Callable[[ThreadDict, Optional[User]], Awaitable[bool]],
) -> Callable[[ThreadDict, Optional[User]], Awaitable[bool]]:
    """Hook to authorize viewing a shared thread.

    Users must implement and return True to allow a non-author to view a thread.
    Thread metadata contains "is_shared" boolean flag and "shared_at" timestamp for custom thread sharing.
    Signature: async (thread: ThreadDict, viewer: Optional[User]) -> bool
    """
    config.code.on_shared_thread_view = wrap_user_function(func)
    return func
