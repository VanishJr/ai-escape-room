"""Last line of defence: make sure a reply never reveals an unsolved code.

The narrator never receives the codes, but it does receive clues (for example
the four digits on the volumes) and could combine them for the player. Every
reply is checked against the codes of locks that are still closed.
"""

from __future__ import annotations

import re

from common.room import Room
from v2.engine.state import GameState

# "7-2-9-4", "7, 2, 9, 4" and "7 2 9 4" all count as "7294"
_DIGIT_SEPARATORS = re.compile(r"(?<=\d)[\s,.\-–—]{1,3}(?=\d)")


def protected_codes(room: Room, state: GameState, player_text: str = "") -> list[str]:
    """Codes of locks that are still closed, except codes the player typed themselves."""
    typed = _normalize(player_text)
    return [
        o.lock.code
        for o in room.objects
        if o.lock and not state.objects[o.id].unlocked and o.lock.code not in typed
    ]


def find_leaks(text: str, codes: list[str]) -> list[str]:
    normalized = _normalize(text)
    return [code for code in codes if code in normalized]


def _normalize(text: str) -> str:
    return _DIGIT_SEPARATORS.sub("", text)
