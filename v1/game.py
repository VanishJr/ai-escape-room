"""V1 game loop: the conversation history is the only game state.

Each turn sends the full history plus the new player message to the LLM. The LLM
decides what happens and signals the escape by writing ESCAPE_MARKER.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.llm_client import ChatTurn, LLMClient
from common.room import Room, default_room
from v1.prompts import ESCAPE_MARKER, build_system_prompt


@dataclass(frozen=True)
class V1Reply:
    text: str
    escaped: bool


class V1Game:
    def __init__(self, llm: LLMClient, room: Room | None = None):
        self.llm = llm
        self.room = room or default_room()
        self.system_prompt = build_system_prompt(self.room)

    def process_turn(self, history: list[ChatTurn], player_text: str) -> V1Reply:
        """Ask the LLM for the next Game Master message. Raises LLMError if the API fails."""
        turns = [*history, ChatTurn("user", player_text)]
        raw = self.llm.generate_text(self.system_prompt, turns)
        return V1Reply(text=raw.replace(ESCAPE_MARKER, "").strip(), escaped=ESCAPE_MARKER in raw)
