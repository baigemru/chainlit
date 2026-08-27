"""The table must not know which transport it is describing.

This is the deliverable, not a nicety: the moment a scenario, a frame or the
runner reaches into ``chainlit.socket``, the table stops being portable and
phase 5 has to rewrite it along with everything else. ``legacy`` is the one
module allowed to know, because it is the one module that dies with the
transport.
"""

import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
PORTABLE = (
    "frames",
    "spec",
    "cases",
    "cases.ask",
    "cases.transcript",
    "cases.orphans",
    "cases.resync",
)

PROBE = """
import sys
import {module}  # noqa: F401

leaked = sorted(
    name
    for name in sys.modules
    if name == "chainlit" or name.startswith("chainlit.")
)
print(",".join(leaked))
"""


def _imports_of(module: str) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(module=module)],
        capture_output=True,
        text=True,
        cwd=PACKAGE.parents[1],
    )
    assert result.returncode == 0, result.stderr
    return {name for name in result.stdout.strip().split(",") if name}


def test_the_table_never_imports_chainlit():
    """Not just ``chainlit.socket``: nothing from the application at all.

    A scenario that needs an application type to say what it means is a
    scenario written against an implementation.
    """
    for module in PORTABLE:
        leaked = _imports_of(f"tests.socketspec.{module}")
        assert not leaked, (
            f"tests/socketspec/{module.replace('.', '/')}.py pulls in {leaked}. "
            f"Only tests/socketspec/legacy.py may import chainlit -- everything "
            f"else has to outlive the transport."
        )


def test_the_driver_is_the_one_that_knows():
    """The boundary is only meaningful if something is actually on the far side."""
    assert "chainlit.socket" in _imports_of("tests.socketspec.legacy")
