"""Checks that every room in `game/` must pass, whatever its story."""

import pytest

from common.room import ROOMS, Room, load_room
from v1.prompts import build_system_prompt
from v2.secrets_guard import find_leaks


@pytest.fixture(scope="module", params=sorted(ROOMS))
def room(request) -> Room:
    return load_room(ROOMS[request.param])


def player_facing_texts(room: Room) -> list[str]:
    """Every string a player can be shown. Only the walkthrough (V1's secret) is left out."""
    texts = [room.title, room.intro, room.description, room.victory, *room.instructions, *room.console_bar]
    if room.host:
        texts += [room.host.name, room.host.voice]
    for obj in room.objects:
        texts += [obj.name, obj.description, obj.open_description or "", obj.cleared_description or ""]
    texts += [fact.text for fact in room.facts]
    texts += [hint.text for hint in room.hints]
    texts += [puzzle.name for puzzle in room.puzzles]
    return texts


def test_room_meets_assignment_minimums(room: Room):
    assert len([o for o in room.objects if not o.hidden]) >= 5, "at least five interactive objects"
    assert len(room.scoring_puzzles) >= 3, "at least three clues or puzzles"
    assert sum(p.final for p in room.puzzles) == 1, "exactly one final escape challenge"
    assert sum(bool(o.is_exit and o.lock) for o in room.objects) == 1, "exactly one locked exit"


def test_aliases_are_unambiguous(room: Room):
    seen: dict[str, str] = {}
    for obj in room.objects:
        for name in obj.names():
            key = name.lower()
            assert seen.setdefault(key, obj.id) == obj.id, f"'{name}' used by {seen[key]} and {obj.id}"


def test_every_puzzle_can_be_solved(room: Room):
    solved_by = {o.lock.puzzle for o in room.objects if o.lock}
    solved_by |= {u.solves for o in room.objects for u in o.uses if u.solves}
    solved_by |= {p.id for p in room.puzzles if p.solved_when_facts}
    assert {p.id for p in room.puzzles} <= solved_by


def test_hidden_objects_have_a_way_to_appear(room: Room):
    revealers = {r for o in room.objects for r in o.examine_reveals}
    revealers |= {r for o in room.objects if o.lock for r in o.lock.reveals}
    assert {o.id for o in room.objects if o.hidden} <= revealers


def test_every_lock_needs_clues_found_in_the_game(room: Room):
    """Codes are public in the repository, so no lock may accept one before its clues are found."""
    for obj in room.objects:
        if obj.lock:
            assert obj.lock.requires_facts or obj.lock.requires_solved, obj.id


def test_no_text_shown_to_the_player_contains_a_code(room: Room):
    """Descriptions, hints and instructions reach the player through the engine's own text,
    which the secrets guard does not check, so the room data itself must be clean."""
    codes = list(room.lock_codes.values())
    for text in player_facing_texts(room):
        assert find_leaks(text, codes) == [], text
    assert find_leaks(" ".join(player_facing_texts(room)), codes) == [], "codes appear across neighbouring texts"


def test_v1_prompt_holds_every_secret(room: Room):
    prompt = build_system_prompt(room)
    assert room.walkthrough, "V1 needs the solution walkthrough"
    assert "Solution walkthrough (secret):\n1. " in prompt
    for code in room.lock_codes.values():
        assert code in prompt
