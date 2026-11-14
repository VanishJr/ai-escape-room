"""The latest reply is typed out once; the text itself is never changed by it."""

import pytest
import streamlit as st
from app_helpers import chat_texts, run_app

import common.ui
from common.llm_client import LLMError
from common.ui import TYPE_LAST, TYPING_DELAY, TYPING_SECONDS, typing_chunks, typing_delay

TWO_PARAGRAPHS = "The radio hums.\n\nA voice crackles:  'Attention, residents.'"


@pytest.mark.parametrize(
    "text",
    [TWO_PARAGRAPHS, "one", "  leading and trailing  ", "tabs\tand\nnewlines\n", "", "   "],
)
def test_chunks_join_back_to_the_exact_text(text):
    assert "".join(typing_chunks(text)) == text


def test_chunks_are_words_with_their_spacing():
    assert typing_chunks("Good morning, resident!") == ["Good ", "morning, ", "resident!"]


def test_a_long_reply_is_typed_faster_instead_of_longer():
    assert typing_delay(10) == TYPING_DELAY
    assert typing_delay(2000) * 2000 == pytest.approx(TYPING_SECONDS)
    assert typing_delay(0) == TYPING_DELAY


@pytest.fixture
def typed(monkeypatch):
    """Every text that is typed out, in order. The pauses are switched off."""
    seen: list[str] = []
    write_stream = st.write_stream

    def spy(stream):
        text = "".join(stream)
        seen.append(text)
        return write_stream(iter([text]))

    monkeypatch.setattr(st, "write_stream", spy)
    monkeypatch.setattr(common.ui, "TYPING_DELAY", 0)
    return seen


def test_v1_types_each_new_reply_once_and_nothing_else(app_llm, typed):
    app_llm.texts = [TWO_PARAGRAPHS, "The door stays shut."]
    at = run_app("v1")
    assert typed == []  # the opening message is not typed

    at.chat_input[0].set_value("use the tube on the radio").run()
    assert typed == [TWO_PARAGRAPHS] and chat_texts(at)[-1] == TWO_PARAGRAPHS
    assert TYPE_LAST not in at.session_state  # consumed by the run that typed it

    at.run()  # any later rerun, such as a click in the side panel
    assert typed == [TWO_PARAGRAPHS] and chat_texts(at)[-1] == TWO_PARAGRAPHS

    at.chat_input[0].set_value("open the door").run()
    assert typed == [TWO_PARAGRAPHS, "The door stays shut."]  # not the player's lines, not old replies
    assert chat_texts(at)[-4:] == ["use the tube on the radio", TWO_PARAGRAPHS, "open the door", "The door stays shut."]


def test_v2_types_each_new_reply_once_and_nothing_else(app_llm, typed):
    app_llm.json_objects = [{"intent": "examine", "target": "desk"}, {"intent": "examine", "target": "clock"}]
    app_llm.texts = [TWO_PARAGRAPHS, "The clock has stopped."]
    at = run_app("v2")
    assert typed == []

    at.chat_input[0].set_value("examine the desk").run()
    assert typed == [TWO_PARAGRAPHS] and chat_texts(at)[-2:] == ["examine the desk", TWO_PARAGRAPHS]
    assert TYPE_LAST not in at.session_state

    at.run()
    assert typed == [TWO_PARAGRAPHS] and chat_texts(at)[-1] == TWO_PARAGRAPHS

    at.chat_input[0].set_value("examine the clock").run()
    assert typed == [TWO_PARAGRAPHS, "The clock has stopped."]


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_a_failed_turn_types_nothing(version, app_llm, typed):
    app_llm.texts = [LLMError("quota")]
    app_llm.json_objects = [LLMError("quota")]
    at = run_app(version)
    at.chat_input[0].set_value("examine the desk").run()
    assert at.error and typed == [] and TYPE_LAST not in at.session_state
