"""The protocol package must not depend on the rest of ``chainlit``.

Pure data plus a codec is what makes it reviewable on its own and reusable
by a client generator. The moment it imports ``chainlit.session``, testing
a message means booting a session.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROTOCOL_DIR = Path(__file__).resolve().parents[2] / "chainlit" / "protocol"

MODULES = sorted(PROTOCOL_DIR.glob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_the_package_is_not_empty() -> None:
    assert {p.name for p in MODULES} == {
        "__init__.py",
        "client.py",
        "codec.py",
        "payloads.py",
        "server.py",
    }


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_module_imports_no_other_chainlit_package(path: Path) -> None:
    offenders = {
        name
        for name in _imported_modules(path)
        if name.split(".")[0] == "chainlit"
        and not name.startswith("chainlit.protocol")
        and name != "chainlit.protocol"
    }
    assert not offenders, f"{path.name} imports {sorted(offenders)}"


@pytest.mark.parametrize("banned", ["socket", "emitter", "session"])
def test_the_hot_path_modules_are_never_imported(banned: str) -> None:
    """Named explicitly: these three are the ones that would tie the knot."""
    for path in MODULES:
        assert f"chainlit.{banned}" not in _imported_modules(path)
