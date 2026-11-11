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
    st.session_state.history = []  # list[ChatTurn]: the only game state in V1
    st.session_state.escaped = False
    st.session_state.celebrate = False


if "history" not in st.session_state:
    reset_game()

with st.sidebar:
    render_instructions(room)
    st.button("Restart game", on_click=reset_game, type="primary", width="stretch")
    st.divider()
    st.caption("Version 1: the LLM Game Master runs the whole game from its prompt and the chat history.")

render_header(room, "Version 1 — Interactive")
render_opening(room)
for turn in st.session_state.history:
    render_turn(turn.role, turn.text)

if st.session_state.escaped:
    st.success("You escaped the library! Press **Restart game** to play again.")
    if st.session_state.celebrate:
        st.balloons()
        st.session_state.celebrate = False

player_text = st.chat_input("What do you do?", disabled=st.session_state.escaped)
if player_text:
    render_turn("user", player_text)
    try:
        with st.spinner("The Game Master is thinking..."):
            reply = get_game().process_turn(st.session_state.history, player_text)
    except LLMError as exc:
        st.error(f"The Game Master is unavailable: {exc}")
        st.stop()
    st.session_state.history += [ChatTurn("user", player_text), ChatTurn("model", reply.text)]
    if reply.escaped:
        st.session_state.escaped = True
        st.session_state.celebrate = True
    st.rerun()
