"""Streamlit pieces shared by the V1 and V2 apps, including the console look.

The look has two halves. `.streamlit/config.toml` sets the colours and fonts of
Streamlit's own widgets; `common/console.css` adds what a theme cannot express
(scan lines, the labels above chat messages, the metal side panel).
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import streamlit as st

from common.room import Room

CONSOLE_CSS = Path(__file__).with_name("console.css")


def host_name(room: Room) -> str:
    """Who the player is talking to, for sentences such as "MABEL is thinking..."."""
    return room.host.name if room.host else "The Game Master"


def host_label(room: Room) -> str:
    """The label printed above each of the host's replies."""
    return room.host.name if room.host else "GAME MASTER"


def theme_css(room: Room) -> str:
    """The console stylesheet plus the one value that depends on the room."""
    # A JSON string is also a valid CSS string. "<" is written as a CSS escape so
    # that no room data can close the surrounding <style> element.
    label = json.dumps(host_label(room), ensure_ascii=False).replace("<", "\\3c ")
    return f"{CONSOLE_CSS.read_text(encoding='utf-8')}\n:root {{ --host-label: {label}; }}\n"


def apply_theme(room: Room) -> None:
    # Streamlit applies a block that contains nothing but <style>; bare CSS would be shown as text.
    st.html(f"<style>{theme_css(room)}</style>")


def lamps_html(solved: int, total: int) -> str:
    """One indicator lamp per puzzle, lit for the solved ones."""
    lamps = "".join('<i class="on"></i>' if n < solved else "<i></i>" for n in range(total))
    return f'<div class="lamps">{lamps}</div>'


def render_header(room: Room, version: str) -> None:
    if len(room.console_bar) == 2:
        left, right = (html.escape(part) for part in room.console_bar)
        st.html(f'<div class="console-bar"><span>{left}</span><span>{right}</span></div>')
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
