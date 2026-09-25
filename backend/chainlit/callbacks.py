"""The decorators an application registers its hooks with.

Each one stores a wrapped function on ``config.code``; nothing here runs
them. Who runs what -- and on which task -- is ``chainlit.runner``, which
also owns the task indicator. That is why no decorator asks for a task any
more: the hook is launched as the session's task by whoever launches it,
and a second counter inside the wrapper would be the old socket.io
bookkeeping under a new name.
"""

import inspect
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Type,
    TypeVar,
    Union,
    overload,
)

from msgspec import Struct

from chainlit.account import AccountActionResult, register as register_account
from chainlit.action import Action
from chainlit.config import config
from chainlit.message import Message
from chainlit.oauth_providers import get_configured_oauth_providers
from chainlit.step import Step, step
from chainlit.types import (
    ChatProfile,
    Starter,
    StarterCategory,
    ThreadDict,
)
from chainlit.user import User
from chainlit.utils import wrap_user_function

S = TypeVar("S", bound=Struct)

AccountActionHook = Callable[
    ..., Union[AccountActionResult, Awaitable[AccountActionResult]]
]
"""What ``@cl.account_action`` decorates.

``...`` rather than a parameter list because the second argument is the
application's: a button on a card is called ``(user, item, account)``, one in
a tab header ``(user, None, account)``, and ``call_hook`` awaits the result
only if there is one to await. The arity itself is checked at import by
``_assert_arity``, where a mismatch costs a start-up error rather than a 500
on the first click. The return type is the part worth pinning -- it is the whole contract
between the hook and the page."""


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


def account(cls: Type[S]) -> Type[S]:
    """Declare the account page, as one ``msgspec.Struct``.

    The Struct is everything at once: the JSON Schema the client renders the
    form from, the validator a save is converted through, the object the
    values are stored as, and the type the route documents. There is no widget
    list to keep in step with it -- that layer is deleted.

    Checked here rather than at the first request: a field with no default
    means the page cannot be drawn for a user who has never saved, and the
    import is where that is cheapest to see.

    Example:
        @cl.account
        class Account(msgspec.Struct):
            notify: Annotated[bool, Meta(title="Notifications")] = True

    Returns:
        Type[S]: The class, unchanged.
    """
    config.code.account = register_account(cls)
    return cls


def on_account_load(
    func: Callable[[Optional[User], Any, Optional[str]], Awaitable[Any]],
) -> Callable[[Optional[User], Any, Optional[str]], Awaitable[Any]]:
    """Fill in the account page, from the values the engine has just read.

    Called ``(user, account, tab)``: ``account`` is an instance of the
    ``@cl.account`` Struct holding what is stored, read with the request's own
    session, so a hook never opens a second one -- and never sees the values
    from before a ``PUT`` the way a separate session would, since the
    request's session commits after the response is built.

    ``tab`` is the section the page is being drawn for -- the Struct field
    name the dialog's ``?tab=`` carries -- or ``None`` when the address names
    none and the dialog opens on its first section. It is here because
    "the page was opened" and "this section was looked at" are different
    events: a hook that marks a feed seen on every load clears the count for
    a user who came for their balance. The client asks again each time the
    user picks a section, so the hook hears every one of them. The name is
    whatever the address says, a stale one included: check it against your
    own fields rather than trusting it.

    Return the Struct to show -- an *instance*, not a mapping of its values:
    what it returns is also what gets *stored*, the engine compares it with
    what it handed over and writes the difference through the same session, and
    a mapping would store the default of every key it left out. Change what you
    were handed (``msgspec.structs.replace(account, plan="pro")``). Marking a
    feed seen as the page opens is therefore a return value rather than a write
    of the application's own, and a hook that returns its argument unchanged
    writes nothing at all. Fields the schema marks ``readOnly`` are never
    stored -- fill them in freely, they are what the page shows and nothing
    else.

    Stored unwrapped, unlike the session hooks: ``wrap_user_function`` logs an
    exception and returns ``None``, and a load hook that crashed would then
    look like one that answered with an empty page. The route lets the
    exception reach Litestar instead.

    Args:
        func (Callable[[Optional[User], Any, Optional[str]], Awaitable[Any]]):
            The load hook.

    Returns:
        Callable[[Optional[User], Any, Optional[str]], Awaitable[Any]]: The
            decorated hook.
    """
    _assert_arity(func, "@cl.on_account_load", "user", "account", "tab")
    config.code.on_account_load = func
    return func


def _assert_arity(func: Callable[..., Any], decorator: str, *names: str) -> None:
    """Refuse a hook the engine cannot call, at import.

    Every account hook is *handed* what it used to read. The signature is
    tried rather than counted: ``bind`` already knows what ``*args``, a
    default, a bound method and a ``functools.partial`` do to an argument
    list. Checked here because a hook the engine will call with three
    arguments and an application wrote with two is a 500 on the first click,
    and the import is where that is cheapest to see.
    """
    try:
        inspect.signature(func).bind(*(None,) * len(names))
    except TypeError as error:
        raise TypeError(
            f"{decorator} takes ({', '.join(names)}), and {func.__name__} does "
            f"not: {error}. The engine hands the hook the values it has read "
            "for this user, so a hook no longer opens a session of its own and "
            "no longer answers from before a write the request has not "
            "committed -- the shorter forms are retired."
        ) from error


