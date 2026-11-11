"""Retry and fallback policy of GeminiClient, tested without network access."""

import time
from types import SimpleNamespace

import httpx2
import pytest
from google.genai import errors
from pydantic import BaseModel

from common.config import Settings
from common.llm_client import ChatTurn, GeminiClient, LLMError, QuotaExhausted

HELLO = [ChatTurn("user", "hello")]


def quota_error(per_day: bool, retry_delay: str = "1s") -> errors.ClientError:
    quota_id = "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if per_day else "GenerateRequestsPerMinute"
    return errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": "quota exceeded",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay},
                ],
            }
        },
    )


def api_error(code: int) -> errors.APIError:
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": f"error {code}", "status": "X"}})


class FakeModels:
    """Replays a scripted list of outcomes per model: an exception to raise or a text to return."""

    def __init__(self, script: dict[str, list]):
        self.script = script
        self.calls: list[str] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        outcome = self.script[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(text=outcome)


def make_client(script: dict[str, list], models=("main", "backup")) -> tuple[GeminiClient, FakeModels]:
    settings = Settings(api_key="test", models=models, parser_models=("parser",))
    client = GeminiClient(settings, max_retries=1, base_delay=0)
    fake = FakeModels(script)
    client._client = SimpleNamespace(models=fake)
    return client, fake


def test_daily_quota_falls_back_and_skips_the_model_for_at_least_an_hour():
    # Real daily-quota errors can carry a short retryDelay; the model must still be skipped.
    client, fake = make_client({"main": [quota_error(per_day=True, retry_delay="41s")], "backup": ["hi", "again"]})
    assert client.generate_text("sys", HELLO) == "hi"
    assert client.last_model == "backup"
    assert client._unavailable["main"][0] - time.monotonic() > 3000
    assert client.generate_text("sys", HELLO) == "again"
    assert fake.calls == ["main", "backup", "backup"]


def test_per_minute_limit_moves_on_without_sleeping():
    client, fake = make_client({"main": [quota_error(per_day=False, retry_delay="40s")], "backup": ["ok"]})
    started = time.monotonic()
    assert client.generate_text("sys", HELLO) == "ok"
    assert time.monotonic() - started < 1 and fake.calls == ["main", "backup"]


def test_all_models_out_of_daily_quota_raises_quota_exhausted_every_time():
    client, _ = make_client({"main": [quota_error(per_day=True)], "backup": [quota_error(per_day=True)]})
    for _ in range(2):  # the second call skips both models but keeps the reason
        with pytest.raises(QuotaExhausted):
            client.generate_text("sys", HELLO)


def test_timeouts_and_overload_fall_back_to_next_model():
    client, fake = make_client({"main": [httpx2.ReadTimeout("slow"), api_error(503)], "backup": ["fine"]})
    assert client.generate_text("sys", HELLO) == "fine"
    assert fake.calls == ["main", "main", "backup"]


def test_unknown_model_does_not_break_the_chain():
    client, _ = make_client({"main": [api_error(404)], "backup": ["fine"]})
    assert client.generate_text("sys", HELLO) == "fine"


def test_single_overloaded_model_raises_llm_error():
    client, _ = make_client({"main": [api_error(503)] * 2}, models=("main",))
    with pytest.raises(LLMError, match="busy"):
        client.generate_text("sys", HELLO)


def test_bad_request_is_not_retried():
    client, fake = make_client({"main": [api_error(400)]})
    with pytest.raises(LLMError, match="400"):
        client.generate_text("sys", HELLO)
    assert fake.calls == ["main"]


def test_programming_errors_are_not_mistaken_for_overload():
    client, fake = make_client({"main": [TypeError("bug in our code")]})
    with pytest.raises(TypeError):
        client.generate_text("sys", HELLO)
    assert "main" not in client._unavailable


def test_invalid_json_becomes_llm_error():
    class Answer(BaseModel):
        ok: bool

    client, _ = make_client({"parser": ["not json"]})
    with pytest.raises(LLMError, match="does not match"):
        client.generate_json("sys", "prompt", Answer)
