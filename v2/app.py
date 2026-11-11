"""V2 Streamlit app: Python runs the game, the LLM parses input and narrates.

Run:  uv run streamlit run v2/app.py
"""

from __future__ import annotations

import streamlit as st

from common.llm_client import GeminiClient, LLMError
from common.room import default_room
from common.ui import render_header, render_instructions, render_opening, render_turn
from v2.game import TurnResult, V2Game

st.set_page_config(page_title="AI Escape Room · V2", page_icon="🗝️")
room = default_room()


@st.cache_resource
def get_llm() -> GeminiClient:
    return GeminiClient()


def reset_game() -> None:
    st.session_state.state = V2Game(llm=None, room=room).new_game()  # type: ignore[arg-type]
    st.session_state.chat = []  # list of (role, text) shown to the player
    st.session_state.narration = []  # past narrator exchanges
    st.session_state.last_turn = None  # TurnResult of the latest turn, for the debug panel
    st.session_state.celebrate = False


if "state" not in st.session_state:
    reset_game()
state = st.session_state.state


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
                f"Narrator model: `{get_llm().last_model}` · fallback to engine text: `{turn.used_fallback}` "
                f"· leak guard triggered: `{turn.guard_triggered}`"
            )
        st.markdown("**Game state**")
        st.json(state.model_dump(mode="json", exclude={"log"}), expanded=False)


with st.sidebar:
    render_instructions(room)
    st.button("Restart game", on_click=reset_game, type="primary", width="stretch")
    st.divider()
    render_progress()
    st.divider()
    render_debug(st.session_state.last_turn)
    st.caption("Version 2: Python validates every action and owns the game state; the LLM only narrates.")

render_header(room, "Version 2 — Reliable")
render_opening(room)
for role, text in st.session_state.chat:
    render_turn(role, text)

if state.escaped:
    st.success(f"You escaped the library in {state.turn} turns! Press **Restart game** to play again.")
    if st.session_state.celebrate:
        st.balloons()
        st.session_state.celebrate = False

player_text = st.chat_input("What do you do?", disabled=state.escaped)
if player_text:
    render_turn("user", player_text)
    try:
        with st.spinner("The Game Master is thinking..."):
            result = V2Game(get_llm(), room).process_turn(state, player_text, st.session_state.narration)
    except LLMError as exc:  # e.g. missing API key
        st.error(f"The Game Master is unavailable: {exc}")
        st.stop()
    if result.error:
        st.warning(result.reply)
        st.stop()
    st.session_state.state = result.state
    st.session_state.chat += [("user", player_text), ("assistant", result.reply)]
    st.session_state.narration.append(result.narration)
    st.session_state.last_turn = result
    if result.state.escaped:
        st.session_state.celebrate = True
    st.rerun()
