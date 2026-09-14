"""The registry, against the rule it exists to enforce: one session a thread.

Every case here is one row of ``tests/socketspec/cases`` rendered in the
registry's own vocabulary -- ``reload.py`` and ``handshake.py`` for the claim
and the ownership check, ``resume_delete.py`` for the two protection
queries. The ``why`` of each is in those files; the name here says which rule.
"""

from __future__ import annotations

import ast
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from chainlit.ws.registry import (
    ClaimOutcome,
    SessionEntry,
    SessionRegistry,
    ThreadHeld,
    is_owned_by,
)

THREAD = "thread-1"
OTHER_THREAD = "another-thread"
USER = "alice"
OTHER_USER = "bob"


@dataclass
class FakeSession:
    """A five-attribute stand-in for whatever satisfies ``SessionView``."""

    id: str
    has_live_ask: bool = False
    has_live_task: bool = False
    has_parked_reply: bool = False
    live_ask_step_ids: Collection[str] = field(default_factory=tuple)


@pytest.fixture
def registry() -> SessionRegistry:
    return SessionRegistry()


def _tenant(
    registry: SessionRegistry,
    *,
    session_id: str = "s1",
    connected: bool = True,
    live_ask: bool = False,
    ask_step_ids: Collection[str] = (),
    running_task: bool = False,
    parked_reply: bool = False,
    thread: str = THREAD,
    user: str | None = USER,
) -> SessionEntry:
    """The session holding one conversation."""
    return registry.register(
        FakeSession(
            id=session_id,
            has_live_ask=live_ask,
            has_live_task=running_task,
            has_parked_reply=parked_reply,
            live_ask_step_ids=ask_step_ids,
        ),
        thread_id=thread,
        user_identifier=user,
        connected=connected,
    )


# --- Registration, lookup, removal ---------------------------------------


def test_a_conversation_nobody_is_in_is_not_held(registry: SessionRegistry):
    assert registry.entry_of_thread(THREAD) is None
    assert registry.find_thread(THREAD) is None
    assert registry.get("nobody") is None
    assert "nobody" not in registry
    assert len(registry) == 0


def test_a_registered_session_is_found_by_thread_and_by_handle(
    registry: SessionRegistry,
):
    entry = _tenant(registry)

    assert registry.entry_of_thread(THREAD) is entry
    assert registry.find_thread(THREAD) is entry.session
    assert registry.get("s1") is entry
    assert registry.find("s1") is entry.session
    assert "s1" in registry
    assert len(registry) == 1


def test_registering_onto_a_held_thread_raises(registry: SessionRegistry):
    """The invariant of the primary key, and never a client's doing.

    ``claim`` answers *kept* for a conversation somebody is in, so a
    second ``register`` on it means the caller skipped the claim or awaited
    between the two -- and the quiet alternatives are both worse than a
    traceback: overwriting orphans a session somebody is looking at, and
    keeping both brings back the fan-out the key exists to forbid.
    """
    first = _tenant(registry)

    with pytest.raises(ThreadHeld) as excinfo:
        _tenant(registry, session_id="s2")

    assert "s1" in str(excinfo.value)
    assert registry.entry_of_thread(THREAD) is first
    assert "s2" not in registry


def test_a_second_session_may_hold_another_conversation(registry: SessionRegistry):
    first = _tenant(registry)
    second = _tenant(registry, session_id="s2", thread=OTHER_THREAD)

    assert registry.entry_of_thread(THREAD) is first
    assert registry.entry_of_thread(OTHER_THREAD) is second
    assert len(registry) == 2


def test_discarding_frees_the_thread_and_the_handle(registry: SessionRegistry):
    entry = _tenant(registry)

    assert registry.discard(entry) is True

    assert registry.entry_of_thread(THREAD) is None
    assert registry.get("s1") is None
    assert "s1" not in registry
    assert registry.holds(entry) is False


def test_discarding_an_entry_a_successor_replaced_does_nothing(
    registry: SessionRegistry,
):
    """Identity, not equality: the window is real.

    A released session's teardown awaits, and the conversation is free from
    the moment it is discarded -- so a successor can be holding the thread
    by the time some deferred cleanup gets round to removing its
    predecessor. Removing by thread id alone would wipe the live one.
    """
    predecessor = _tenant(registry)
    registry.discard(predecessor)
    successor = _tenant(registry, session_id="s2")

    assert registry.discard(predecessor) is False
    assert registry.entry_of_thread(THREAD) is successor
    assert registry.holds(successor) is True


def test_iterating_gives_one_entry_per_conversation(registry: SessionRegistry):
    first = _tenant(registry)
    second = _tenant(registry, session_id="s2", thread=OTHER_THREAD)

    assert list(registry) == [first, second]
    assert len(registry) == 2


def test_a_caller_may_discard_while_iterating(registry: SessionRegistry):
    _tenant(registry)
    _tenant(registry, session_id="s2", thread=OTHER_THREAD)

    for entry in registry:
        registry.discard(entry)

    assert len(registry) == 0


