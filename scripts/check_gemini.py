"""Check that the Gemini API key works and the configured models are available.

Usage:  uv run python scripts/check_gemini.py
"""

from __future__ import annotations

import sys

from google import genai
from pydantic import BaseModel

from common.config import get_settings
from common.llm_client import ChatTurn, GeminiClient, LLMError


class Ping(BaseModel):
    ok: bool
    word: str


def main() -> int:
    settings = get_settings()
    if not settings.api_key:
        print("GEMINI_API_KEY is missing. Copy .env.example to .env and add your key.")
        return 1

    available = {
        m.name.removeprefix("models/")
        for m in genai.Client(api_key=settings.api_key).models.list()
        if m.name and "generateContent" in (m.supported_actions or [])
    }
    print("Text models available to this key:")
    for name in sorted(n for n in available if n.startswith("gemini")):
        print("  ", name)

    missing = [m for m in (settings.model, settings.parser_model) if m not in available]
    if missing:
        print(f"\nConfigured model(s) not available: {missing}. Update GEMINI_MODEL / GEMINI_PARSER_MODEL in .env.")
        return 1

    client = GeminiClient(settings)
    try:
        text = client.generate_text("Answer in one short sentence.", [ChatTurn("user", "Say hello to a player.")])
        print(f"\n[{settings.model}] text call OK: {text}")
        ping = client.generate_json("Return ok=true and any one word.", "ping", Ping)
        print(f"[{settings.parser_model}] JSON call OK: {ping.model_dump()}")
    except LLMError as exc:
        print(f"\nAPI call failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
