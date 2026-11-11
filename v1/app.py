"""V1 Streamlit app: the LLM runs the whole game.

Run:  uv run streamlit run v1/app.py
"""

from __future__ import annotations

import streamlit as st

from common.llm_client import ChatTurn, GeminiClient, LLMError
from common.room import default_room
from common.ui import render_header, render_instructions, render_opening, render_turn
from v1.game import V1Game

st.set_page_config(page_title="AI Escape Room · V1", page_icon="🗝️")
room = default_room()


@st.cache_resource
def get_game() -> V1Game:
    return V1Game(GeminiClient(), room)


def reset_game() -> None:
    # One dict, replaced as a whole after each turn, so a rerun can never see half a turn.
    st.session_state.v1 = {"history": [], "escaped": False}  # the chat history is V1's only game state
    st.session_state.celebrate = False


def queue_retry(text: str) -> None:
    st.session_state.retry_text = text


if "v1" not in st.session_state:
    reset_game()
game_state = st.session_state.v1

with st.sidebar:
    render_instructions(room)
    st.button("Restart game", on_click=reset_game, type="primary", width="stretch")
    st.divider()
    st.caption("Version 1: the LLM Game Master runs the whole game from its prompt and the chat history.")

render_header(room, "Version 1 — Interactive")
render_opening(room)
for turn in game_state["history"]:
    render_turn(turn.role, turn.text)

if game_state["escaped"]:
    st.success("You escaped the library! Press **Restart game** to play again.")
    if st.session_state.celebrate:
        st.balloons()
        st.session_state.celebrate = False

typed = st.chat_input("What do you do?", disabled=game_state["escaped"])
player_text = typed or st.session_state.pop("retry_text", None)
if player_text:
    render_turn("user", player_text)
    try:
        with st.spinner("The Game Master is thinking..."):
            reply = get_game().process_turn(game_state["history"], player_text)
    except LLMError as exc:
        st.error(f"The Game Master is unavailable: {exc}")
        st.button("Retry", on_click=queue_retry, args=(player_text,))
        st.stop()
    history = [*game_state["history"], ChatTurn("user", player_text), ChatTurn("model", reply.text)]
    st.session_state.v1 = {"history": history, "escaped": reply.escaped}
    st.session_state.celebrate = reply.escaped
    st.rerun()
