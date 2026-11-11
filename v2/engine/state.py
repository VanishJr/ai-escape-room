"""Structured game state for V2.

Everything that decides the game lives here, outside the LLM: which objects the
player has discovered, what they carry, which locks are open, which puzzles are
solved and whether the door is open. The LLM never edits this state.
"""

from __future__ import annotations

from pydantic import BaseModel

from common.room import Room


class ObjectState(BaseModel):
    visible: bool  # discovered by the player
    examined: bool = False
    unlocked: bool = False  # only meaningful for objects with a lock


class LogEntry(BaseModel):
    turn: int
    intent: str
    target: str | None = None
    item: str | None = None
    code: str | None = None
    event: str
    success: bool


class GameState(BaseModel):
    objects: dict[str, ObjectState]
    inventory: list[str] = []
    solved: list[str] = []  # puzzle ids, in the order they were solved
    known_facts: list[str] = []  # fact ids the player has discovered
    failed_attempts: dict[str, int] = {}  # lock id -> wrong codes in a row
    jammed_until: dict[str, int] = {}  # lock id -> first turn on which it accepts codes again
    turn: int = 0
    escaped: bool = False
    log: list[LogEntry] = []

    def is_visible(self, object_id: str) -> bool:
        return self.objects[object_id].visible

    def carries(self, object_id: str) -> bool:
        return object_id in self.inventory


def new_game(room: Room) -> GameState:
    return GameState(objects={o.id: ObjectState(visible=not o.hidden) for o in room.objects})
