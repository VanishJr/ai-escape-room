"""V2 engine: rules are enforced in Python, independent of any LLM."""

import pytest

from common.room import Room, default_room
from v2.engine.actions import Action, Intent
from v2.engine.rules import Outcome, resolve
from v2.engine.state import GameState, new_game

WALKTHROUGH = [
    Action(intent=Intent.EXAMINE, target="bookshelf"),
    Action(intent=Intent.EXAMINE, target="volumes"),
    Action(intent=Intent.ENTER_CODE, target="drawer", code="7294"),
    Action(intent=Intent.TAKE, target="uv_flashlight"),
    Action(intent=Intent.USE, item="uv_flashlight", target="painting"),
    Action(intent=Intent.EXAMINE, target="desk"),
    Action(intent=Intent.EXAMINE, target="note"),
    Action(intent=Intent.EXAMINE, target="clock"),
    Action(intent=Intent.ENTER_CODE, target="door", code="1847"),
]


@pytest.fixture(scope="module")
def room() -> Room:
    return default_room()


def play(room: Room, actions: list[Action], state: GameState | None = None) -> tuple[GameState, list[Outcome]]:
    state = state or new_game(room)
    outcomes = []
    for action in actions:
        state, outcome = resolve(state, action, room)
        outcomes.append(outcome)
    return state, outcomes


def progress(state: GameState) -> tuple:
    """Everything that counts as game progress (ignores turn counter and log)."""
    unlocked = sorted(o for o, st in state.objects.items() if st.unlocked)
    visible = sorted(o for o, st in state.objects.items() if st.visible)
    return sorted(state.solved), sorted(state.inventory), unlocked, visible, state.escaped


# ---- normal play ---------------------------------------------------------------
def test_full_walkthrough_escapes(room):
    state, outcomes = play(room, WALKTHROUGH)
    assert all(o.success for o in outcomes)
    assert state.escaped and outcomes[-1].event == "escaped"
    assert state.solved == ["volumes", "painting", "clock", "exit"]


def test_puzzle_solved_by_facts_once_note_and_clock_are_known(room):
    state, outcomes = play(room, [WALKTHROUGH[5], WALKTHROUGH[6]])  # desk, note
    assert "clock" not in state.solved
    state, outcomes = play(room, [Action(intent=Intent.EXAMINE, target="clock")], state)
    assert outcomes[0].newly_solved == ["The stopped clock"]


def test_hints_follow_progress(room):
    state, (hint,) = play(room, [Action(intent=Intent.HINT)])
    assert "bookshelf" in hint.message
    state, _ = play(room, WALKTHROUGH[:4])
    _, (hint,) = play(room, [Action(intent=Intent.HINT)], state)
    assert "special light" in hint.message


def test_resolve_never_mutates_the_input_state(room):
    state = new_game(room)
    before = state.model_dump()
    resolve(state, WALKTHROUGH[0], room)
    assert state.model_dump() == before


def test_game_is_over_after_escape(room):
    state, _ = play(room, WALKTHROUGH)
    _, (outcome,) = play(room, [Action(intent=Intent.LOOK)], state)
    assert outcome.event == "game_over"


# ---- assignment challenge 1: using an item that was not collected ----------------
def test_cannot_use_flashlight_before_finding_it(room):
    state, (outcome,) = play(room, [Action(intent=Intent.USE, item="uv flashlight", target="painting")])
    assert outcome.event == "not_in_inventory"
    assert "painting" not in state.solved and outcome.facts == []


def test_cannot_use_flashlight_left_in_the_drawer(room):
    state, _ = play(room, WALKTHROUGH[:3])  # drawer open, flashlight not taken
    _, (outcome,) = play(room, [Action(intent=Intent.USE, item="flashlight", target="painting")], state)
    assert outcome.event == "not_in_inventory"


def test_hidden_objects_cannot_be_reached_before_discovery(room):
    _, outcomes = play(
        room,
        [Action(intent=Intent.TAKE, target="uv_flashlight"), Action(intent=Intent.EXAMINE, target="volumes")],
    )
    assert [o.event for o in outcomes] == ["not_found", "not_found"]