def _assert_takes_user_and_account(func: Callable[..., Any], decorator: str) -> None:
    _assert_arity(func, decorator, "user", "account")


def on_account_update(
    func: Callable[[Optional[User], Any], Awaitable[Optional[str]]],
) -> Callable[[Optional[User], Any], Awaitable[Optional[str]]]:
    """React to a save, after it has been validated and before it is stored.

    The second argument is an instance of the ``@cl.account`` Struct, so a
    hook never has to check a type or a range that the Struct already states.
    It is what will be stored, not what was posted: every ``readOnly`` leaf
    is already back at its default, so a hook does not have to know which of
    its own fields are derived and clean them out by hand.
    A returned string is shown to the user as a toast.

    Stored unwrapped: a save hook that raises must fail the request, not be
    logged away while the engine stores the values and answers "saved".

    Args:
        func (Callable[[Optional[User], Any], Awaitable[Optional[str]]]): The save hook.

    Returns:
        Callable[[Optional[User], Any], Awaitable[Optional[str]]]: The decorated hook.
    """
    config.code.on_account_update = func
    return func


def account_action(
    name: str,
) -> Callable[[AccountActionHook], AccountActionHook]:
    """Answer a button the account page's schema declares in ``x-actions``.

    The hook is called ``(user, item, account)``: ``item`` is the card the
    button sits on, already converted to the Struct the *schema* says it is,
    or ``None`` for a button in a tab header, and ``account`` is the whole
    ``@cl.account`` Struct as the store holds it -- the same value
    ``@cl.on_account_load`` and ``@cl.on_account_badge`` are handed, read
    with the request's own session. Return ``cl.AccountToast``,
    ``cl.AccountRefresh`` or ``cl.AccountOpenThread``; anything else is a
    bug in the application and is reported as a 500.

    ``cl.AccountRefresh(account=...)`` is how an action changes what is
    stored: the engine writes it with the request's own session -- minus the
    ``readOnly`` leaves -- and draws the page from it. Since a refresh
    carries the *whole* document, the third argument is what it is built
    from: an action that changes one card returns the account it was handed
    with that one card changed. Reading the column again through a session of
    its own was the only way to do that before, and it raced the one the
    request is already in.

    Read unlocked, unlike a ``PUT``: the lock a save holds spans the merge,
    and holding one here would span whatever network the action talks to.
    What that costs is stated in ``_store_action_values`` -- the whole
    document the action returns wins, so an action must change what it was
    handed rather than rebuild it.

    Stored unwrapped, like the other account hooks: a button that failed
    must fail the request rather than be logged away while the page
    answers "done".

    Example:
        @cl.account_action("compare")
        async def compare(user, item: WatchedItem | None, account: Account):
            return cl.AccountOpenThread(chat_profile="Fast", transit_message=item)

    Args:
        name (str): The ``name`` the schema's ``x-actions`` entry carries.

    Returns:
        Callable: The decorator that registers the hook and returns it.
    """

    def decorator(func: AccountActionHook) -> AccountActionHook:
        _assert_arity(func, f"@cl.account_action({name!r})", "user", "item", "account")
        config.code.account_actions[name] = func
        return func

    return decorator


def on_account_badge(
    func: Callable[[Optional[User], Any], Awaitable[int]],
) -> Callable[[Optional[User], Any], Awaitable[int]]:
    """Say how many things on the account page the user has not seen.

    Called ``(user, account)`` by the engine, never by the client: the number
    is pushed on the socket after every hello, after the page is read, and
    whenever a session asks for it with
    ``cl.context.emitter.refresh_account_badge()``. An application that does
    not register this hook sends no badge frame at all, and the client shows
    nothing -- it never invents a zero.

    ``account`` is the ``@cl.account`` Struct as the **store** holds it,
    handed over rather than read by the hook. On the account route that
    matters: the mark-as-seen ``on_account_load`` returned is a write of the
    request's own session, which commits after the response is built, so a
    hook that opened a session to count would answer from before the mark and
    the pushed number would be one page load behind. The other two call sites
    read the row through the runner's persistence, so the shape is the same
    whichever asked. It is ``None`` only when no ``@cl.account`` is declared.

    Stored unwrapped: a hook that raises on a request the user made is a
    500, not a silently missing badge.

    Args:
        func (Callable[[Optional[User], Any], Awaitable[int]]): The count hook.

    Returns:
        Callable[[Optional[User], Any], Awaitable[int]]: The decorated hook.
    """
    _assert_takes_user_and_account(func, "@cl.on_account_badge")
    config.code.on_account_badge = func
    return func
