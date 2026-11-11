from fakes import FakeLLM

from common.llm_client import ChatTurn
from v1.game import V1Game
from v1.prompts import ESCAPE_MARKER


def test_escape_marker_is_detected_and_hidden_from_player():
    game = V1Game(FakeLLM(texts=[f"The door swings open. You are free! {ESCAPE_MARKER}"]))
    reply = game.process_turn([], "enter 1847 on the door")
    assert reply.escaped
    assert ESCAPE_MARKER not in reply.text


def test_normal_reply_is_not_an_escape():
    game = V1Game(FakeLLM(texts=["The bookshelf is dusty."]))
    assert not game.process_turn([], "examine the bookshelf").escaped


def test_full_history_is_sent_every_turn():
    llm = FakeLLM(texts=["ok"])
    history = [ChatTurn("user", "examine the desk"), ChatTurn("model", "You see a note.")]
    V1Game(llm).process_turn(history, "read the note")
    _, turns = llm.text_calls[0]
    assert [t.text for t in turns] == ["examine the desk", "You see a note.", "read the note"]


def test_v1_prompt_contains_every_secret():
    """V1's core weakness: the LLM holds all solutions, so it can be talked into revealing them."""
    prompt = V1Game(FakeLLM()).system_prompt
    assert "7294" in prompt and "1847" in prompt
