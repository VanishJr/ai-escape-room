"""V2 Streamlit app: Python runs the game, the LLM parses input and narrates.

Run:  uv run streamlit run v2/app.py
"""

from __future__ import annotations

import streamlit as st

from common.llm_client import GeminiClient, LLMError
from common.room import active_room
from common.ui import render_header, render_instructions, render_opening, render_turn
from v2.engine.state import GameState, new_game
from v2.game import TurnResult, V2Game

st.set_page_config(page_title="AI Escape Room · V2", page_icon="🗝️")
room = active_room()


@st.cache_resource
def get_llm() -> GeminiClient:
    return GeminiClient()


def reset_game() -> None:
    # One dict, replaced as a whole after each turn, so a rerun can never see half a turn.
    st.session_state.v2 = {
        "state": new_game(room),  # the authoritative game state
        "chat": [],  # (role, text) pairs shown to the player
        "narration": [],  # past narrator exchanges
        "last_turn": None,  # TurnResult of the latest turn, for the debug panel
    }
    st.session_state.celebrate = False


def queue_retry(text: str) -> None:
    st.session_state.retry_text = text


if "v2" not in st.session_state:
    reset_game()
session = st.session_state.v2
state: GameState = session["state"]


def render_progress() -> None:
    st.subheader("Your progress")
    items = [room.obj(i).name for i in state.inventory]
    st.markdown(f"**Inventory:** {', '.join(items) if items else 'empty'}")
    solved = [p.name for p in room.scoring_puzzles if p.id in state.solved]
    st.markdown(f"**Puzzles solved:** {len(solved)} / {len(room.scoring_puzzles)}")
    for name in solved:
        st.markdown(f"- ✅ {name}")
    st.caption(f"Turn {state.turn}")


def render_debug(turn: TurnResult | None) -> None:
    with st.expander("Debug: what the engine saw"):
        if turn and turn.action:
            st.markdown("**Parsed action**")
            st.json(turn.action.model_dump(mode="json"))
            st.markdown(f"**Engine result:** `{turn.outcome.event}` ({'success' if turn.outcome.success else 'refused'})")
            st.markdown(
                f"Parser: `{turn.parser_model or 'no LLM needed'}` · narrator: `{turn.narrator_model or 'engine text'}`"
                f" · leak guard triggered: `{turn.guard_triggered}`"
            )
        st.markdown("**Game state** (objects you have discovered)")
        data = state.model_dump(mode="json", exclude={"log"})
        data["objects"] = {k: v for k, v in data["objects"].items() if v["visible"]}  # no spoilers
        st.json(data, expanded=False)


with st.sidebar:
    render_instructions(room)
    st.button("Restart game", on_click=reset_game, type="primary", width="stretch")
    st.divider()
    render_progress()
    st.divider()
    render_debug(session["last_turn"])
    st.caption("Version 2: Python validates every action and owns the game state; the LLM only narrates.")

render_header(room, "Version 2 — Reliable")
render_opening(room)
for role, text in session["chat"]:
    render_turn(role, text)

if state.escaped:
    st.success(f"{room.victory} in {state.turn} turns! Press **Restart game** to play again.")
    if st.session_state.celebrate:
        st.balloons()
        st.session_state.celebrate = False

typed = st.chat_input("What do you do?", disabled=state.escaped)
player_text = typed or st.session_state.pop("retry_text", None)
if player_text:
    render_turn("user", player_text)
    try:
        with st.spinner("The Game Master is thinking..."):
            result = V2Game(get_llm(), room).process_turn(state, player_text, session["narration"])
    except LLMError as exc:  # e.g. missing API key
        result = None
        error = str(exc)
    else:
        error = result.error
    if error:
        st.error(f"The Game Master is unavailable: {error}")
        st.button("Retry", on_click=queue_retry, args=(player_text,))
        st.stop()
    st.session_state.v2 = {
        "state": result.state,
        "chat": [*session["chat"], ("user", player_text), ("assistant", result.reply)],
        "narration": [*session["narration"], result.narration],
        "last_turn": result,
    }
    st.session_state.celebrate = result.state.escaped
    st.rerun()
