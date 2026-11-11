import json

import pytest

from common.room import DEFAULT_ROOM_PATH, Room, load_room


@pytest.fixture(scope="module")
def room() -> Room:
    return load_room()


def test_room_meets_assignment_minimums(room: Room):
    visible = [o for o in room.objects if not o.hidden]
    assert len(visible) >= 5, "at least five interactive objects"
    assert len(room.scoring_puzzles) >= 3, "at least three clues or puzzles"
    assert sum(p.final for p in room.puzzles) == 1, "exactly one final escape challenge"
    assert any(o.is_exit and o.lock for o in room.objects)


def test_find_object_matches_names_and_aliases(room: Room):
    assert room.find_object("The old Clock").id == "clock"
    assert room.find_object("uv flashlight").id == "uv_flashlight"
    assert room.find_object("Bookshelf").id == "bookshelf"
    assert room.find_object("crowbar") is None
    assert room.find_object(None) is None


@pytest.mark.parametrize(
    "words, expected",
    [
        ("the drawer lock", "drawer"),
        ("Volume III", "volumes"),
        ("Vol. I", "volumes"),
        ("the clock's hands", "clock"),
        ("my flashlight", "uv_flashlight"),
        ("UV-flashlight", "uv_flashlight"),
        ("the door keypad", "door"),
    ],
)
def test_natural_wordings_resolve(room: Room, words, expected):
    assert room.find_object(words).id == expected


def test_message_is_not_an_alias_of_the_note(room: Room):
    """'the message' usually means the glowing text on the portrait, not the note."""
    assert room.find_object("message") is None


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


def test_unknown_reference_is_rejected():
    data = json.loads(DEFAULT_ROOM_PATH.read_text())
    data["objects"][0]["examine_reveals"] = ["does_not_exist"]
    with pytest.raises(ValueError, match="unknown ids"):
        Room.model_validate(data)
