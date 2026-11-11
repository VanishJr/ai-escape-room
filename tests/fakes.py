"""Scripted stand-in for GeminiClient (implements the LLMClient protocol)."""

from __future__ import annotations

from pydantic import BaseModel

from common.llm_client import ChatTurn


class FakeLLM:
    """Returns pre-scripted answers in order and records every request."""

    def __init__(self, texts: list = (), json_objects: list = ()):
        self.texts = list(texts)
        self.json_objects = list(json_objects)
        self.text_calls: list[tuple[str, list[ChatTurn]]] = []
        self.json_calls: list[tuple[str, str]] = []

    def generate_text(self, system: str, turns: list[ChatTurn]) -> str:
        self.text_calls.append((system, list(turns)))
        return _next(self.texts)

    def generate_json(self, system: str, prompt: str, schema: type[BaseModel]):
        self.json_calls.append((system, prompt))
        out = _next(self.json_objects)
        return schema.model_validate(out) if isinstance(out, dict) else out


def _next(queue: list):
    out = queue.pop(0)
    if isinstance(out, Exception):
        raise out
    return out