# --- Moving a session between conversations -------------------------------


def test_set_thread_rekeys_the_registry(registry: SessionRegistry):
    """What the disown does: the session asked for a thread it may not have.

    The primary key moves with it, or the conversation it was refused stays
    pointing at a session that is no longer in it -- and the next arrival
    there is handed somebody else's screen.
    """
    entry = _tenant(registry)

    assert registry.set_thread("s1", OTHER_THREAD) is True

    assert registry.entry_of_thread(THREAD) is None
    assert registry.entry_of_thread(OTHER_THREAD) is entry
    assert entry.thread_id == OTHER_THREAD
    assert registry.get("s1") is entry


def test_set_thread_to_the_thread_it_is_already_in_changes_nothing(
    registry: SessionRegistry,
):
    entry = _tenant(registry)

    assert registry.set_thread("s1", THREAD) is True
    assert registry.entry_of_thread(THREAD) is entry


def test_set_thread_onto_an_occupied_conversation_raises(registry: SessionRegistry):
    moving = _tenant(registry)
    settled = _tenant(registry, session_id="s2", thread=OTHER_THREAD)

    with pytest.raises(ThreadHeld):
        registry.set_thread("s1", OTHER_THREAD)

    assert registry.entry_of_thread(OTHER_THREAD) is settled
    assert registry.entry_of_thread(THREAD) is moving
    assert moving.thread_id == THREAD


def test_set_thread_of_a_session_that_is_not_here_says_so(registry: SessionRegistry):
    assert registry.set_thread("nobody", THREAD) is False


def test_connectedness_is_recorded_through_the_registry(registry: SessionRegistry):
    entry = _tenant(registry, connected=True)

    assert registry.mark_disconnected("s1") is True
    assert entry.connected is False
    assert registry.mark_connected("s1") is True
    assert entry.connected is True
    assert registry.mark_connected("nobody") is False


# --- The claim ------------------------------------------------------------


def test_a_conversation_nobody_is_in_is_created(registry: SessionRegistry):
    claim = registry.claim(THREAD, USER)

    assert claim.outcome is ClaimOutcome.CREATED
    assert claim.entry is None


def test_a_client_naming_no_conversation_is_created(registry: SessionRegistry):
    """A first visit: nothing in the address bar, nothing to look up."""
    claim = registry.claim(None, USER)

    assert claim.outcome is ClaimOutcome.CREATED
    assert claim.entry is None


def test_the_users_own_live_conversation_is_kept(registry: SessionRegistry):
    """A reload, a second tab and a dropped network say the same sentence.

    They all name the thread the user is in, and the answer to all three is
    the session that is in it. Nothing about *why* the socket opened is
    consulted -- which is the change: an idle session used to be thrown
    away on a page load, and with it the hooks' work and the ``user_session``
    dict.
    """
    held = _tenant(registry)

    claim = registry.claim(THREAD, USER)

    assert claim.outcome is ClaimOutcome.KEPT
    assert claim.entry is held


def test_an_idle_conversation_is_kept_too(registry: SessionRegistry):
    """No ask, no task, no parked reply -- and still kept.

    This is the a30 behaviour reversed on purpose. The session *is* the
    conversation now, and a reload that started a new one left the thread
    holding a screen the application had never greeted.
    """
    held = _tenant(registry, live_ask=False, running_task=False, parked_reply=False)

    assert registry.claim(THREAD, USER).entry is held
    assert registry.claim(THREAD, USER).outcome is ClaimOutcome.KEPT


def test_a_conversation_somebody_else_is_in_is_created_on_a_thread_of_ones_own(
    registry: SessionRegistry,
):
    """Not refused: a refusal is an answer about a row that exists.

    The arriving user gets a conversation of their own and hears nothing
    about this one. The claim still names the tenant, because the handshake
    has to know the thread it asked for is not the thread it is getting.
    """
    theirs = _tenant(registry, user=OTHER_USER)

    claim = registry.claim(THREAD, USER)

    assert claim.outcome is ClaimOutcome.CREATED
    assert claim.entry is theirs


def test_claiming_a_foreign_conversation_leaves_it_alone(registry: SessionRegistry):
    """Reading the registry is not touching it.

    The stranger's session stays registered, stays connected and stays in
    its thread: somebody is looking at that screen, and the arriving
    connection is a different person entirely.
    """
    theirs = _tenant(registry, user=OTHER_USER, connected=True, live_ask=True)

    registry.claim(THREAD, USER)

    assert registry.entry_of_thread(THREAD) is theirs
    assert theirs.connected is True
    assert len(registry) == 1


def test_an_anonymous_deployment_hands_the_conversation_to_whoever_asks(
    registry: SessionRegistry,
):
    """With authentication off the thread id in the URL is the whole
    capability -- a uuid4, like a share link. In the release notes."""
    held = _tenant(registry, user=None)

    claim = registry.claim(THREAD, None)

    assert claim.outcome is ClaimOutcome.KEPT
    assert claim.entry is held


# --- Ownership ------------------------------------------------------------


