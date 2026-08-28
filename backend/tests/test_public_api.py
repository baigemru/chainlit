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
EXPECTED_EXPORTS = {
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
}

# What chainlit-panda calls, measured in its source. Every name here must
# stay exported; the snapshot above may shrink around it, never through it.
CONSUMER_SURFACE = {
    "Message",
    "user_session",
    "Action",
    "context",
    "CustomElement",
    "AskActionMessage",
    "AskUserMessage",
    "AskFileMessage",
    "AskElementMessage",
    "Step",
    "Starter",
    "Image",
    "ElementSidebar",
    "chat_context",
    "User",
    "ChatProfile",
    "logger",
    "on_chat_start",
    "on_message",
    "on_chat_resume",
    "on_chat_end",
    "on_stop",
    "on_thread_ready",
    "set_chat_profiles",
    "set_starters",
    "oauth_callback",
    "password_auth_callback",
    "action_callback",
}


def test_exports_match_the_snapshot():
    actual = set(chainlit.__all__)

    missing = EXPECTED_EXPORTS - actual
    added = actual - EXPECTED_EXPORTS

    assert not missing, (
        f"exports removed without updating the snapshot: {sorted(missing)}"
    )
    assert not added, f"exports added without updating the snapshot: {sorted(added)}"


def test_the_consumer_surface_is_exported():
    assert CONSUMER_SURFACE <= set(chainlit.__all__)


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
        "chainlit.data",
        "chainlit.auth",
        "fastapi",
    )
    code = (
        f"import sys; [__import__(m) for m in {modules!r}]; "
        f"bad = sorted(set({forbidden!r}) & set(sys.modules)); "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
