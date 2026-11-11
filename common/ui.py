"""Streamlit pieces shared by the V1 and V2 apps."""

from __future__ import annotations

import streamlit as st

from common.room import Room


def render_header(room: Room, version: str) -> None:
    st.title(room.title)
    st.caption(f"AI Escape Room · {version}")


def render_instructions(room: Room) -> None:
    st.subheader("How to play")
    st.markdown("\n".join(f"- {line}" for line in room.instructions))


def render_opening(room: Room) -> None:
    with st.chat_message("assistant"):
        st.markdown(f"{room.intro}\n\n{room.description}")


def render_turn(role: str, text: str) -> None:
    with st.chat_message("user" if role == "user" else "assistant"):
        st.markdown(text)
