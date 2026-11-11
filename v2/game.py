"""V2 turn pipeline: parse -> validate & apply (engine) -> narrate -> check for leaks.

    player text --LLM parser--> Action --engine--> new GameState + Outcome
                                                         |
                                     LLM narrator <------+  (facts the player may know)
                                          |
                                   secrets guard --> reply

Only the engine changes the game state. If the parser is unavailable the turn
is not played at all; if the narrator is unavailable or leaks a code, the reply
falls back to the engine's own plain description, so the game stays correct
even without the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.llm_client import LLMClient, LLMError
from common.room import Room, default_room
from v2.engine.actions import Action
from v2.engine.rules import Outcome, resolve
from v2.engine.state import GameState, new_game
from v2.llm.narrator import NarrationTurn, build_turn_prompt, fallback_reply, narrate
from v2.llm.parser import parse
from v2.secrets_guard import find_leaks, protected_codes

LEAK_RETRY_INSTRUCTION = (
    "Your previous reply revealed a secret code. Rewrite it without stating, guessing or combining any code "
    "or sequence of digits."
)


@dataclass(frozen=True)
class TurnResult:
    state: GameState
    reply: str
    action: Action | None = None
    outcome: Outcome | None = None
    narration: NarrationTurn | None = None  # append to the history for the next turn
    used_fallback: bool = False  # reply came from the engine, not the narrator
    guard_triggered: bool = False  # the narrator's first reply leaked a code
    error: str | None = None


class V2Game:
    HISTORY_TURNS = 3  # past exchanges given to the narrator for continuity

    def __init__(self, llm: LLMClient, room: Room | None = None):
        self.llm = llm
        self.room = room or default_room()

    def new_game(self) -> GameState:
        return new_game(self.room)

    def process_turn(self, state: GameState, player_text: str, history: list[NarrationTurn]) -> TurnResult:
        try:
            action = parse(player_text, self.room, state, self.llm)
        except LLMError as exc:
            return TurnResult(
                state=state,
                reply=f"The Game Master could not process that right now ({exc}). Nothing has changed.",
                error=str(exc),
            )

        new_state, outcome = resolve(state, action, self.room)
        turn_prompt = build_turn_prompt(player_text, action, outcome, new_state, self.room)
        codes = protected_codes(self.room, new_state, player_text)
        recent = history[-self.HISTORY_TURNS :]

        guard_triggered = used_fallback = False
        try:
            reply = narrate(self.llm, self.room, turn_prompt, recent)
            if find_leaks(reply, codes):
                guard_triggered = True
                reply = narrate(self.llm, self.room, turn_prompt, recent, LEAK_RETRY_INSTRUCTION)
                if find_leaks(reply, codes):
                    reply, used_fallback = fallback_reply(outcome), True
        except LLMError:
            reply, used_fallback = fallback_reply(outcome), True

        return TurnResult(
            state=new_state,
            reply=reply,
            action=action,
            outcome=outcome,
            narration=NarrationTurn(turn_prompt, reply),
            used_fallback=used_fallback,
            guard_triggered=guard_triggered,
        )
