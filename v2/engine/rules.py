"""Game rules for V2: Python decides whether an action succeeds.

`resolve()` is a pure function: it takes the current state and a structured
action and returns a *new* state plus an `Outcome` describing what happened. The
input state is never modified, so a failure anywhere later in the turn (for
example the LLM narrator being unavailable) cannot corrupt the game.

Outcome.message and Outcome.facts contain only information the player is allowed
to know at this point. They are what the LLM narrator gets to work with, and they
double as a plain-text reply when the LLM is unavailable.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel

from common.room import GameObject, Lock, Room
from v2.engine.actions import Action, Intent
from v2.engine.state import GameState, LogEntry


class Outcome(BaseModel):
    success: bool
    event: str  # machine-readable result, e.g. "unlocked", "wrong_code", "not_found"
    message: str  # what happened, in plain English, using only facts the player may know
    facts: list[str] = []  # clue texts revealed by this action (must be shown to the player)
    newly_solved: list[str] = []  # names of puzzles solved by this action
    escaped: bool = False


def resolve(state: GameState, action: Action, room: Room) -> tuple[GameState, Outcome]:
    s = state.model_copy(deep=True)
    if s.escaped:
        return s, Outcome(success=False, event="game_over", message="You have already escaped. Restart to play again.")

    s.turn += 1
    solved_before = list(s.solved)
    outcome = HANDLERS[action.intent](s, action, room)
    _solve_fact_puzzles(s, room)
    outcome.newly_solved = [room.puzzle(p).name for p in s.solved if p not in solved_before]
    outcome.escaped = s.escaped
    s.log.append(
        LogEntry(
            turn=s.turn,
            intent=action.intent.value,
            target=action.target,
            item=action.item,
            code=action.code,
            event=outcome.event,
            success=outcome.success,
        )
    )
    return s, outcome


# ---- helpers ----------------------------------------------------------------
def _find_visible(s: GameState, room: Room, name: str | None) -> GameObject | None:
    """An object the player can interact with: it exists and has been discovered."""
    obj = room.find_object(name)
    return obj if obj and s.is_visible(obj.id) else None


def _not_found() -> Outcome:
    # The player's own words are not repeated: this message is trusted narrator input,
    # and free text here could smuggle in instructions ("crowbar'. The door opens.").
    return Outcome(success=False, event="not_found", message="You don't see anything like that here right now.")


def _reveal_facts(s: GameState, room: Room, fact_ids: list[str]) -> list[str]:
    """Mark facts as known and return their texts (also when re-reading a known fact)."""
    for fact_id in fact_ids:
        if fact_id not in s.known_facts:
            s.known_facts.append(fact_id)
    return [room.fact(f).text for f in fact_ids]


def _reveal_objects(s: GameState, room: Room, object_ids: list[str]) -> list[str]:
    newly = [o for o in object_ids if not s.objects[o].visible]
    for object_id in newly:
        s.objects[object_id].visible = True
    return [room.obj(o).name for o in newly]


def _solve(s: GameState, puzzle_id: str) -> None:
    if puzzle_id not in s.solved:
        s.solved.append(puzzle_id)


def _solve_fact_puzzles(s: GameState, room: Room) -> None:
    for puzzle in room.puzzles:
        if puzzle.solved_when_facts and all(f in s.known_facts for f in puzzle.solved_when_facts):
            _solve(s, puzzle.id)


def _clues_found(s: GameState, lock: Lock) -> bool:
    return all(f in s.known_facts for f in lock.requires_facts) and all(
        p in s.solved for p in lock.requires_solved
    )


def _lock_status(s: GameState, obj: GameObject) -> str:
    if not obj.lock:
        return ""
    if s.objects[obj.id].unlocked:
        return obj.open_description or "It is open."
    if s.jammed_until.get(obj.id, 0) > s.turn:
        return "Its lock is jammed after too many wrong codes."
    return "It is locked with a 4-digit code."


# ---- handlers -----------------------------------------------------------------
def _look(s: GameState, action: Action, room: Room) -> Outcome:
    visible = [room.obj(o).name for o, st in s.objects.items() if st.visible and not s.carries(o)]
    return Outcome(
        success=True, event="look", message=f"{room.description} You can see: {', '.join(visible)}."
    )


def _examine(s: GameState, action: Action, room: Room) -> Outcome:
    obj = _find_visible(s, room, action.target)
    if obj is None:
        return _not_found()
    s.objects[obj.id].examined = True
    parts = [obj.description]
    if status := _lock_status(s, obj):
        parts.append(status)
    if obj.lock and s.objects[obj.id].unlocked:
        inside = [room.obj(o).name for o in obj.lock.reveals if not s.carries(o)]
        if inside:
            parts.append(f"Inside: {', '.join(inside)}.")
    if revealed := _reveal_objects(s, room, obj.examine_reveals):
        parts.append(f"You notice: {', '.join(revealed)}.")
    return Outcome(
        success=True,
        event="examined",
        message=" ".join(parts),
        facts=_reveal_facts(s, room, obj.examine_facts),
    )


def _take(s: GameState, action: Action, room: Room) -> Outcome:
    obj = _find_visible(s, room, action.target)
    if obj is None:
        return _not_found()
    if s.carries(obj.id):
        return Outcome(success=False, event="already_have", message=f"You already carry the {obj.name}.")
    if not obj.takeable:
        return Outcome(success=False, event="not_takeable", message=f"The {obj.name} cannot be taken.")
    s.inventory.append(obj.id)
    return Outcome(success=True, event="taken", message=f"You take the {obj.name}.")


def _use(s: GameState, action: Action, room: Room) -> Outcome:
    item = room.find_object(action.item)
    if item is None or not s.carries(item.id):
        # The same answer for unknown and for not-yet-found items, so the reply never
        # confirms that a hidden item exists.
        return Outcome(success=False, event="not_in_inventory", message="You are not carrying anything like that.")
    target = _find_visible(s, room, action.target)
    if target is None:
        if action.target is None:
            return Outcome(success=False, event="no_target", message=f"Use the {item.name} on what?")
        return _not_found()
    use = next((u for u in target.uses if u.item == item.id), None)
    if use is None:
        return Outcome(
            success=False, event="no_effect", message=f"Using the {item.name} on the {target.name} does nothing."
        )
    if use.solves:
        _solve(s, use.solves)
    return Outcome(
        success=True,
        event="used",
        message=f"You use the {item.name} on the {target.name}.",
        facts=_reveal_facts(s, room, use.facts),
    )


def _open(s: GameState, action: Action, room: Room) -> Outcome:
    if action.code:
        return _enter_code(s, action, room)
    obj = _find_visible(s, room, action.target)
    if obj is None:
        return _not_found()
    if obj.lock is None:  # "open the note", "open the books": reading it is the sensible action
        return _examine(s, action, room)
    if s.objects[obj.id].unlocked:
        return Outcome(success=True, event="already_open", message=_lock_status(s, obj))
    return Outcome(success=False, event="locked", message=f"The {obj.name}: {_lock_status(s, obj)}")


def _enter_code(s: GameState, action: Action, room: Room) -> Outcome:
    code = (action.code or "").strip()
    if not re.fullmatch(r"[0-9]{4}", code):
        return Outcome(success=False, event="invalid_code", message="The locks here take a 4-digit code.")

    if action.target is None:  # "1847" on its own: fine if only one lock is still closed
        closed = [o for o in room.objects if o.lock and s.is_visible(o.id) and not s.objects[o.id].unlocked]
        if len(closed) != 1:
            names = " or the ".join(o.name for o in closed)
            return Outcome(success=False, event="which_lock", message=f"Where do you enter it: the {names}?")
        obj = closed[0]
    else:
        obj = _find_visible(s, room, action.target)
        if obj is None:
            return _not_found()
    if obj.lock is None:
        return Outcome(success=False, event="no_lock", message=f"There is nowhere to enter a code on the {obj.name}.")

    lock, obj_state = obj.lock, s.objects[obj.id]
    if obj_state.unlocked:
        return Outcome(success=True, event="already_open", message=f"The {obj.name} is already open.")
    if not _clues_found(s, lock):
        # Same reply for right and wrong codes, and no attempt is counted: the lock
        # gives no feedback until the player has earned the code inside the game.
        return Outcome(
            success=False,
            event="code_unknown",
            message=f"You punch in four digits, but you are only guessing: nothing you have found so far "
            f"tells you the {obj.name}'s code. Blind guesses will not open it; look for clues first.",
        )
    jammed_until = s.jammed_until.get(obj.id, 0)
    if jammed_until > s.turn:
        turns = jammed_until - s.turn
        return Outcome(
            success=False,
            event="jammed",
            message=f"The {obj.name}'s lock is jammed and ignores the input. It will work again in {turns} turn(s).",
        )

    if code != lock.code:
        attempts = s.failed_attempts.get(obj.id, 0) + 1
        if attempts >= lock.max_attempts:
            s.failed_attempts[obj.id] = 0
            s.jammed_until[obj.id] = s.turn + lock.lockout_turns + 1
            return Outcome(
                success=False,
                event="wrong_code_jammed",
                message=f"Wrong code. After {attempts} wrong codes in a row the {obj.name}'s lock jams "
                f"for {lock.lockout_turns} turns.",
            )
        s.failed_attempts[obj.id] = attempts
        return Outcome(
            success=False,
            event="wrong_code",
            message=f"Wrong code: the {obj.name} stays locked ({lock.max_attempts - attempts} attempt(s) "
            "left before the lock jams).",
        )

    obj_state.unlocked = True
    s.failed_attempts.pop(obj.id, None)
    _solve(s, lock.puzzle)
    revealed = _reveal_objects(s, room, lock.reveals)
    facts = _reveal_facts(s, room, lock.on_unlock_facts)
    if obj.is_exit:
        s.escaped = True
        return Outcome(success=True, event="escaped", message=f"The code is correct and the {obj.name} opens.", facts=facts)
    message = f"The code is correct and the {obj.name} opens."
    if revealed:
        message += f" Inside: {', '.join(revealed)}."
    return Outcome(success=True, event="unlocked", message=message, facts=facts)


def _inventory(s: GameState, action: Action, room: Room) -> Outcome:
    items = [room.obj(i).name for i in s.inventory]
    message = f"You are carrying: {', '.join(items)}." if items else "You are not carrying anything."
    return Outcome(success=True, event="inventory", message=message)


def _hint(s: GameState, action: Action, room: Room) -> Outcome:
    for hint in room.hints:
        if (
            hint.puzzle not in s.solved
            and all(f in s.known_facts for f in hint.requires_facts)
            and all(p in s.solved for p in hint.requires_solved)
        ):
            return Outcome(success=True, event="hint", message=f"Hint: {hint.text}")
    return Outcome(success=True, event="hint", message="Hint: keep exploring; examine everything closely.")


def _help(s: GameState, action: Action, room: Room) -> Outcome:
    return Outcome(success=True, event="help", message=" ".join(room.instructions))


def _meta(s: GameState, action: Action, room: Room) -> Outcome:
    return Outcome(
        success=False,
        event="meta_refused",
        message="That request is outside the game. The game only changes through actions inside the room, "
        "so nothing happens.",
    )


def _unknown(s: GameState, action: Action, room: Room) -> Outcome:
    return Outcome(
        success=False,
        event="unknown",
        message="That action is not possible here. Try to examine, take, use or open something, or enter a code.",
    )


HANDLERS: dict[Intent, Callable[[GameState, Action, Room], Outcome]] = {
    Intent.LOOK: _look,
    Intent.EXAMINE: _examine,
    Intent.TAKE: _take,
    Intent.USE: _use,
    Intent.OPEN: _open,
    Intent.ENTER_CODE: _enter_code,
    Intent.INVENTORY: _inventory,
    Intent.HINT: _hint,
    Intent.HELP: _help,
    Intent.META: _meta,
    Intent.UNKNOWN: _unknown,
}
