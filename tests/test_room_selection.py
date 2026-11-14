"""Choosing the room with ESCAPE_ROOM, and the scenario text that moved from code into room data."""

import json

import pytest

from common.config import DEFAULT_ROOM
from common.room import DEFAULT_ROOM_PATH, ROOMS, Room, active_room, default_room, load_room
from v1.prompts import build_system_prompt


def test_room_name_ignores_case_and_spaces(use_room):
    use_room(" Library ")
    assert active_room().title == "The Locked Library"


def test_empty_name_means_the_default_room(use_room):
    use_room("")
    assert active_room().title == load_room(ROOMS[DEFAULT_ROOM]).title


def test_unknown_room_name_lists_the_available_ones(use_room):
    use_room("vault")
    with pytest.raises(ValueError, match="Unknown ESCAPE_ROOM 'vault'. Available: library, shelter"):
        active_room()


def test_reference_room_does_not_follow_the_setting(use_room):
    """Tests and the attack evaluation use default_room(): always the library."""
    use_room("shelter")
    assert default_room().title == "The Locked Library"


def test_v1_prompt_numbers_the_walkthrough_from_room_data():
    prompt = build_system_prompt(default_room())
    assert "Solution walkthrough (secret):\n1. Examine the bookshelf" in prompt
    assert "\n5. Enter the four-digit founding year (1847) on the iron door to escape.\n" in prompt
    assert "{exit_code}" in default_room().walkthrough[-1]  # the code itself is stored once, on the lock


def test_room_without_walkthrough_gets_no_walkthrough_section():
    data = json.loads(DEFAULT_ROOM_PATH.read_text(encoding="utf-8"))
    del data["walkthrough"]
    room = Room.model_validate(data)
    assert room.victory == "You escaped the library"
    assert "Solution walkthrough" not in build_system_prompt(room)


def test_victory_line_has_a_neutral_default():
    data = json.loads(DEFAULT_ROOM_PATH.read_text(encoding="utf-8"))
    del data["victory"]
    assert Room.model_validate(data).victory == "You escaped"


@pytest.mark.parametrize("labels", [["ONLY ONE"], ["ONE", "TWO", "THREE"]])
def test_status_line_takes_two_labels_or_none(labels):
    data = json.loads(DEFAULT_ROOM_PATH.read_text(encoding="utf-8"))
    data["console_bar"] = labels
    with pytest.raises(ValueError, match="exactly two labels"):
        Room.model_validate(data)


@pytest.mark.parametrize("host", [{"name": "", "voice": "Plain."}, {"name": "MABEL", "voice": ""}])
def test_a_host_needs_a_name_and_a_voice(host):
    data = json.loads(DEFAULT_ROOM_PATH.read_text(encoding="utf-8"))
    data["host"] = host
    with pytest.raises(ValueError):
        Room.model_validate(data)
