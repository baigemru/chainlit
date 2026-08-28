"""What ``chainlit run`` boots, asserted without binding a port.

``run_chainlit`` hands uvicorn an app; these tests catch that app at the
uvicorn boundary and look at it. The contract with the Cypress harness --
the announce line, the port, SIGTERM -- is ``test_cli_contract.py``, which
runs the real process.

The asyncio guard at the bottom predates the rebuild and stays: importing
``chainlit.cli`` must never swap ``asyncio.Task`` for the pure Python class
(``nest_asyncio.apply()`` does exactly that, and anyio then raises inside
every request). See https://github.com/Chainlit/chainlit/issues/2767.
"""

import asyncio
from pathlib import Path
from typing import Any, List

import pytest
import uvicorn
from litestar import Litestar

import chainlit.cli as cli
from chainlit.plugin import ChainlitPlugin

SAMPLE = Path(cli.BACKEND_ROOT) / "sample" / "hello.py"


class _CapturedServer:
    """Stands in for ``uvicorn.Server``: records the config, serves nothing."""

    captured: List[uvicorn.Config] = []

    def __init__(self, config: uvicorn.Config) -> None:
        self.config = config
        _CapturedServer.captured.append(config)

    async def serve(self) -> None:
        return None


@pytest.fixture
def captured_uvicorn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Run the CLI against the sample app, capturing what reaches uvicorn."""
    _CapturedServer.captured = []
    monkeypatch.setattr(uvicorn, "Server", _CapturedServer)
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))
    monkeypatch.delenv(cli.DATABASE_URL_ENV, raising=False)
    monkeypatch.delenv("CHAINLIT_ROOT_PATH", raising=False)
    monkeypatch.setattr(cli.config.run, "headless", True)
    monkeypatch.setattr(cli.config.run, "watch", False)

    def run(**env: str) -> uvicorn.Config:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        cli.run_chainlit(str(SAMPLE))
        assert len(_CapturedServer.captured) == 1
        return _CapturedServer.captured[0]

    return run


def _plugin(app: Any) -> ChainlitPlugin:
    assert isinstance(app, Litestar)
    plugin = app.plugins.get(ChainlitPlugin)
    assert plugin is not None, "the CLI must boot the app through ChainlitPlugin"
    return plugin


def test_run_hands_uvicorn_a_litestar_app_with_the_plugin(captured_uvicorn):
    server_config = captured_uvicorn()

    plugin = _plugin(server_config.app)
    assert plugin.persistence is None, "no DATABASE_URL means no data layer"
    assert server_config.host == cli.DEFAULT_HOST
    assert server_config.port == cli.DEFAULT_PORT


def test_database_url_turns_persistence_on(captured_uvicorn):
    server_config = captured_uvicorn(DATABASE_URL="sqlite+aiosqlite:///:memory:")

    assert _plugin(server_config.app).persistence is not None


def test_the_environment_reaches_uvicorn(captured_uvicorn):
    server_config = captured_uvicorn(
        CHAINLIT_HOST="127.0.0.5",
        CHAINLIT_PORT="8123",
        CHAINLIT_ROOT_PATH="/chat",
        UVICORN_WS_PROTOCOL="websockets",
        UVICORN_WS_PER_MESSAGE_DEFLATE="false",
    )

    assert server_config.host == "127.0.0.5"
    assert server_config.port == 8123
    # The prefix is uvicorn's ``root_path``, which Litestar strips before
    # routing -- not a ``Litestar(path=...)`` that would move every route.
    assert server_config.root_path == "/chat"
    assert server_config.ws == "websockets"
    assert server_config.ws_per_message_deflate is False
    assert all(not route.path.startswith("/chat") for route in server_config.app.routes)


def test_run_rejects_a_target_that_is_not_a_python_file(captured_uvicorn, tmp_path):
    import click

    (tmp_path / "app.txt").write_text("")
    with pytest.raises(click.BadArgumentUsage):
        cli.run_chainlit(str(tmp_path / "app.txt"))


def test_asyncio_task_not_globally_patched():
    """Importing chainlit.cli must leave the C task implementation in place.

    Compared against the accelerator class itself rather than matching
    Task.__module__: the point is that nothing rebound asyncio.Task, not that
    a C accelerator exists. importorskip keeps an interpreter built without
    _asyncio reporting "skipped" instead of an unrelated failure.
    """
    _asyncio = pytest.importorskip("_asyncio")

    assert asyncio.Task is _asyncio.Task, (
        f"asyncio.Task is {asyncio.Task!r}, expected the C implementation. "
        "Something imported by chainlit.cli has swapped in the pure Python "
        "task class, which desynchronises asyncio.current_task() and breaks "
        "anyio."
    )