def test_ownership_matches_only_like_for_like(registry: SessionRegistry):
    anonymous = SessionEntry(session=FakeSession(id="a"), thread_id=THREAD)
    named = SessionEntry(
        session=FakeSession(id="b"), thread_id=THREAD, user_identifier=USER
    )

    assert is_owned_by(anonymous, None) is True
    assert is_owned_by(named, USER) is True
    # Neither direction: a named user must not inherit an unowned session,
    # and an unauthenticated request must not inherit a named one.
    assert is_owned_by(anonymous, USER) is False
    assert is_owned_by(named, None) is False
    assert is_owned_by(named, OTHER_USER) is False


# --- What a conversation protects ----------------------------------------


def test_work_running_in_the_conversation_makes_it_live(registry: SessionRegistry):
    _tenant(registry, running_task=True)

    assert registry.has_live_task(THREAD) is True


def test_work_running_behind_a_dropped_socket_still_counts(
    registry: SessionRegistry,
):
    """It will post its results whether or not anybody is watching."""
    _tenant(registry, running_task=True, connected=False)

    assert registry.has_live_task(THREAD) is True


def test_a_conversation_with_nothing_running_is_not_live(registry: SessionRegistry):
    _tenant(registry)

    assert registry.has_live_task(THREAD) is False


def test_work_running_in_another_conversation_does_not_make_this_one_live(
    registry: SessionRegistry,
):
    _tenant(registry, running_task=True, thread=OTHER_THREAD)

    assert registry.has_live_task(THREAD) is False


def test_a_thread_that_is_no_thread_is_never_live(registry: SessionRegistry):
    _tenant(registry, running_task=True)

    assert registry.has_live_task(None) is False


def test_a_question_on_screen_protects_its_steps(registry: SessionRegistry):
    _tenant(registry, live_ask=True, ask_step_ids=("m2", "m3"))

    assert registry.protected_step_ids(THREAD) == frozenset({"m2", "m3"})


def test_a_question_behind_a_dropped_socket_still_protects_its_step(
    registry: SessionRegistry,
):
    _tenant(registry, connected=False, live_ask=True, ask_step_ids=("m2",))

    assert registry.protected_step_ids(THREAD) == frozenset({"m2"})


def test_a_step_of_an_ask_that_is_no_longer_live_is_not_protected(
    registry: SessionRegistry,
):
    _tenant(registry, live_ask=False, ask_step_ids=("m2",))

    assert registry.protected_step_ids(THREAD) == frozenset()


def test_a_question_in_another_conversation_protects_nothing_here(
    registry: SessionRegistry,
):
    _tenant(registry, live_ask=True, ask_step_ids=("m2",), thread=OTHER_THREAD)

    assert registry.protected_step_ids(THREAD) == frozenset()


def test_no_thread_protects_no_steps(registry: SessionRegistry):
    _tenant(registry, live_ask=True, ask_step_ids=("m2",))

    assert registry.protected_step_ids(None) == frozenset()


def test_releasing_the_session_makes_the_conversation_idle_again(
    registry: SessionRegistry,
):
    """Both protection queries answer from the entry, so both go with it.

    The sequence a "New chat" and a reaper both produce: the session is
    discarded, and from that instant the thread reads as nobody's -- which
    is what lets the next arrival resume it from the database instead of
    being handed the empty screen that was sitting on it.
    """
    entry = _tenant(registry, running_task=True, live_ask=True, ask_step_ids=("m2",))

    assert registry.has_live_task(THREAD) is True
    assert registry.protected_step_ids(THREAD) == frozenset({"m2"})

    registry.discard(entry)

    assert registry.has_live_task(THREAD) is False
    assert registry.protected_step_ids(THREAD) == frozenset()
    assert registry.claim(THREAD, USER).outcome is ClaimOutcome.CREATED


# --- Independence ---------------------------------------------------------

REGISTRY = Path(__file__).resolve().parents[2] / "chainlit" / "ws" / "registry.py"
MODULES = [REGISTRY]
"""Only this module. The package it sits in holds transport code that is
allowed the imports the registry is not, and asserting over the whole
directory would make this file a gate on somebody else's."""


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_the_registry_imports_no_other_chainlit_package(path: Path):
    offenders = {
        name
        for name in _imported_modules(path)
        if name.split(".")[0] == "chainlit"
        and name != "chainlit.ws"
        and not name.startswith("chainlit.ws.")
    }
    assert not offenders, f"{path.name} imports {sorted(offenders)}"


@pytest.mark.parametrize(
    "banned",
    [
        "litestar",
        "chainlit.protocol",
        "chainlit.socket",
        "chainlit.server",
        "chainlit.emitter",
    ],
)
def test_the_transport_is_never_imported(banned: str):
    """The registry is the piece that would become shared storage on a
    second replica. Anything it imports comes with it."""
    for path in MODULES:
        imported = _imported_modules(path)
        assert banned not in imported, f"{path.name} imports {banned}"
        assert not any(name.startswith(f"{banned}.") for name in imported), (
            f"{path.name} imports from {banned}"
        )
