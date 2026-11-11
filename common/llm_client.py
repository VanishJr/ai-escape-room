"""Thin wrapper around the Gemini API, shared by V1 and V2.

Every SDK call lives here, so the game code only deals with plain strings and
Pydantic models. Tests replace `GeminiClient` with a fake that implements the
same `LLMClient` protocol, so no test needs network access or an API key.

The free tier shapes the error handling. Models are tried in the order of the
configured chain; a model that cannot answer right now is skipped for a while
and the next one is used:
- daily quota used up (429, "PerDay")   -> skip until the quota resets (>= 1 hour);
- per-minute limit (429)                 -> skip for the delay the server asks for;
- model unknown or forbidden (404/403)   -> skip for an hour;
- overload (5xx, 408) or network timeout -> short backoff, then skip for 2 minutes.
The client never sleeps for long inside a request: a player should not wait
minutes for one turn. The evaluation script adds its own patient waiting on top.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

import httpx2
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


class QuotaExhausted(LLMError):
    """Every configured model has used up its daily free-tier quota."""


class LLMClient(Protocol):
    def generate_text(self, system: str, turns: list[ChatTurn]) -> str: ...

    def generate_json(self, system: str, prompt: str, schema: type[SchemaT]) -> SchemaT: ...


class _ModelUnavailable(Exception):
    def __init__(self, reason: str, retry_after: float):
        self.reason = reason  # "quota", "rate_limited", "unavailable" or "overloaded"
        self.retry_after = retry_after


class GeminiClient:
    """`LLMClient` backed by the Google Gemini API (free tier)."""

    RETRYABLE_CODES = {408, 500, 502, 503, 504}
    NETWORK_ERRORS = (httpx2.TransportError, TimeoutError, ConnectionError)
    OVERLOAD_COOLDOWN = 120.0  # seconds to skip a model that keeps failing
    LONG_COOLDOWN = 3600.0

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        max_retries: int = 1,
        base_delay: float = 2.0,
        timeout_s: float = 30.0,
    ):
        self.settings = settings or get_settings()
        if not self.settings.api_key:
            raise LLMError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env, add your key and restart the app."
            )
        # SDK-level retries are disabled: they would retry a daily-quota error for
        # a minute before giving up. The policy below is quota-aware instead.
        self._client = genai.Client(
            api_key=self.settings.api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=1),
                timeout=int(timeout_s * 1000),  # milliseconds
            ),
        )
        self.max_retries = max_retries
        self.base_delay = base_delay
        # Shared by all Streamlit sessions (one key = one quota): model -> (skip until, reason)
        self._unavailable: dict[str, tuple[float, str]] = {}
        self._local = threading.local()  # per-thread, i.e. per Streamlit session

    @property
    def last_model(self) -> str | None:
        """Model that produced the latest answer in this thread."""
        return getattr(self._local, "model", None)

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

    # ---- fallback across models ------------------------------------------
    def _generate(self, models: tuple[str, ...], contents, config: types.GenerateContentConfig):
        reasons: set[str] = set()
        for model in models:
            until, reason = self._unavailable.get(model, (0.0, ""))
            if until > time.monotonic():
                reasons.add(reason)
                continue
            try:
                response = self._call_with_retries(model, contents, config)
            except _ModelUnavailable as exc:
                self._unavailable[model] = (time.monotonic() + exc.retry_after, exc.reason)
                reasons.add(exc.reason)
                continue
            self._local.model = model
            return response
        if reasons == {"quota"}:
            raise QuotaExhausted(
                f"The daily free-tier quota is used up for: {', '.join(models)}. "
                "It resets at midnight Pacific time; you can also add another model to GEMINI_MODEL in .env."
            )
        raise LLMError(
            f"Gemini is busy or out of quota right now ({', '.join(sorted(reasons))}). "
            "Please try again in a minute."
        )

    def _call_with_retries(self, model: str, contents, config: types.GenerateContentConfig):
        for attempt in range(self.max_retries + 1):
            last_attempt = attempt == self.max_retries
            try:
                return self._client.models.generate_content(model=model, contents=contents, config=config)
            except errors.APIError as exc:
                if exc.code == 429:
                    delay = _retry_delay(exc)
                    if _is_daily_quota(exc):
                        raise _ModelUnavailable("quota", max(delay or 0.0, self.LONG_COOLDOWN)) from exc
                    raise _ModelUnavailable("rate_limited", delay or 30.0) from exc
                if exc.code in (403, 404):
                    raise _ModelUnavailable("unavailable", self.LONG_COOLDOWN) from exc
                if exc.code not in self.RETRYABLE_CODES:
                    raise LLMError(f"Gemini API error {exc.code}: {exc.message}") from exc
                if last_attempt:
                    raise _ModelUnavailable("overloaded", self.OVERLOAD_COOLDOWN) from exc
            except self.NETWORK_ERRORS as exc:
                if last_attempt:
                    raise _ModelUnavailable("overloaded", self.OVERLOAD_COOLDOWN) from exc
            time.sleep(self.base_delay * 2**attempt)
        raise AssertionError("unreachable")


def _is_daily_quota(exc: errors.APIError) -> bool:
    return "PerDay" in str(exc.details)


def _retry_delay(exc: errors.APIError) -> float | None:
    """Seconds the server asks us to wait (google.rpc.RetryInfo), if present."""
    match = re.search(r"'retryDelay': '(\d+(?:\.\d+)?)s'", str(exc.details))
    return float(match.group(1)) if match else None
