"""Smoke tests of both Streamlit apps, with the Gemini client replaced by a fake."""

import pytest
import streamlit as st
from fakes import FakeLLM
from streamlit.testing.v1 import AppTest

import common.llm_client
from common.config import PROJECT_ROOT
from common.llm_client import LLMError

TIMEOUT = 30


@pytest.fixture
def fake_gemini(monkeypatch):
    """Make `GeminiClient()` in the apps return one shared scripted FakeLLM."""
    fake = FakeLLM()
    monkeypatch.setattr(common.llm_client, "GeminiClient", lambda *args, **kwargs: fake)
    st.cache_resource.clear()
    yield fake
    st.cache_resource.clear()


def chat_texts(at: AppTest) -> list[str]:
    return [m.markdown[0].value for m in at.chat_message]


def test_v1_plays_a_turn_and_detects_escape(fake_gemini):
    fake_gemini.texts = ["You see a dusty desk.", "The door opens! [ESCAPED]"]
    at = AppTest.from_file(str(PROJECT_ROOT / "v1" / "app.py"), default_timeout=TIMEOUT).run()
    assert not at.exception and at.title[0].value == "The Locked Library"

    at.chat_input[0].set_value("examine the desk").run()
    assert chat_texts(at)[-1] == "You see a dusty desk."

    at.chat_input[0].set_value("enter 1847 on the door").run()
    assert "[ESCAPED]" not in chat_texts(at)[-1]
    assert at.success and at.chat_input[0].disabled


def test_v1_error_keeps_history_and_offers_retry(fake_gemini):
    fake_gemini.texts = [LLMError("quota")]
    at = AppTest.from_file(str(PROJECT_ROOT / "v1" / "app.py"), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value("examine the desk").run()
    assert at.error and any(b.label == "Retry" for b in at.button)
    assert at.session_state.v1["history"] == []


def test_v2_plays_a_turn_without_spoilers_in_debug_panel(fake_gemini):
    fake_gemini.json_objects = [{"intent": "examine", "target": "bookshelf"}]
    fake_gemini.texts = ["Four clean volumes stand out."]
    at = AppTest.from_file(str(PROJECT_ROOT / "v2" / "app.py"), default_timeout=TIMEOUT).run()
    assert not at.exception

    at.chat_input[0].set_value("examine the bookshelf").run()
    assert chat_texts(at)[-1] == "Four clean volumes stand out."
    state = at.session_state.v2["state"]
    assert state.turn == 1 and state.objects["volumes"].visible
    debug_state = at.json[-1].value
    assert "uv_flashlight" not in debug_state and "note" not in debug_state  # hidden objects stay hidden


def test_v2_parser_error_changes_nothing(fake_gemini):
    fake_gemini.json_objects = [LLMError("busy")]
    at = AppTest.from_file(str(PROJECT_ROOT / "v2" / "app.py"), default_timeout=TIMEOUT).run()
    at.chat_input[0].set_value("examine the desk").run()
    assert at.error and at.session_state.v2["state"].turn == 0
