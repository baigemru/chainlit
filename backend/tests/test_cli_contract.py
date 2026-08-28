"""The contract the Cypress harness depends on, asserted from Python.

cypress/support/run.ts waits for a literal string on stdout, expects port 8000,
passes `-h --ci`, and tears the server down with SIGTERM to the process group.
None of that is visible from the backend sources, so a reworded log line or a
changed teardown turns all 60+ e2e specs into five-minute timeouts with no
useful message. These tests fail in seconds instead.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ANNOUNCE = "Your app is available at"
PORT = 8000
BOOT_TIMEOUT = 90.0
TEARDOWN_TIMEOUT = 10.0
BIND_TIMEOUT = 30.0

BACKEND = Path(__file__).resolve().parent.parent
SAMPLE = BACKEND / "chainlit" / "sample" / "hello.py"


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture
def chainlit_process(tmp_path):
    """Run `chainlit run <target> -h --ci` exactly the way the harness does."""
    if _port_is_open(PORT):
        pytest.skip(f"port {PORT} already in use")

    env = {**os.environ, "CHAINLIT_APP_ROOT": str(tmp_path)}
    process = subprocess.Popen(
        [sys.executable, "-m", "chainlit", "run", str(SAMPLE), "-h", "--ci"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,  # own process group, as the harness expects
        env=env,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            process.wait(timeout=5)


def _await_announce(process) -> str:
    """Read stdout until the announce line appears. Returns the captured output."""
    captured: list[str] = []
    deadline = time.monotonic() + BOOT_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            captured.extend(process.stdout.readlines())
            raise AssertionError(
                f"process exited with {process.returncode} before announcing:\n"
                + "".join(captured)
            )
        line = process.stdout.readline()
        if not line:
            continue
        captured.append(line)
        if ANNOUNCE in line:
            return "".join(captured)
    raise AssertionError(
        f"no {ANNOUNCE!r} within {BOOT_TIMEOUT}s:\n" + "".join(captured)
    )


def test_announces_on_stdout_and_binds_the_default_port(chainlit_process):
    output = _await_announce(chainlit_process)

    assert ANNOUNCE in output

    # The announce is emitted from the lifespan startup hook, which uvicorn runs
    # BEFORE it binds the listening socket -- so the line means "starting", not
    # "ready", and anything treating it as ready is racing. Poll for the bind.
    deadline = time.monotonic() + BIND_TIMEOUT
    while time.monotonic() < deadline:
        if _port_is_open(PORT):
            return
        time.sleep(0.1)
    raise AssertionError(
        f"nothing listening on {PORT} {BIND_TIMEOUT}s after the announce"
    )


def test_sigterm_to_the_process_group_frees_the_port(chainlit_process):
    _await_announce(chainlit_process)

    os.killpg(os.getpgid(chainlit_process.pid), signal.SIGTERM)

    deadline = time.monotonic() + TEARDOWN_TIMEOUT
    while time.monotonic() < deadline:
        if chainlit_process.poll() is not None:
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f"still alive {TEARDOWN_TIMEOUT}s after SIGTERM")

    deadline = time.monotonic() + TEARDOWN_TIMEOUT
    while time.monotonic() < deadline:
        if not _port_is_open(PORT):
            return
        time.sleep(0.1)
    raise AssertionError(f"port {PORT} still bound after the process exited")


def test_uvicorn_has_a_websocket_implementation() -> None:
    """The socket is the product; uvicorn serves it only with an extra.

    ``uvicorn`` alone ships no websocket protocol -- ``ws="auto"`` silently
    resolves to nothing and every upgrade fails. The implementation used
    to arrive transitively through socket.io's ``simple-websocket``; with
    that gone it has to be a dependency of our own (``uvicorn[standard]``).
    """
    from uvicorn.protocols.websockets.auto import AutoWebSocketsProtocol

    assert AutoWebSocketsProtocol is not None
