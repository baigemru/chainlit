"""The migration is monotone: the frameworks being left cannot come back.

While the port ran, this file listed the modules still on the old stack and
made the list shrink-only. ``config.py`` was the last one; the list is gone
and the guard is now flat: no module anywhere in the package may import a
departing library, directly. Transitive pulls need no second guard once the
direct one covers every file.

``socketio``, ``starlette`` and ``literalai`` joined the banned list when the
last import of each left: the wire is native websockets, the step types are
spelled locally, and nothing in the install pulls the other two any more, so
an import of them would fail at runtime anyway -- this test just says so
earlier and by name.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "chainlit"

DEPARTING = (
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_settings",
    "dataclasses_json",
    "lazify",
    "syncer",
    "asyncer",
    "socketio",
    "literalai",
)


def _roots(source: str) -> set[str]:
    """The top-level packages a module imports."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _departing_imports() -> dict[str, frozenset[str]]:
    found: dict[str, frozenset[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        hits = _roots(path.read_text()) & set(DEPARTING)
        if hits:
            found[str(path.relative_to(PACKAGE))] = frozenset(hits)
    return found


def test_the_walk_sees_the_package():
    """A guard over an empty set passes for the wrong reason."""
    seen = {str(p.relative_to(PACKAGE)) for p in PACKAGE.rglob("*.py")}
    assert {"config.py", "plugin.py", "cli/__init__.py"} <= seen


def test_no_module_imports_a_departing_library():
    """Static, by ``ast``, not by importing.

    Importing the package would answer for the interpreter's environment,
    where a departed library can linger installed; the source is what ships.
    """
    found = {name: sorted(libs) for name, libs in _departing_imports().items()}
    assert not found, (
        f"imports of a departing library: {found}. The rebuild left these "
        f"behind -- write the replacement rather than adding a caller."
    )
