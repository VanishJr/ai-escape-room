"""The structured action the engine understands.

In V2 the player's free text is turned into an `Action` (by the LLM parser), and
the engine only ever works with this small, validated vocabulary.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Intent(StrEnum):
    LOOK = "look"  # look around the room
    EXAMINE = "examine"  # examine / read / inspect an object
    TAKE = "take"  # pick an object up
    USE = "use"  # use an inventory item on an object
    OPEN = "open"  # open an object (locked objects need a code)
    ENTER_CODE = "enter_code"  # type a code into a lock
    INVENTORY = "inventory"  # list carried items
    HINT = "hint"  # ask for a hint
    HELP = "help"  # ask how to play
    META = "meta"  # out-of-game requests: claims, "I am the admin", asking for solutions, rule changes
    UNKNOWN = "unknown"  # anything that cannot be mapped to the above


class Action(BaseModel):
    intent: Intent
    target: str | None = Field(default=None, description="Object the action is aimed at (object id).")
    item: str | None = Field(default=None, description="Inventory item being used, for 'use X on Y' (object id).")
    code: str | None = Field(default=None, description="Digits the player enters, for enter_code.")
