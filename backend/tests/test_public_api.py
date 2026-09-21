"""Golden snapshot of the public API.

The Litestar rebuild deletes and reshapes a lot. Every change to this file must
be deliberate: update the snapshot in the same commit that changes the surface,
so the diff shows what app authors have to migrate.
"""

import inspect

import chainlit

# Frozen 2026-08-28, the surface after the Litestar rebuild of the ``cl.*``
# API. Removed in that rebuild, with the reason:
#   CopilotFunction, InputAudioChunk, OutputAudioChunk, on_audio_* --
#     audio and the copilot call channel have no message on the new wire;
#   ChatSettings, on_settings_edit, on_settings_update -- no wire message;
#   cache -- a bare memo dict, functools.cache exists;
#   current_user -- replaced by request.user on Litestar;
#   header_auth_callback, on_logout, server_route -- FastAPI-typed;
#   make_async, run_sync -- zero callers;
#   on_window_message, send_window_message -- no wire message;
#   switch_chat_profile -- built on the socket.io profile switch.
# Added: ``logger``, which applications already reached as an attribute.
# Changed 2026-09-19, the account page:
#   input_widget -- the chat-settings widget layer had no wire message and no
#     consumer; replaced by @cl.account, where one msgspec.Struct is the
#     schema, the validator and the storage shape at once;
#   account, on_account_load, on_account_update -- added with it.
# Changed 2026-09-19, the account page's second wave:
#   account_action, on_account_badge -- buttons on the page and the unread
#     count it pushes on the socket;
#   AccountToast, AccountRefresh, AccountOpenThread -- what an action hook
#     returns. Prefixed because `Toast` and `Refresh` are ordinary words an
#     application already uses, and `cl.Toast` would read as the chat toast
#     that `cl.context.emitter.send_toast` sends.
# Changed 2026-09-22, the mechanics that were stuck in the application:
#   Mode, ModeOption -- removed; the modes system was dead from the column to
#     the TypeScript type and is deleted outright, not deprecated;
#   deliver_to_thread, refresh_account_badge, persistence, uow -- the way in
#     from code the engine never launched, which the one consumer was
#     reaching by importing five private modules and building a step by hand;
#   element_file_url -- the blob route an application has to link to, which
#     was reachable only as a controller internal;
#   run_in_background -- a run the application declares background, so the
#     composer stays open while it goes;
#   matches_device, is_offered, pick_default_profile,
#     check_one_default_per_device -- the client's own profile rules, stated
#     once in the engine rather than copied into the application.
EXPECTED_EXPORTS = {
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
}


def test_exports_match_the_snapshot():
    actual = set(chainlit.__all__)

    missing = EXPECTED_EXPORTS - actual
    added = actual - EXPECTED_EXPORTS

    assert not missing, (
        f"exports removed without updating the snapshot: {sorted(missing)}"
    )
    assert not added, f"exports added without updating the snapshot: {sorted(added)}"


def test_the_old_sidebar_helper_is_gone_rather_than_shimmed():
    """``ElementSidebar`` is deleted, not kept as a facade over ``Sidebar``.

    The fork has one consumer and it migrates on the bump; a facade would
    have had to carry the old semantics (``key`` meaning "do not update",
    ``set_title`` opening an empty panel) into a model that has no room for
    them. The migration is four lines of release notes instead.
    """
    assert not hasattr(chainlit, "ElementSidebar")
    assert "ElementSidebar" not in chainlit.__all__


def test_every_export_resolves():
    unresolved = []
    for name in chainlit.__all__:
        try:
            getattr(chainlit, name)
        except Exception as e:
            unresolved.append(f"{name}: {type(e).__name__}")

    assert not unresolved, f"names in __all__ that do not resolve: {unresolved}"


def test_callable_signatures_are_snapshotted():
    """Guards against a silent signature change on the hooks apps implement."""
    expected = {
        "password_auth_callback": "(func: Callable[[str, str], Awaitable[chainlit.user.User | None]]) -> Callable",
        "on_message": "(func: Callable) -> Callable",
        "on_chat_start": "(func: Callable) -> Callable",
    }

    actual = {
        name: str(inspect.signature(getattr(chainlit, name))) for name in expected
    }

    assert actual == expected


def test_no_old_stack_imports_in_the_api_modules():
    """The ``cl.*`` modules must not reach the deleted transport.

    In a subprocess: the test session has other suites' imports in
    ``sys.modules``, and what those pulled in is not what these modules do.
    """
    import subprocess
    import sys

    modules = (
        "chainlit.message",
        "chainlit.step",
        "chainlit.element",
        "chainlit.action",
        "chainlit.sidebar",
        "chainlit.user_session",
        "chainlit.chat_context",
        "chainlit.callbacks",
        "chainlit.types",
        "chainlit.user",
    )
    forbidden = (
        "chainlit.server",
        "chainlit.socket",
        "chainlit.session",
        "chainlit.persist_barrier",
        "chainlit.resume_policy",
        "chainlit.auth",
        "fastapi",
    )
    code = (
        f"import sys; [__import__(m) for m in {modules!r}]; "
        f"bad = sorted(set({forbidden!r}) & set(sys.modules)); "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
