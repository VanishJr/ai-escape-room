"""Last line of defence: make sure a reply never reveals an unsolved code.

The narrator never receives the codes, but once the player has found clues it
does see them (for example the four digits on the volumes) and could combine
them for the player. Every reply is therefore checked against the codes of
locks that are still closed, after normalising digits ("7-2-9-4", "７２９４",
"7, 2, 9, 4" all count as 7294).
"""

from __future__ import annotations

from common.room import Room
from v2.digits import join_digits
from v2.engine.state import GameState


def protected_codes(room: Room, state: GameState, entered_code: str | None = None) -> list[str]:
    """Codes of locks that are still closed.

    Only the code the player actually entered into a lock this turn is exempt, so
    the narrator may say "you type 1847". A code merely mentioned in a question
    ("is it 1847?") stays protected.
    """
    return [
        o.lock.code
        for o in room.objects
        if o.lock and not state.objects[o.id].unlocked and o.lock.code != entered_code
    ]


def find_leaks(text: str, codes: list[str]) -> list[str]:
    normalized = join_digits(text)
    return [code for code in codes if code in normalized]