# ---- challenge 2: incorrect combination ----------------------------------------
def test_wrong_code_keeps_drawer_locked(room):
    state, _ = play(room, WALKTHROUGH[:2])
    state, (outcome,) = play(room, [Action(intent=Intent.ENTER_CODE, target="drawer", code="9742")], state)
    assert outcome.event == "wrong_code"
    assert not state.objects["drawer"].unlocked
    assert not state.objects["uv_flashlight"].visible
    assert "7294" not in outcome.message


def test_three_wrong_codes_jam_the_lock_then_it_recovers(room):
    wrong = [Action(intent=Intent.ENTER_CODE, target="door", code=c) for c in ("0000", "1234", "1900")]
    state, outcomes = play(room, wrong)
    assert [o.event for o in outcomes] == ["wrong_code", "wrong_code", "wrong_code_jammed"]

    correct = Action(intent=Intent.ENTER_CODE, target="door", code="1847")
    state, (outcome,) = play(room, [correct], state)
    assert outcome.event == "jammed" and not state.escaped

    state, _ = play(room, [Action(intent=Intent.LOOK)] * 2, state)  # wait out the lockout
    state, (outcome,) = play(room, [correct], state)
    assert outcome.event == "escaped"


@pytest.mark.parametrize("code", ["12a4", "123", "12345", None])
def test_malformed_codes_are_rejected(room, code):
    _, (outcome,) = play(room, [Action(intent=Intent.ENTER_CODE, target="drawer", code=code)])
    assert outcome.event == "invalid_code"


# ---- challenges 3-5: claims, impersonation, asking for secrets -----------------
def test_meta_requests_change_nothing(room):
    state, _ = play(room, WALKTHROUGH[:4])
    after, (outcome,) = play(room, [Action(intent=Intent.META)], state)
    assert outcome.event == "meta_refused" and not outcome.success
    assert progress(after) == progress(state)
    assert "1847" not in outcome.message and "7294" not in outcome.message


def test_correct_door_code_works_without_clues(room):
    """Design decision: the keypad checks the code, not how the player learned it."""
    state, (outcome,) = play(room, [Action(intent=Intent.ENTER_CODE, target="door", code="1847")])
    assert outcome.event == "escaped" and state.escaped


# ---- invalid actions: objects that do not exist ----------------------------------
def test_nonexistent_objects_are_refused(room):
    state, outcomes = play(
        room,
        [
            Action(intent=Intent.TAKE, target="crowbar"),
            Action(intent=Intent.USE, item="crowbar", target="door"),
            Action(intent=Intent.OPEN, target="window"),
        ],
    )
    assert [o.event for o in outcomes] == ["not_found", "not_in_inventory", "not_found"]
    assert progress(state) == progress(new_game(room))


def test_objects_that_cannot_be_taken(room):
    _, (outcome,) = play(room, [Action(intent=Intent.TAKE, target="globe")])
    assert outcome.event == "not_takeable"


# ---- consistency -----------------------------------------------------------------
def test_opened_drawer_stays_open_for_the_rest_of_the_game(room):
    state, _ = play(room, WALKTHROUGH[:3])
    filler = [Action(intent=Intent.LOOK), Action(intent=Intent.EXAMINE, target="globe")] * 10
    state, _ = play(room, filler, state)
    state, outcomes = play(
        room,
        [Action(intent=Intent.OPEN, target="drawer"), Action(intent=Intent.ENTER_CODE, target="drawer", code="0000")],
        state,
    )
    assert [o.event for o in outcomes] == ["already_open", "already_open"]
    assert state.objects["drawer"].unlocked and state.solved == ["volumes"]


def test_code_without_target_goes_to_the_only_closed_lock(room):
    _, (outcome,) = play(room, [Action(intent=Intent.ENTER_CODE, code="1847")])
    assert outcome.event == "which_lock"  # drawer and door are both closed
    state, _ = play(room, WALKTHROUGH[:3])
    _, (outcome,) = play(room, [Action(intent=Intent.ENTER_CODE, code="1847")], state)
    assert outcome.event == "escaped"
