"""Runtime configuration loaded from environment variables (.env in the project root)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MODELS = ("gemini-3.6-flash",)
DEFAULT_PARSER_MODELS = ("gemini-3.5-flash-lite",)
# The room the apps play when ESCAPE_ROOM is not set: a key of common.room.ROOMS.
# Not to be confused with common.room.default_room(), the reference room (the
# library) that the tests and the attack evaluation always use.
DEFAULT_ROOM = "library"


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    # Each setting is a fallback chain: the free tier has a small daily quota per
    # model, so when one model's quota runs out the client moves to the next one.
    models: tuple[str, ...]  # write the story: V1 game master, V2 narrator
    parser_models: tuple[str, ...]  # turn player text into a structured action (V2)
    room: str = DEFAULT_ROOM  # which scenario the apps load


def _model_list(var: str, default: tuple[str, ...]) -> tuple[str, ...]:
    models = tuple(m.strip() for m in (os.getenv(var) or "").split(",") if m.strip())
    return models or default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    return Settings(
        api_key=os.getenv("GEMINI_API_KEY") or None,
        models=_model_list("GEMINI_MODEL", DEFAULT_MODELS),
        parser_models=_model_list("GEMINI_PARSER_MODEL", DEFAULT_PARSER_MODELS),
        room=(os.getenv("ESCAPE_ROOM") or "").strip().lower() or DEFAULT_ROOM,
    )
