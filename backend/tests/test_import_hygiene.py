"""The migration is monotone: the frameworks being left cannot come back.

Two guards, because there are two questions. Nothing the rebuilt packages
depend on may be on the old stack -- not directly, and not one import deep.
And the modules still on the old stack must only ever shrink:
the list below is the whole of what is left to port, so adding an import of a
departing library anywhere fails here, and removing the last one from a module
requires deleting its line.

``starlette`` is deliberately absent from the banned list. It cannot leave the
install while the old FastAPI stack is still running -- so banning it would be
a rule the project has already decided not to follow.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "chainlit"

DEPARTING = ("fastapi", "pydantic", "dataclasses_json", "lazify", "syncer", "asyncer")

# ``chainlit.plugin`` is deliberately absent: the startup bootstrap it took
# over from the CLI reaches ``chainlit.markdown``, ``chainlit.cache`` and the
# pydantic ``ChainlitConfig``, and will keep reaching them until those are
# ported. Its two new dependencies are listed instead, because they can be
# kept clean and are what the socket and the auth middleware will import.
REBUILT = (
    "chainlit.persistence",
    "chainlit.protocol",
    "chainlit.security",
    "chainlit.transit_store",
)

# Every module still importing one of the departing libraries, and which.
# This is the port's remaining surface, written down. Shrink it as modules
# move over; it must never grow.
REMAINING: dict[str, frozenset[str]] = {
    "__init__.py": frozenset({"pydantic"}),
    "action.py": frozenset({"dataclasses_json", "pydantic"}),
    "auth/__init__.py": frozenset({"fastapi"}),
    "auth/cookie.py": frozenset({"fastapi"}),
    "callbacks.py": frozenset({"fastapi"}),
    "chat_settings.py": frozenset({"pydantic"}),
    "config.py": frozenset({"fastapi", "pydantic"}),
    "context.py": frozenset({"lazify"}),
    "data/acl.py": frozenset({"fastapi"}),
    "element.py": frozenset({"pydantic"}),
    "input_widget.py": frozenset({"pydantic"}),
    "mode.py": frozenset({"dataclasses_json"}),
    "server.py": frozenset({"fastapi"}),
    "sync.py": frozenset({"asyncer", "syncer"}),
    "types.py": frozenset({"dataclasses_json", "pydantic"}),
    "user.py": frozenset({"dataclasses_json", "pydantic"}),
    "utils.py": frozenset({"fastapi"}),
}


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


def test_the_port_only_ever_shrinks():
    """Equality, not containment.

    Containment would let the list rot: a module that was ported would stay
    listed, and the guard would go on permitting an import nobody needs any
    more. Deleting the line is part of porting the module.
    """
    found = _departing_imports()
    added = {
        name: sorted(libs) for name, libs in found.items() if name not in REMAINING
    }
    ported = sorted(set(REMAINING) - set(found))
    changed = {
        name: sorted(libs)
        for name, libs in found.items()
        if name in REMAINING and libs != REMAINING[name]
    }
    assert not added, (
        f"new imports of a departing library: {added}. The rebuild is leaving "
        f"these behind -- write the replacement rather than adding a caller."
    )
    assert not ported, (
        f"these no longer import a departing library: {ported}. Delete them "
        f"from REMAINING so the guard keeps its teeth."
    )
    assert not changed, f"REMAINING is out of date for: {changed}"


def _closure(entry_points: tuple[str, ...]) -> set[str]:
    """Every module inside chainlit reachable from these packages.

    Static, not by importing: ``chainlit/__init__.py`` still pulls the whole
    old stack in, so a runtime probe would answer a question about the parent
    package rather than about these two. Following the import graph on disk
    asks what the rebuilt code itself depends on, which is the thing that has
    to stay clean while the port finishes.
    """
    seen: set[str] = set()
    queue = [name.removeprefix("chainlit.") for name in entry_points]
    while queue:
        target = queue.pop()
        base = PACKAGE / Path(*target.split("."))
        path = base / "__init__.py" if base.is_dir() else base.with_suffix(".py")
        if not path.exists():
            continue
        relative = str(path.relative_to(PACKAGE))
        if relative in seen:
            continue
        seen.add(relative)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("chainlit."):
                        queue.append(alias.name.removeprefix("chainlit."))
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                if not module.startswith("chainlit"):
                    continue
                inner = module.removeprefix("chainlit").lstrip(".")
                # Both readings of ``from chainlit.x import y``: y may be a
                # name inside x, or a submodule of it. Whichever it is, the
                # other resolves to no file and is dropped above.
                queue.append(inner)
                queue.extend(f"{inner}.{alias.name}" for alias in node.names)
    return seen


def test_nothing_the_rebuilt_packages_depend_on_is_departing():
    """A transitive pull is the same dependency.

    The point of the rebuilt packages is that they can be loaded, tested and
    shipped without the stack being left behind. One import of a legacy module
    for one helper would quietly undo that.
    """
    reachable = _closure(REBUILT)
    assert reachable, "the closure found nothing -- the walk is broken"
    tainted = sorted(reachable & set(REMAINING))
    assert not tainted, (
        f"the rebuilt packages reach {tainted}, which still import a "
        f"departing library. Copy what is needed rather than importing it."
    )
