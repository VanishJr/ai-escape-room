"""Thin wrapper around the Gemini API, shared by V1 and V2.

Every SDK call lives here, so the game code only deals with plain strings and
Pydantic models. Tests replace `GeminiClient` with a fake that implements the
same `LLMClient` protocol, so no test needs network access or an API key.

Free-tier limits shape the retry logic:
- per-minute limit (429)  -> wait the delay the server asks for, then retry;
- per-day limit (429)     -> skip that model until its quota resets and fall back
                             to the next model in the configured chain;
- server errors (5xx)     -> short exponential backoff.
"""

from __future__ import annotations

import re
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
    """The LLM could not produce a usable answer (missing key, quota, outage, bad output)."""


class LLMClient(Protocol):
    def generate_text(self, system: str, turns: list[ChatTurn]) -> str: ...

    def generate_json(self, system: str, prompt: str, schema: type[SchemaT]) -> SchemaT: ...


class _DailyQuotaExceeded(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after


class GeminiClient:
    """`LLMClient` backed by the Google Gemini API (free tier)."""

    SERVER_ERRORS = {500, 502, 503, 504}

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        max_retries: int = 2,
        base_delay: float = 2.0,
        max_wait: float = 60.0,
    ):
        self.settings = settings or get_settings()
        if not self.settings.api_key:
            raise LLMError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        # SDK-level retries are disabled: they would retry a daily-quota error for
        # a minute before giving up. The retry policy below is quota-aware.
        self._client = genai.Client(
            api_key=self.settings.api_key,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
        )
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_wait = max_wait
        self._unavailable_until: dict[str, float] = {}  # model -> monotonic time its quota resets
        self.last_model: str | None = None  # which model produced the latest answer

    # ---- public API ----------------------------------------------------
    def generate_text(self, system: str, turns: list[ChatTurn]) -> str:
        """Continue a conversation. `turns` is the full history, ending with the player's message."""
        contents = [types.Content(role=t.role, parts=[types.Part.from_text(text=t.text)]) for t in turns]
        config = types.GenerateContentConfig(system_instruction=system)
        response = self._generate(self.settings.models, contents, config)
        text = (response.text or "").strip()
        if not text:
            raise LLMError("The model returned an empty response.")
        return text

    def generate_json(self, system: str, prompt: str, schema: type[SchemaT]) -> SchemaT:
        """Single request whose answer must be JSON matching `schema` (Gemini structured output)."""
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
        )
        response = self._generate(self.settings.parser_models, prompt, config)
        try:
            return schema.model_validate_json(response.text or "")
        except ValidationError as exc:
            raise LLMError(f"The model returned JSON that does not match {schema.__name__}.") from exc

    # ---- retry and fallback --------------------------------------------
    def _generate(self, models: tuple[str, ...], contents, config: types.GenerateContentConfig):
        now = time.monotonic()
        for model in models:
            if self._unavailable_until.get(model, 0) > now:
                continue
            try:
                response = self._call_with_retries(model, contents, config)
            except _DailyQuotaExceeded as exc:
                self._unavailable_until[model] = time.monotonic() + exc.retry_after
                continue
            self.last_model = model
            return response
        raise LLMError(
            f"The daily free-tier quota is used up for: {', '.join(models)}. "
            "It resets at midnight Pacific time; you can also add another model to GEMINI_MODEL in .env."
        )

    def _call_with_retries(self, model: str, contents, config: types.GenerateContentConfig):
        for attempt in range(self.max_retries + 1):
            try:
                return self._client.models.generate_content(model=model, contents=contents, config=config)
            except errors.APIError as exc:
                last_attempt = attempt == self.max_retries
                if exc.code == 429:
                    if _is_daily_quota(exc):
                        raise _DailyQuotaExceeded(_retry_delay(exc) or 3600.0) from exc
                    delay = _retry_delay(exc) or self.base_delay * 2**attempt
                    if last_attempt or delay > self.max_wait:
                        raise LLMError("Gemini rate limit reached. Wait a minute and try again.") from exc
                elif exc.code in self.SERVER_ERRORS and not last_attempt:
                    delay = self.base_delay * 2**attempt
                else:
                    raise LLMError(f"Gemini API error {exc.code}: {exc.message}") from exc
                time.sleep(delay)
        raise AssertionError("unreachable")


def _is_daily_quota(exc: errors.APIError) -> bool:
    return "PerDay" in str(exc.details)


def _retry_delay(exc: errors.APIError) -> float | None:
    """Seconds the server asks us to wait (google.rpc.RetryInfo), if present."""
    match = re.search(r"'retryDelay': '(\d+(?:\.\d+)?)s'", str(exc.details))
    return float(match.group(1)) if match else None
