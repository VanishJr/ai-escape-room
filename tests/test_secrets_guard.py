import pytest

from common.room import default_room
from v2.engine.state import new_game
from v2.secrets_guard import find_leaks, protected_codes

ROOM = default_room()
CODES = ["7294", "1847"]


def test_closed_locks_are_protected_and_opened_ones_are_not():
    state = new_game(ROOM)
    assert sorted(protected_codes(ROOM, state)) == ["1847", "7294"]
    state.objects["drawer"].unlocked = True
    assert protected_codes(ROOM, state) == ["1847"]


def test_only_the_code_entered_this_turn_is_exempt():
    assert protected_codes(ROOM, new_game(ROOM), entered_code="1847") == ["7294"]
    assert sorted(protected_codes(ROOM, new_game(ROOM), entered_code=None)) == ["1847", "7294"]


@pytest.mark.parametrize(
    "reply",
    [
        "Perhaps the year is 1847?",
        "Try 7-2-9-4 on the drawer.",
        "Read in order: 7, 2, 9, 4.",
        "The digits glow: 1 / 8 / 4 / 7.",
        "1·8·4·7",
        "１８４７",
        "18_47",
        "7​2​9​4",
    ],
)
def test_leaks_are_found_in_many_formats(reply):
    assert find_leaks(reply, CODES)


def test_clue_texts_are_not_flagged():
    for fact in ROOM.facts:
        assert find_leaks(fact.text, CODES) == [], fact.id
    assert find_leaks("The clock shows IV and VII; the year begins with 18.", CODES) == []
