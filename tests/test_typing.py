"""The latest reply is typed out once; the text itself is never changed by it."""

import pytest
from streamlit.testing.v1 import AppTest

from common.config import PROJECT_ROOT
from common.llm_client import LLMError
from common.ui import typing_chunks

TIMEOUT = 30
TWO_PARAGRAPHS = "The radio hums.\n\nA voice crackles:  'Attention, residents.'"


@pytest.mark.parametrize(
    "text",
    [TWO_PARAGRAPHS, "one", "  leading and trailing  ", "tabs\tand\nnewlines\n", "", "   "],
)
def test_chunks_join_back_to_the_exact_text(text):
    assert "".join(typing_chunks(text)) == text


def test_chunks_are_words_with_their_spacing():
    assert typing_chunks("Good morning, resident!") == ["Good ", "morning, ", "resident!"]


@pytest.fixture
def fake(app_llm):
    return app_llm


def run_app(version: str) -> AppTest:
    return AppTest.from_file(str(PROJECT_ROOT / version / "app.py"), default_timeout=TIMEOUT).run()


def chat_texts(at: AppTest) -> list[str]:
    return [m.markdown[0].value for m in at.chat_message]


def test_v1_reply_is_shown_in_full_and_typed_only_once(fake):
    fake.texts = [TWO_PARAGRAPHS]
    at = run_app("v1")
    at.chat_input[0].set_value("use the tube on the radio").run()
    assert chat_texts(at)[-1] == TWO_PARAGRAPHS
    assert "type_last" not in at.session_state  # consumed by the run that typed it

    at.run()  # any later rerun, e.g. opening the debug panel
    assert chat_texts(at)[-1] == TWO_PARAGRAPHS and "type_last" not in at.session_state


def test_v2_reply_is_shown_in_full_and_typed_only_once(fake):
    fake.json_objects = [{"intent": "examine", "target": "desk"}]
    fake.texts = [TWO_PARAGRAPHS]
    at = run_app("v2")
    at.chat_input[0].set_value("examine the desk").run()
    assert chat_texts(at)[-2:] == ["examine the desk", TWO_PARAGRAPHS]
    assert "type_last" not in at.session_state

    at.run()
    assert chat_texts(at)[-1] == TWO_PARAGRAPHS and "type_last" not in at.session_state


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_a_failed_turn_types_nothing(version, fake):
    fake.texts = [LLMError("quota")]
    fake.json_objects = [LLMError("quota")]
    at = run_app(version)
    at.chat_input[0].set_value("examine the desk").run()
    assert at.error and "type_last" not in at.session_state
