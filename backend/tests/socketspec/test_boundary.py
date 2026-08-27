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

# Not a hand-written list: a case module added later would silently sit
# outside a guard that exists precisely to catch what gets added later.
NOT_PORTABLE = {"legacy", "__init__"}


def _portable() -> tuple[str, ...]:
    modules = [
        path.stem
        for path in sorted(PACKAGE.glob("*.py"))
        if path.stem not in NOT_PORTABLE and not path.stem.startswith("test_")
    ]
    modules += ["cases"] + [
        f"cases.{path.stem}"
        for path in sorted((PACKAGE / "cases").glob("*.py"))
        if path.stem != "__init__"
    ]
    return tuple(modules)


PORTABLE = _portable()

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


def _source_of(module: str) -> str:
    """The text of a module in this package, whether file or package."""
    base = PACKAGE / Path(*module.split("."))
    path = base.with_suffix(".py")
    return (path if path.exists() else base / "__init__.py").read_text()


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


def test_no_transport_event_name_leaks_into_the_table():
    """An import is not the only way to depend on a transport.

    A socket.io event name is a string literal, so the import probe above
    cannot see it -- and the translation tables sat inside a module that
    probe certified as portable until an audit noticed. Names that happen to
    be protocol vocabulary too are excluded -- ``toast`` and ``reload`` as
    message tags, ``action`` and ``element`` as ask-spec kinds. Those are not
    a dependency; they are the same word in both vocabularies.
    """
    from typing import get_args

    import msgspec

    from chainlit.protocol import payloads
    from chainlit.protocol.client import ClientMsg
    from chainlit.protocol.server import ServerMsg

    from . import legacy

    tags = {branch.__struct_config__.tag for branch in get_args(ServerMsg)} | {
        branch.__struct_config__.tag for branch in get_args(ClientMsg)
    }
    # Nested tags count as vocabulary too: an ask spec's kind is protocol,
    # even where it is spelled like an old event name.
    for name in dir(payloads):
        candidate = getattr(payloads, name)
        if isinstance(candidate, type) and issubclass(candidate, msgspec.Struct):
            tag = candidate.__struct_config__.tag
            if isinstance(tag, str):
                tags.add(tag)
    legacy_only = (
        set(legacy._RENAMES)
        | set(legacy._WRAPPED)
        | set(legacy._COLLAPSED)
        | set(legacy._INBOUND_EVENTS)
    ) - tags
    assert legacy_only, "the guard is vacuous if every legacy name is also a tag"

    for module in PORTABLE:
        source = _source_of(module)
        leaked = sorted(
            name
            for name in legacy_only
            if f'"{name}"' in source or f"'{name}'" in source
        )
        assert not leaked, (
            f"tests/socketspec/{module.replace('.', '/')}.py names the socket.io "
            f"events {leaked}. Event names belong with the driver that speaks "
            f"them; the table speaks protocol tags."
        )
