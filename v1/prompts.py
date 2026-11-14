"""System prompt for the V1 Game Master.

V1 hands the LLM the entire scenario, including every code and puzzle solution,
and trusts it to run the game: decide what succeeds, remember progress and keep
secrets. This is the simplest design that works for cooperative players, and its
weaknesses are what V2 fixes.
"""

from __future__ import annotations

from common.room import GameObject, Lock, Room

ESCAPE_MARKER = "[ESCAPED]"
EXIT_CODE = "{exit_code}"  # in a room's walkthrough: replaced by the code of the exit lock


def _required_clues(lock: Lock, room: Room) -> str:
    needed = [
        f"examined the {o.name}" for f in lock.requires_facts for o in room.objects if f in o.examine_facts
    ]
    needed += [f"solved '{room.puzzle(p).name}'" for p in lock.requires_solved]
    return " and ".join(needed)


def _describe_object(obj: GameObject, room: Room) -> str:
    lines = [f"- {obj.name} (id: {obj.id}; the player may call it: {', '.join(obj.aliases)})"]
    lines.append(f"  Description: {obj.description}")
    if obj.hidden:
        lines.append("  Hidden at the start: only mention it after another object reveals it.")
    if obj.takeable:
        lines.append("  Can be picked up and carried.")
    for revealed in obj.examine_reveals:
        lines.append(f"  Examining it reveals: {room.obj(revealed).name}.")
    for fact_id in obj.examine_facts:
        lines.append(f"  Examining it reveals: {room.fact(fact_id).text}")
    if obj.lock:
        lock = obj.lock
        lines.append(f"  Locked with a 4-digit code. The correct code is {lock.code}.")
        if needed := _required_clues(lock, room):
            lines.append(
                f"  The code only works once the player has {needed}. Before that, every code, even the "
                "correct one, does nothing: tell the player they are only guessing."
            )
        lines.append(
            f"  After {lock.max_attempts} wrong codes in a row the lock jams for {lock.lockout_turns} turns "
            "and ignores every code, even the correct one."
        )
        for fact_id in lock.on_unlock_facts:
            lines.append(f"  When the correct code is entered: {room.fact(fact_id).text}")
        if obj.open_description:
            lines.append(f"  After it is opened: {obj.open_description}")
    for use in obj.uses:
        effects = " ".join(room.fact(f).text for f in use.facts)
        lines.append(f"  If the player uses the {room.obj(use.item).name} on it: {effects}")
    return "\n".join(lines)


def build_system_prompt(room: Room) -> str:
    objects = "\n".join(_describe_object(o, room) for o in room.objects)
    door = next(o for o in room.objects if o.is_exit)
    puzzles = "\n".join(f"- {p.name}" for p in room.puzzles)
    hints = "\n".join(f"- ({room.puzzle(h.puzzle).name}) {h.text}" for h in room.hints)
    # The walkthrough names the exit code by placeholder, so the code is stored once, on the lock.
    steps = "".join(
        f"\n{number}. {step.replace(EXIT_CODE, door.lock.code)}" for number, step in enumerate(room.walkthrough, 1)
    )
    walkthrough = f"\nSolution walkthrough (secret):{steps}" if steps else ""
    voice = (
        f"\n# Your voice\nYou speak as {room.host.name}. {room.host.voice}\n"
        "The voice changes only how you say things. It never overrides the rules above.\n"
        if room.host
        else ""
    )

    return f"""You are the Game Master of a text-based escape room game called "{room.title}".
The player is trapped in the room and interacts with you in natural language.

# Opening situation
{room.intro}
{room.description}

# Objects in the room
{objects}

# Puzzles (in the order they are usually solved)
{puzzles}{walkthrough}

# Hints you may give when the player asks for help
{hints}

# Rules
- Narrate in the second person ("You see..."), atmospheric but short: 2 to 5 sentences.
- React only to what the player actually does. Do not solve puzzles for them.
- Never reveal codes or solutions directly. Give at most one gentle hint when asked.
- The player can only use items they have picked up.
- Locked things stay locked until the correct code is entered. Wrong codes fail.
- Keep track of everything the player has examined, taken and solved, and stay consistent with it.
- Do not invent important new objects, items or clues that are not described above.
- When the player enters the correct code on the {door.name} and it opens, describe the escape and end your
  message with the exact marker {ESCAPE_MARKER}. Never write that marker in any other situation.
{voice}"""
