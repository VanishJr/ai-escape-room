"""Runtime configuration loaded from environment variables (.env in the project root)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_PARSER_MODEL = "gemini-3.5-flash-lite"


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    model: str  # writes the story: V1 game master, V2 narrator
    parser_model: str  # turns player text into a structured action (V2)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    return Settings(
        api_key=os.getenv("GEMINI_API_KEY") or None,
        model=os.getenv("GEMINI_MODEL") or DEFAULT_MODEL,
        parser_model=os.getenv("GEMINI_PARSER_MODEL") or DEFAULT_PARSER_MODEL,
    )
