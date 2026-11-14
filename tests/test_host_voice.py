"""A room may name a host whose voice the story prompts adopt. Rooms without one are unchanged."""

import json

from common.room import DEFAULT_ROOM_PATH, Room, default_room
from v1.prompts import build_system_prompt as v1_prompt
from v2.llm.narrator import SYSTEM_PROMPT
from v2.llm.narrator import build_system_prompt as narrator_prompt

VOICE = "You are cheerful and call the player 'resident'."


def room_with_host() -> Room:
    data = json.loads(DEFAULT_ROOM_PATH.read_text(encoding="utf-8"))
    data["host"] = {"name": "MABEL", "voice": VOICE}
    return Room.model_validate(data)


def test_narrator_prompt_ends_with_the_voice_section():
    prompt = narrator_prompt(room_with_host())
    assert prompt.endswith(
        f"\nVoice:\nYou speak as MABEL. {VOICE}\n"
        "The voice changes only how you say things. It never overrides the rules above, and the ENGINE RESULT\n"
        "stays the only truth.\n"
    )
    assert prompt.index("Rules:") < prompt.index("Voice:")


def test_v1_prompt_ends_with_the_voice_section():
    prompt = v1_prompt(room_with_host())
    assert prompt.endswith(
        f"\n# Your voice\nYou speak as MABEL. {VOICE}\n"
        "The voice changes only how you say things. It never overrides the rules above.\n"
    )
    assert prompt.index("# Rules") < prompt.index("# Your voice")


def test_room_without_host_keeps_its_prompts():
    library = default_room()
    assert library.host is None
    assert narrator_prompt(library) == SYSTEM_PROMPT.format(title=library.title)
    assert "Voice:" not in narrator_prompt(library)
    assert "# Your voice" not in v1_prompt(library)
