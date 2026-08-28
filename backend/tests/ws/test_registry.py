"""The registry, against the scenarios that define it.

Every case here is one row of ``tests/socketspec/cases`` rendered in the
registry's own vocabulary -- ``bystanders.py`` for the sweep and the two
protection queries, ``reload.py`` for the four-way claim and the ownership
check. The ``why`` of each is in those files; the name here says which row.
"""

from __future__ import annotations

import ast
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from chainlit.ws.registry import (
    Claim,
    ClaimOutcome,
    SessionEntry,
    SessionRegistry,
    has_live_work,
    is_abandoned_ask_session,
    is_disconnected,
    is_owned_by,
    is_parked_on_live_ask,
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


def _bystander(
    registry: SessionRegistry,
    *,
    session_id: str = "bystander-0",
    connected: bool = True,
    live_ask: bool = False,
    ask_step_ids: Collection[str] = (),
    running_task: bool = False,
    thread: str | None = THREAD,
    user: str | None = USER,
) -> SessionEntry:
    """Another session of this conversation, in ``Bystander``'s vocabulary."""
    return registry.register(
        FakeSession(
            id=session_id,
            has_live_ask=live_ask,
            has_live_task=running_task,
            live_ask_step_ids=ask_step_ids,
        ),
        user_identifier=user,
        thread_id=thread,
        connected=connected,
    )


def _arriving(registry: SessionRegistry, session_id: str = "arriving") -> SessionEntry:
    return registry.register(
        FakeSession(id=session_id), user_identifier=USER, thread_id=THREAD
    )


def _evicted(registry: SessionRegistry, arriving: str = "arriving") -> list[str]:
    return [
        entry.id
        for entry in registry.abandoned_ask_sessions(
            THREAD, arriving_session_id=arriving
        )
    ]


# --- Registration, lookup, removal ---------------------------------------


def test_an_id_the_registry_has_never_seen_is_not_held(registry: SessionRegistry):
    assert registry.get("nobody") is None
    assert "nobody" not in registry
    assert len(registry) == 0
    assert registry.entries_of_thread(THREAD) == ()


def test_a_registered_session_is_found_by_its_id(registry: SessionRegistry):
    entry = _bystander(registry, session_id="s1")

    assert registry.get("s1") is entry
    assert "s1" in registry
    assert entry.session.id == "s1"
    assert entry.id == "s1"


def test_a_registered_session_appears_in_its_thread(registry: SessionRegistry):
    entry = _bystander(registry, session_id="s1")

    assert registry.entries_of_thread(THREAD) == (entry,)


def test_a_session_of_no_thread_is_in_no_thread(registry: SessionRegistry):
    _bystander(registry, session_id="s1", thread=None)

    assert registry.entries_of_thread(None) == ()
    assert registry.entries_of_thread(THREAD) == ()


def test_removing_a_session_takes_it_out_of_the_thread_index(
    registry: SessionRegistry,
):
    entry = _bystander(registry, session_id="s1")

    assert registry.remove("s1") is entry
    assert registry.get("s1") is None
    assert registry.entries_of_thread(THREAD) == ()
    assert len(registry) == 0


def test_removing_an_unknown_id_is_not_an_error(registry: SessionRegistry):
    assert registry.remove("nobody") is None


def test_a_successor_under_the_same_id_leaves_no_predecessor_behind(
    registry: SessionRegistry,
):
    """The replaced page load: a new session is created under the old id."""
    _bystander(registry, session_id="s1")
    successor = _bystander(registry, session_id="s1")

    assert registry.entries_of_thread(THREAD) == (successor,)
    assert registry.get("s1") is successor
    assert len(registry) == 1


def test_a_successor_in_another_conversation_leaves_no_stale_index_entry(
    registry: SessionRegistry,
):
    """The predecessor's bucket is a different one, so nothing overwrites
    its row: the id would answer the sweep of a conversation it left."""
    _bystander(registry, session_id="s1", thread=THREAD)
    successor = _bystander(registry, session_id="s1", thread=OTHER_THREAD)

    assert registry.entries_of_thread(THREAD) == ()
    assert registry.entries_of_thread(OTHER_THREAD) == (successor,)


def test_discarding_an_entry_a_successor_replaced_does_nothing(
    registry: SessionRegistry,
):
    """A deferred cleanup must not wipe the registry entry of its successor."""
    predecessor = _bystander(registry, session_id="s1")
    successor = _bystander(registry, session_id="s1")

    assert registry.discard(predecessor) is False
    assert registry.get("s1") is successor
    assert registry.entries_of_thread(THREAD) == (successor,)


def test_discarding_the_current_tenant_removes_it(registry: SessionRegistry):
    entry = _bystander(registry, session_id="s1")

    assert registry.discard(entry) is True
    assert registry.get("s1") is None
    assert registry.entries_of_thread(THREAD) == ()


def test_holds_is_identity_not_equality(registry: SessionRegistry):
    predecessor = _bystander(registry, session_id="s1")
    successor = _bystander(registry, session_id="s1")

    assert registry.holds(predecessor) is False
    assert registry.holds(successor) is True


def test_moving_a_session_to_another_thread_updates_both_buckets(
    registry: SessionRegistry,
):
    entry = _bystander(registry, session_id="s1")

    assert registry.set_thread("s1", OTHER_THREAD) is True

    assert registry.entries_of_thread(THREAD) == ()
    assert registry.entries_of_thread(OTHER_THREAD) == (entry,)
    assert entry.thread_id == OTHER_THREAD


def test_a_session_moved_out_of_every_thread_is_indexed_nowhere(
    registry: SessionRegistry,
):
    _bystander(registry, session_id="s1")

    registry.set_thread("s1", None)

    assert registry.entries_of_thread(THREAD) == ()
    assert registry.get("s1") is not None


def test_a_session_given_a_thread_after_registration_is_indexed(
    registry: SessionRegistry,
):
    entry = _bystander(registry, session_id="s1", thread=None)

    registry.set_thread("s1", THREAD)

    assert registry.entries_of_thread(THREAD) == (entry,)


def test_setting_the_thread_of_an_unknown_session_reports_failure(
    registry: SessionRegistry,
):
    assert registry.set_thread("nobody", THREAD) is False
    assert registry.entries_of_thread(THREAD) == ()


def test_the_thread_index_keeps_registration_order(registry: SessionRegistry):
    first = _bystander(registry, session_id="s1")
    second = _bystander(registry, session_id="s2")

    assert registry.entries_of_thread(THREAD) == (first, second)


def test_iterating_yields_a_snapshot_so_sessions_may_be_removed(
    registry: SessionRegistry,
):
    _bystander(registry, session_id="s1")
    _bystander(registry, session_id="s2")

    seen = []
    for entry in registry:
        seen.append(entry.id)
        registry.remove(entry.id)

    assert sorted(seen) == ["s1", "s2"]
    assert len(registry) == 0


# --- The liveness flag ----------------------------------------------------


def test_a_session_starts_connected(registry: SessionRegistry):
    entry = _bystander(registry, session_id="s1")

    assert entry.connected is True
    assert is_disconnected(entry) is False


def test_a_session_whose_socket_went_away_is_disconnected(registry: SessionRegistry):
    entry = _bystander(registry, session_id="s1")

    assert registry.mark_disconnected("s1") is True

    assert entry.connected is False
    assert is_disconnected(entry) is True


def test_handing_the_socket_over_marks_a_session_connected_again(
    registry: SessionRegistry,
):
    entry = _bystander(registry, session_id="s1", connected=False)

    assert registry.mark_connected("s1") is True

    assert entry.connected is True
    assert is_disconnected(entry) is False


def test_marking_an_unknown_session_reports_failure(registry: SessionRegistry):
    assert registry.mark_disconnected("nobody") is False
    assert registry.mark_connected("nobody") is False


# --- Filtering by user ----------------------------------------------------


def test_sessions_are_filterable_by_user(registry: SessionRegistry):
    mine = _bystander(registry, session_id="s1", user=USER)
    _bystander(registry, session_id="s2", user=OTHER_USER)

    assert registry.entries_of_user(USER) == (mine,)


def test_the_anonymous_user_sees_only_anonymous_sessions(registry: SessionRegistry):
    anonymous = _bystander(registry, session_id="s1", user=None)
    _bystander(registry, session_id="s2", user=USER)

    assert registry.entries_of_user(None) == (anonymous,)


# --- Ownership ------------------------------------------------------------


def test_a_user_owns_their_own_session(registry: SessionRegistry):
    entry = _bystander(registry, session_id="s1", user=USER)

    assert is_owned_by(entry, USER) is True


def test_another_user_does_not_own_it(registry: SessionRegistry):
    entry = _bystander(registry, session_id="s1", user=USER)

    assert is_owned_by(entry, OTHER_USER) is False


def test_an_unowned_session_belongs_to_the_anonymous_user(registry: SessionRegistry):
    entry = _bystander(registry, session_id="s1", user=None)

    assert is_owned_by(entry, None) is True


def test_a_named_user_does_not_inherit_an_unowned_session(registry: SessionRegistry):
    entry = _bystander(registry, session_id="s1", user=None)

    assert is_owned_by(entry, USER) is False


def test_an_owned_session_is_not_handed_to_the_anonymous_user(
    registry: SessionRegistry,
):
    entry = _bystander(registry, session_id="s1", user=USER)

    assert is_owned_by(entry, None) is False


# --- The four-way claim (reload.py) ---------------------------------------


def test_an_id_the_server_has_never_seen_opens_a_new_conversation(
    registry: SessionRegistry,
):
    claim = registry.claim("s1", USER, page_load=True)

    assert claim == Claim(ClaimOutcome.CREATED, None)
    assert claim.outcome == "created"


def test_reloading_an_idle_conversation_starts_a_new_one(registry: SessionRegistry):
    entry = _bystander(registry, session_id="s1")

    claim = registry.claim("s1", USER, page_load=True)

    assert claim.outcome is ClaimOutcome.REPLACED
    assert claim.entry is entry


def test_reloading_while_a_question_is_waiting_keeps_the_conversation(
    registry: SessionRegistry,
):
    _bystander(registry, session_id="s1", live_ask=True)

    assert registry.claim("s1", USER, page_load=True).outcome is ClaimOutcome.KEPT


def test_reloading_while_work_is_running_keeps_the_conversation(
    registry: SessionRegistry,
):
    _bystander(registry, session_id="s1", running_task=True)

    assert registry.claim("s1", USER, page_load=True).outcome is ClaimOutcome.KEPT


def test_reloading_while_a_rescued_answer_is_in_flight_keeps_the_conversation(
    registry: SessionRegistry,
):
    session = FakeSession(id="s1", has_parked_reply=True)
    registry.register(session, user_identifier=USER, thread_id=THREAD)

    assert registry.claim("s1", USER, page_load=True).outcome is ClaimOutcome.KEPT


def test_a_transport_reconnect_never_starts_a_new_conversation(
    registry: SessionRegistry,
):
    _bystander(registry, session_id="s1")

    assert registry.claim("s1", USER, page_load=False).outcome is ClaimOutcome.KEPT


@pytest.mark.parametrize("page_load", [True, False], ids=["reload", "reconnect"])
def test_a_conversation_belonging_to_someone_else_is_refused(
    registry: SessionRegistry, page_load: bool
):
    entry = _bystander(registry, session_id="s1", user=USER)

    claim = registry.claim("s1", OTHER_USER, page_load=page_load)

    assert claim.outcome is ClaimOutcome.REFUSED
    assert claim.entry is entry


def test_refusal_beats_replacement_on_an_idle_conversation(registry: SessionRegistry):
    """Ownership is checked first, or a guessed id starts over in someone
    else's conversation instead of being turned away."""
    _bystander(registry, session_id="s1", user=USER)

    claim = registry.claim("s1", OTHER_USER, page_load=True)

    assert claim.outcome is ClaimOutcome.REFUSED


def test_live_work_is_any_of_the_three_things_a_user_would_lose(
    registry: SessionRegistry,
):
    idle = _bystander(registry, session_id="idle")
    asking = _bystander(registry, session_id="asking", live_ask=True)
    working = _bystander(registry, session_id="working", running_task=True)
    parked = registry.register(
        FakeSession(id="parked", has_parked_reply=True), user_identifier=USER
    )

    assert has_live_work(idle) is False
    assert has_live_work(asking) is True
    assert has_live_work(working) is True
    assert has_live_work(parked) is True


# --- The sweep (bystanders.py) -------------------------------------------


def test_a_session_parked_on_a_question_nobody_came_back_to_is_evicted(
    registry: SessionRegistry,
):
    _arriving(registry)
    _bystander(registry, connected=False, live_ask=True)

    assert _evicted(registry) == ["bystander-0"]


def test_a_second_tab_showing_the_same_question_is_left_alone(
    registry: SessionRegistry,
):
    _arriving(registry)
    _bystander(registry, connected=True, live_ask=True)

    assert _evicted(registry) == []


def test_a_disconnected_session_working_between_questions_is_left_alone(
    registry: SessionRegistry,
):
    _arriving(registry)
    _bystander(registry, connected=False, running_task=True)

    assert _evicted(registry) == []


def test_a_session_of_another_conversation_is_never_touched(
    registry: SessionRegistry,
):
    _arriving(registry)
    _bystander(registry, connected=False, live_ask=True, thread=OTHER_THREAD)

    assert _evicted(registry) == []
    assert registry.get("bystander-0") is not None


def test_a_running_task_does_not_shield_an_abandoned_question(
    registry: SessionRegistry,
):
    """The regression the sweep exists for: a hook parked on a long offer
    keeps its task live for the whole deadline, so shielding on a running
    task leaves one more session behind on every reload."""
    _arriving(registry)
    _bystander(registry, connected=False, live_ask=True, running_task=True)

    assert _evicted(registry) == ["bystander-0"]


def test_the_arriving_session_never_sweeps_itself_up(registry: SessionRegistry):
    registry.register(
        FakeSession(id="arriving", has_live_ask=True),
        user_identifier=USER,
        thread_id=THREAD,
    )
    registry.mark_disconnected("arriving")

    assert _evicted(registry) == []


def test_the_sweep_never_looks_outside_the_thread_it_was_given(
    registry: SessionRegistry,
):
    _arriving(registry)
    _bystander(registry, session_id="here", connected=False, live_ask=True)
    _bystander(
        registry,
        session_id="elsewhere",
        connected=False,
        live_ask=True,
        thread=OTHER_THREAD,
    )

    assert _evicted(registry) == ["here"]


def test_a_sweep_of_no_thread_finds_nothing(registry: SessionRegistry):
    registry.register(
        FakeSession(id="s1", has_live_ask=True), user_identifier=USER, thread_id=None
    )
    registry.mark_disconnected("s1")

    assert registry.abandoned_ask_sessions(None) == ()


def test_the_predicate_names_all_three_conditions(registry: SessionRegistry):
    abandoned = _bystander(registry, session_id="a", connected=False, live_ask=True)
    connected = _bystander(registry, session_id="b", connected=True, live_ask=True)
    between_asks = _bystander(
        registry, session_id="c", connected=False, running_task=True
    )

    assert is_abandoned_ask_session(abandoned, THREAD) is True
    assert is_abandoned_ask_session(connected, THREAD) is False
    assert is_abandoned_ask_session(between_asks, THREAD) is False
    assert is_abandoned_ask_session(abandoned, OTHER_THREAD) is False
    assert is_parked_on_live_ask(abandoned) is True
    assert is_parked_on_live_ask(between_asks) is False


# --- The re-check before the awaiting delete ------------------------------


def test_a_candidate_that_is_still_abandoned_is_evicted(registry: SessionRegistry):
    _arriving(registry)
    candidate = _bystander(registry, connected=False, live_ask=True)

    assert (
        registry.should_evict(candidate, THREAD, arriving_session_id="arriving") is True
    )


def test_a_candidate_that_reconnected_while_a_delete_was_in_flight_survives(
    registry: SessionRegistry,
):
    _arriving(registry)
    candidate = _bystander(registry, connected=False, live_ask=True)

    registry.mark_connected(candidate.id)

    assert registry.should_evict(candidate, THREAD) is False


def test_a_candidate_replaced_under_its_id_is_not_deleted_again(
    registry: SessionRegistry,
):
    _arriving(registry)
    candidate = _bystander(registry, connected=False, live_ask=True)
    successor = _bystander(registry, connected=False, live_ask=True)

    assert registry.should_evict(candidate, THREAD) is False
    assert registry.should_evict(successor, THREAD) is True


def test_a_candidate_already_removed_is_not_deleted_again(registry: SessionRegistry):
    candidate = _bystander(registry, connected=False, live_ask=True)
    registry.remove(candidate.id)

    assert registry.should_evict(candidate, THREAD) is False


def test_the_recheck_also_refuses_the_arriving_session(registry: SessionRegistry):
    entry = registry.register(
        FakeSession(id="arriving", has_live_ask=True),
        user_identifier=USER,
        thread_id=THREAD,
    )
    registry.mark_disconnected("arriving")

    assert registry.should_evict(entry, THREAD, arriving_session_id="arriving") is False


def test_a_candidate_that_changed_conversation_is_not_evicted(
    registry: SessionRegistry,
):
    candidate = _bystander(registry, connected=False, live_ask=True)
    registry.set_thread(candidate.id, OTHER_THREAD)

    assert registry.should_evict(candidate, THREAD) is False


# --- What the conversation's other sessions protect ----------------------


def test_work_running_in_a_tab_that_is_still_open_makes_the_thread_live(
    registry: SessionRegistry,
):
    _bystander(registry, connected=True, running_task=True)

    assert registry.has_live_task(THREAD) is True


def test_work_running_behind_a_dropped_socket_still_makes_the_thread_live(
    registry: SessionRegistry,
):
    """Scenario 3's session is left alone precisely because its work is
    still work; a liveness query that skipped it would delete the messages
    that work is producing."""
    _bystander(registry, connected=False, running_task=True)

    assert registry.has_live_task(THREAD) is True


def test_an_idle_conversation_has_no_live_task(registry: SessionRegistry):
    _bystander(registry, connected=True, live_ask=True)

    assert registry.has_live_task(THREAD) is False


def test_work_running_in_another_conversation_does_not_make_this_one_live(
    registry: SessionRegistry,
):
    _bystander(registry, running_task=True, thread=OTHER_THREAD)

    assert registry.has_live_task(THREAD) is False


def test_a_thread_that_is_no_thread_is_never_live(registry: SessionRegistry):
    registry.register(FakeSession(id="s1", has_live_task=True), thread_id=None)

    assert registry.has_live_task(None) is False


def test_a_question_waiting_in_another_session_protects_its_own_step(
    registry: SessionRegistry,
):
    _arriving(registry)
    _bystander(registry, connected=True, live_ask=True, ask_step_ids=("m2",))

    assert registry.protected_step_ids(THREAD) == frozenset({"m2"})


def test_a_question_behind_a_dropped_socket_still_protects_its_step(
    registry: SessionRegistry,
):
    _bystander(registry, connected=False, live_ask=True, ask_step_ids=("m2",))

    assert registry.protected_step_ids(THREAD) == frozenset({"m2"})


def test_a_step_of_an_ask_that_is_no_longer_live_is_not_protected(
    registry: SessionRegistry,
):
    _bystander(registry, connected=True, live_ask=False, ask_step_ids=("m2",))

    assert registry.protected_step_ids(THREAD) == frozenset()


def test_a_question_in_another_conversation_protects_nothing_here(
    registry: SessionRegistry,
):
    _bystander(registry, live_ask=True, ask_step_ids=("m2",), thread=OTHER_THREAD)

    assert registry.protected_step_ids(THREAD) == frozenset()


def test_every_live_question_of_the_thread_protects_its_steps(
    registry: SessionRegistry,
):
    _bystander(registry, session_id="a", live_ask=True, ask_step_ids=("m2",))
    _bystander(registry, session_id="b", live_ask=True, ask_step_ids=("m3", "m4"))

    assert registry.protected_step_ids(THREAD) == frozenset({"m2", "m3", "m4"})


def test_no_thread_protects_no_steps(registry: SessionRegistry):
    registry.register(
        FakeSession(id="s1", has_live_ask=True, live_ask_step_ids=("m2",)),
        thread_id=None,
    )

    assert registry.protected_step_ids(None) == frozenset()


# --- Eviction first, so the conversation reads as idle again -------------


def test_evicting_the_abandoned_session_makes_the_conversation_idle_again(
    registry: SessionRegistry,
):
    """bystanders.py scenario 5, as a sequence: the sweep runs before the
    resume="delete" decision, and the decision must see the thread without
    the session that was just evicted."""
    _arriving(registry)
    _bystander(
        registry,
        connected=False,
        live_ask=True,
        ask_step_ids=("a-question-of-its-own",),
        running_task=True,
    )

    assert registry.has_live_task(THREAD) is True
    assert registry.protected_step_ids(THREAD) == frozenset({"a-question-of-its-own"})

    for entry in registry.abandoned_ask_sessions(
        THREAD, arriving_session_id="arriving"
    ):
        assert registry.should_evict(entry, THREAD, arriving_session_id="arriving")
        registry.discard(entry)

    assert registry.has_live_task(THREAD) is False
    assert registry.protected_step_ids(THREAD) == frozenset()
    assert registry.get("bystander-0") is None


def test_a_live_tab_survives_the_sweep_and_goes_on_protecting(
    registry: SessionRegistry,
):
    """bystanders.py scenario 6: nothing is evicted, so the messages the
    running work is producing are still protected afterwards."""
    _arriving(registry)
    _bystander(registry, connected=True, running_task=True)

    assert _evicted(registry) == []
    assert registry.has_live_task(THREAD) is True


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
