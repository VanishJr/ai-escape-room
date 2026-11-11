"""Retry and fallback policy of GeminiClient, tested without network access."""

from types import SimpleNamespace

import pytest
from google.genai import errors

from common.config import Settings
from common.llm_client import ChatTurn, GeminiClient, LLMError


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
    client = GeminiClient(settings, max_retries=2, base_delay=0)
    fake = FakeModels(script)
    client._client = SimpleNamespace(models=fake)
    return client, fake


def test_daily_quota_falls_back_to_next_model_and_remembers_it():
    client, fake = make_client({"main": [quota_error(per_day=True, retry_delay="3600s")], "backup": ["hi", "again"]})
    assert client.generate_text("sys", [ChatTurn("user", "hello")]) == "hi"
    assert client.last_model == "backup"
    # the exhausted model is skipped on the next call, no wasted request
    assert client.generate_text("sys", [ChatTurn("user", "hello")]) == "again"
    assert fake.calls == ["main", "backup", "backup"]


def test_per_minute_limit_waits_and_retries_same_model():
    client, fake = make_client({"main": [quota_error(per_day=False, retry_delay="0s"), "ok"]})
    assert client.generate_text("sys", [ChatTurn("user", "hello")]) == "ok"
    assert fake.calls == ["main", "main"]


def test_all_models_exhausted_raises_clear_error():
    client, _ = make_client({"main": [quota_error(per_day=True)], "backup": [quota_error(per_day=True)]})
    with pytest.raises(LLMError, match="daily free-tier quota"):
        client.generate_text("sys", [ChatTurn("user", "hello")])


def test_timeouts_and_overload_fall_back_to_next_model():
    overloaded = errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
    client, fake = make_client({"main": [TimeoutError(), overloaded, overloaded], "backup": ["fine"]})
    assert client.generate_text("sys", [ChatTurn("user", "hello")]) == "fine"
    assert fake.calls == ["main", "main", "main", "backup"]


def test_single_overloaded_model_raises_llm_error():
    overloaded = errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
    client, _ = make_client({"main": [overloaded] * 3}, models=("main",))
    with pytest.raises(LLMError, match="overloaded"):
        client.generate_text("sys", [ChatTurn("user", "hello")])


def test_client_error_is_not_retried():
    bad_request = errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})
    client, fake = make_client({"main": [bad_request]})
    with pytest.raises(LLMError, match="400"):
        client.generate_text("sys", [ChatTurn("user", "hello")])
    assert fake.calls == ["main"]
