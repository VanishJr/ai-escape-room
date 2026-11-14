"""Room scenario schema and loader.

A scenario is one JSON file in `game/`, shared by both versions:
- V1 renders all of it (including the solutions) into the LLM system prompt.
- V2 keeps it in Python: the engine reads the codes and rules, and the LLM only
  ever sees the parts the player has already discovered.

`ROOMS` lists the scenarios. The apps play `active_room()`, chosen with the
ESCAPE_ROOM setting; `default_room()` is always the library, the reference room
of the test suite and of the attack evaluation.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, PrivateAttr

from common.config import PROJECT_ROOT, get_settings

DEFAULT_ROOM_PATH = PROJECT_ROOT / "game" / "room.json"
ROOMS: dict[str, Path] = {
    "library": DEFAULT_ROOM_PATH,
    "shelter": PROJECT_ROOT / "game" / "shelter.json",
}


class Lock(BaseModel):
    code: str = Field(pattern=r"^[0-9]{4}$")  # the secret solution
    puzzle: str  # puzzle solved when the lock opens
    # The lock only accepts codes once the player has found its clues, so a code
    # learned outside the game (e.g. from this file) cannot skip the puzzles.
    requires_facts: list[str] = []
    requires_solved: list[str] = []
    on_unlock_facts: list[str] = []
    reveals: list[str] = []  # objects that become visible when it opens
    max_attempts: int = 3  # wrong codes in a row before the lock jams
    lockout_turns: int = 3


class Use(BaseModel):
    """`use <item> on <object>`: what happens when the right item is used here."""

    item: str
    facts: list[str] = []
    solves: str | None = None


class GameObject(BaseModel):
    id: str
    name: str
    aliases: list[str] = []
    description: str
    open_description: str | None = None  # for locks, once opened
    cleared_description: str | None = None  # once everything it reveals has been taken
    hidden: bool = False  # invisible until revealed by another action
    takeable: bool = False
    is_exit: bool = False
    examine_reveals: list[str] = []
    examine_facts: list[str] = []
    lock: Lock | None = None
    uses: list[Use] = []

    def names(self) -> list[str]:
        return [self.id.replace("_", " "), self.name, *self.aliases]


class Fact(BaseModel):
    id: str
    text: str


class Puzzle(BaseModel):
    id: str
    name: str
    solved_when_facts: list[str] = []  # solved once all these facts are known
    final: bool = False


class Hint(BaseModel):
    puzzle: str  # shown only while this puzzle is unsolved
    text: str
    requires_facts: list[str] = []
    requires_solved: list[str] = []


class Room(BaseModel):
    title: str
    intro: str
    instructions: list[str]
    description: str
    objects: list[GameObject]
    facts: list[Fact]
    puzzles: list[Puzzle]
    hints: list[Hint]
    walkthrough: list[str] = []  # the secret solution, step by step (V1 puts it into its prompt)
    victory: str = "You escaped"  # start of the message shown after the escape
    console_bar: list[str] = []  # two short labels for the status line above the title

    # ---- lookups -------------------------------------------------------
    def obj(self, object_id: str) -> GameObject:
        return self._objects[object_id]

    def fact(self, fact_id: str) -> Fact:
        return self._facts[fact_id]

    def puzzle(self, puzzle_id: str) -> Puzzle:
        return self._puzzles[puzzle_id]

    def find_object(self, name: str | None) -> GameObject | None:
        """Match free text ("the old clock", "Clock") to an object by id, name or alias."""
        if not name:
            return None
        wanted = _normalize(name)
        if wanted in self._objects:
            return self._objects[wanted]
        for obj in self.objects:
            if wanted in (_normalize(n) for n in obj.names()):
                return obj
        return None

    @property
    def scoring_puzzles(self) -> list[Puzzle]:
        """Puzzles shown as progress (everything except the final door)."""
        return [p for p in self.puzzles if not p.final]

    @property
    def lock_codes(self) -> dict[str, str]:
        """puzzle id -> secret code, for every lock in the room."""
        return {o.lock.puzzle: o.lock.code for o in self.objects if o.lock}

    # ---- validation ----------------------------------------------------
    _objects: dict[str, GameObject] = PrivateAttr(default_factory=dict)
    _facts: dict[str, Fact] = PrivateAttr(default_factory=dict)
    _puzzles: dict[str, Puzzle] = PrivateAttr(default_factory=dict)

    def model_post_init(self, context: object, /) -> None:
        self._objects = {o.id: o for o in self.objects}
        self._facts = {f.id: f for f in self.facts}
        self._puzzles = {p.id: p for p in self.puzzles}
        self._check_references()

    def _check_references(self) -> None:
        for kind, items, index in (
            ("object", self.objects, self._objects),
            ("fact", self.facts, self._facts),
            ("puzzle", self.puzzles, self._puzzles),
        ):
            if len(items) != len(index):
                raise ValueError(f"duplicate {kind} ids")

        def check(ids: list[str], index: dict, where: str) -> None:
            missing = [i for i in ids if i not in index]
            if missing:
                raise ValueError(f"{where} references unknown ids: {missing}")

        for o in self.objects:
            check(o.examine_reveals, self._objects, f"{o.id}.examine_reveals")
            check(o.examine_facts, self._facts, f"{o.id}.examine_facts")
            if o.lock:
                check([o.lock.puzzle, *o.lock.requires_solved], self._puzzles, f"{o.id}.lock.puzzle")
                check(o.lock.requires_facts, self._facts, f"{o.id}.lock.requires_facts")
                check(o.lock.on_unlock_facts, self._facts, f"{o.id}.lock.on_unlock_facts")
                check(o.lock.reveals, self._objects, f"{o.id}.lock.reveals")
            for use in o.uses:
                check([use.item], self._objects, f"{o.id}.uses.item")
                check(use.facts, self._facts, f"{o.id}.uses.facts")
                check([use.solves] if use.solves else [], self._puzzles, f"{o.id}.uses.solves")
        for p in self.puzzles:
            check(p.solved_when_facts, self._facts, f"puzzle {p.id}")
        for h in self.hints:
            check([h.puzzle, *h.requires_solved], self._puzzles, "hint")
            check(h.requires_facts, self._facts, "hint")


def _normalize(text: str) -> str:
    text = re.sub(r"['’]s\b", "", text.lower())  # "the clock's hands" -> "the clock hands"
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"^(the|a|an|my|your|this|that|these|those) ", "", " ".join(text.split()))
    return text


def load_room(path: Path | str = DEFAULT_ROOM_PATH) -> Room:
    return Room.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def default_room() -> Room:
    """The library: the reference room, whatever ESCAPE_ROOM says."""
    return load_room()


@lru_cache(maxsize=None)
def _named_room(name: str) -> Room:
    return load_room(ROOMS[name])


def active_room() -> Room:
    """The room the apps play, chosen with the ESCAPE_ROOM setting."""
    name = get_settings().room
    if name not in ROOMS:
        raise ValueError(f"Unknown ESCAPE_ROOM '{name}'. Available: {', '.join(ROOMS)}")
    return _named_room(name)
