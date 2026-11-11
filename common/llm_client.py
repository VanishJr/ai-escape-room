"""Thin wrapper around the Gemini API, shared by V1 and V2.

Every SDK call lives here, so the game code only deals with plain strings and
Pydantic models. Tests replace `GeminiClient` with a fake that implements the
same `LLMClient` protocol, so no test needs network access or an API key.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from common.config import Settings, get_settings

Role = Literal["user", "model"]
SchemaT = TypeVar("SchemaT", bound=BaseModel)


@dataclass(frozen=True)
class ChatTurn:
    role: Role  # "user" = player, "model" = game master
    text: str


class LLMError(RuntimeError):
    """The LLM could not produce a usable answer (missing key, rate limit, outage, bad output)."""


class LLMClient(Protocol):
    def generate_text(self, system: str, turns: list[ChatTurn], *, model: str | None = None) -> str: ...

    def generate_json(
        self, system: str, prompt: str, schema: type[SchemaT], *, model: str | None = None
    ) -> SchemaT: ...


class GeminiClient:
    """`LLMClient` backed by the Google Gemini API (free tier)."""

    RETRYABLE_CODES = {429, 500, 502, 503, 504}

    def __init__(self, settings: Settings | None = None, max_retries: int = 3, base_delay: float = 2.0):
        self.settings = settings or get_settings()
        if not self.settings.api_key:
            raise LLMError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        self._client = genai.Client(api_key=self.settings.api_key)
        self.max_retries = max_retries
        self.base_delay = base_delay

    def generate_text(self, system: str, turns: list[ChatTurn], *, model: str | None = None) -> str:
        """Continue a conversation. `turns` is the full history, ending with the player's message."""
        contents = [types.Content(role=t.role, parts=[types.Part.from_text(text=t.text)]) for t in turns]
        config = types.GenerateContentConfig(system_instruction=system)
        response = self._generate(model or self.settings.model, contents, config)
        text = (response.text or "").strip()
        if not text:
            raise LLMError("The model returned an empty response.")
        return text

    def generate_json(
        self, system: str, prompt: str, schema: type[SchemaT], *, model: str | None = None
    ) -> SchemaT:
        """Single request whose answer must be JSON matching `schema` (Gemini structured output)."""
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
        )
        response = self._generate(model or self.settings.parser_model, prompt, config)
        try:
            return schema.model_validate_json(response.text or "")
        except ValidationError as exc:
            raise LLMError(f"The model returned JSON that does not match {schema.__name__}.") from exc

    def _generate(self, model: str, contents, config: types.GenerateContentConfig):
        for attempt in range(self.max_retries + 1):
            try:
                return self._client.models.generate_content(model=model, contents=contents, config=config)
            except errors.APIError as exc:
                if exc.code in self.RETRYABLE_CODES and attempt < self.max_retries:
                    time.sleep(self.base_delay * 2**attempt)
                    continue
                if exc.code == 429:
                    raise LLMError("Gemini rate limit reached. Wait a minute and try again.") from exc
                raise LLMError(f"Gemini API error {exc.code}: {exc.message}") from exc
        raise AssertionError("unreachable")
