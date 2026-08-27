"""Golden snapshot of the public API.

The Litestar rebuild deletes and reshapes a lot. Every change to this file must
be deliberate: update the snapshot in the same commit that changes the surface,
so the diff shows what app authors have to migrate.
"""

import inspect

import chainlit

# Frozen 2026-08-27, before the Litestar rebuild. Names scheduled for removal
# carry a comment; remove them from this list in the commit that removes them.
EXPECTED_EXPORTS = {
    "AsyncLangchainCallbackHandler",
    "Action",
    "AskActionMessage",
    "AskElementMessage",
    "AskFileMessage",
    "AskSlotBusyError",
    "AskUserMessage",
    "Audio",
    "ChatGeneration",
    "ChatProfile",
    "ChatSettings",
    "CompletionGeneration",
    "CopilotFunction",  # rebuild: delete (unused, undocumented)
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
    "cache",  # rebuild: delete (a bare memo dict, functools.cache exists)
    "chat_context",
    "context",
    "current_user",  # rebuild: delete (replaced by request.user)
    "data_layer",  # rebuild: renamed to `persistence`
    "header_auth_callback",
    "input_widget",
    "instrument_mistralai",
    "instrument_openai",
    "make_async",  # rebuild: delete (one line of anyio.to_thread.run_sync)
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
    "on_logout",  # rebuild: delete (FastAPI-typed signature)
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
    "run_sync",  # rebuild: delete (zero callers; _reentrant_loop.py exists for it)
    "send_window_message",
    "server_route",  # rebuild: replaced by register_routes + litestar decorators
    "set_chat_profiles",
    "set_starter_categories",
    "set_starters",
    "sleep",
    "step",
    "switch_chat_profile",
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
        # Starlette-typed on purpose: this is one of the documented breaks of the
        # Litestar rebuild. When it changes, the migration guide changes with it.
        "header_auth_callback": "(func: Callable[[starlette.datastructures.Headers], Awaitable[chainlit.user.User | None]]) -> Callable",
        "on_message": "(func: Callable) -> Callable",
        "on_chat_start": "(func: Callable) -> Callable",
    }

    actual = {
        name: str(inspect.signature(getattr(chainlit, name))) for name in expected
    }

    assert actual == expected
