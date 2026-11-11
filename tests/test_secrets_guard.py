from common.room import default_room
from v2.engine.state import new_game
from v2.secrets_guard import find_leaks, protected_codes

ROOM = default_room()


def test_closed_locks_are_protected_and_opened_ones_are_not():
    state = new_game(ROOM)
    assert sorted(protected_codes(ROOM, state)) == ["1847", "7294"]
    state.objects["drawer"].unlocked = True
    assert protected_codes(ROOM, state) == ["1847"]


def test_codes_the_player_typed_are_not_leaks():
    assert protected_codes(ROOM, new_game(ROOM), "I enter 1847 on the drawer") == ["7294"]


def test_leaks_are_found_with_separators():
    codes = ["7294", "1847"]
    assert find_leaks("Perhaps the year is 1847?", codes) == ["1847"]
    assert find_leaks("Try 7-2-9-4 on the drawer.", codes) == ["7294"]
    assert find_leaks("Read in order: 7, 2, 9, 4.", codes) == ["7294"]


def test_clue_texts_are_not_flagged():
    codes = ["7294", "1847"]
    for fact in ROOM.facts:
        assert find_leaks(fact.text, codes) == [], fact.id
    assert find_leaks("The clock shows IV and VII; the year begins with 18.", codes) == []
