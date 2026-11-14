"""Streamlit pieces shared by the V1 and V2 apps, including the console look.

The look has two halves. `.streamlit/config.toml` sets the colours and fonts of
Streamlit's own widgets; `common/console.css` adds what a theme cannot express
(scan lines, the labels above chat messages, the metal side panel).
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

import streamlit as st

from common.room import Room

CONSOLE_CSS = Path(__file__).with_name("console.css")
CONSOLE_BACKGROUND = "#07110b"  # theme.backgroundColor in .streamlit/config.toml
TYPING_DELAY = 0.03  # seconds between words when a reply is typed out
TYPING_SECONDS = 2.5  # a long reply is typed faster, so that typing never takes longer than this
TYPE_LAST = "type_last"  # session flag: the turn that has just been played wants its reply typed out


# ---- the console look -------------------------------------------------------
def host_name(room: Room) -> str:
    """Who the player is talking to, for sentences such as "MABEL is thinking..."."""
    return room.host.name if room.host else "The Game Master"


def host_label(room: Room) -> str:
    """The label printed above each of the host's replies."""
    return room.host.name if room.host else "GAME MASTER"


def console_theme_loaded() -> bool:
    """Whether Streamlit has read this repository's `.streamlit/config.toml`.

    It looks for that file in the folder the app is started from. Started elsewhere,
    the app has Streamlit's default colours, and the stylesheet on its own would put
    dark text on a dark screen."""
    return (st.get_option("theme.backgroundColor") or "").lower() == CONSOLE_BACKGROUND


def css_string(text: str) -> str:
    """`text` as a quoted CSS string. Quotes, backslashes, control characters and "<" are
    escaped, so no room data can end the string or close the surrounding <style> element."""
    escaped = "".join(
        f"\\{ch}" if ch in '"\\' else f"\\{ord(ch):x} " if ch == "<" or ord(ch) < 0x20 or ord(ch) == 0x7F else ch
        for ch in text
    )
    return f'"{escaped}"'


@lru_cache(maxsize=1)
def _stylesheet() -> str:
    return CONSOLE_CSS.read_text(encoding="utf-8")


def theme_css(room: Room) -> str:
    """The console stylesheet plus the one value that depends on the room."""
    return f"{_stylesheet()}\n:root {{ --host-label: {css_string(host_label(room))}; }}\n"


def apply_theme(room: Room) -> None:
    if not console_theme_loaded():
        st.info(
            "The console look is switched off: its colours are in `.streamlit/config.toml`, which Streamlit "
            "reads only when the app is started from the repository root."
        )
        return
    # Streamlit applies a block that contains nothing but <style>; bare CSS would be shown as text.
    st.html(f"<style>{theme_css(room)}</style>")


def lamps_html(lit: list[bool]) -> str:
    """One indicator lamp per puzzle, in the room's order, lit for the solved ones."""
    lamps = "".join('<i class="on"></i>' if on else "<i></i>" for on in lit)
    return f'<div class="lamps">{lamps}</div>'


def console_bar_html(room: Room) -> str | None:
    """The status line above the title, for rooms that define one."""
    if not room.console_bar:
        return None
    left, right = (html.escape(label) for label in room.console_bar)
    return f'<div class="console-bar"><span>{left}</span><span>{right}</span></div>'


# ---- page pieces --------------------------------------------------------------
def render_header(room: Room, version: str) -> None:
    if bar := console_bar_html(room):
        st.html(bar)
    st.title(room.title)
    st.caption(f"AI Escape Room · {version}")


def render_instructions(room: Room) -> None:
    st.subheader("How to play")
    st.markdown("\n".join(f"- {line}" for line in room.instructions))


def render_opening(room: Room) -> None:
    with st.chat_message("assistant"):
        st.markdown(f"{room.intro}\n\n{room.description}")


# ---- chat ---------------------------------------------------------------------
def typing_chunks(text: str) -> list[str]:
    """Words with the whitespace around them: joined again they give exactly `text`."""
    return re.findall(r"\s*\S+\s*", text) or ([text] if text else [])


def typing_delay(chunks: int) -> float:
    """Pause after each chunk: TYPING_DELAY, or less when the reply is long."""
    return min(TYPING_DELAY, TYPING_SECONDS / max(chunks, 1))


def _typed_out(text: str) -> Iterator[str]:
    chunks = typing_chunks(text)
    delay = typing_delay(len(chunks))
    for chunk in chunks:
        yield chunk
        time.sleep(delay)


def render_turn(role: str, text: str, typed: bool = False) -> None:
    """Show one chat message. `typed=True` prints the host's reply word by word, like terminal output."""
    with st.chat_message("user" if role == "user" else "assistant"):
        if typed and role != "user" and text.strip():
            st.write_stream(_typed_out(text))
        else:
            st.markdown(text)


def type_next_reply() -> None:
    """Call after a turn, before the rerun: the next `render_history` types the last message out."""
    st.session_state[TYPE_LAST] = True


def render_history(messages: list[tuple[str, str]]) -> None:
    """Show the chat so far as (role, text) pairs. Only the reply of the turn that has just been
    played is typed out, and only once: any later rerun shows it at once."""
    type_last = st.session_state.pop(TYPE_LAST, False)
    for index, (role, text) in enumerate(messages):
        render_turn(role, text, typed=type_last and index == len(messages) - 1)
