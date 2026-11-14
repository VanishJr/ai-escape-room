"""The console look: host labels, the injected CSS and the apps rendering in both rooms."""

import json
import tomllib

import pytest
from app_helpers import run_app

import common.ui
from common.config import PROJECT_ROOT
from common.llm_client import LLMError
from common.room import DEFAULT_ROOM_PATH, ROOMS, Room, default_room, load_room
from common.ui import (
    CONSOLE_BACKGROUND,
    console_bar_html,
    css_string,
    host_label,
    host_name,
    lamps_html,
    theme_css,
)

SHELTER = load_room(ROOMS["shelter"])


def library_with(**fields) -> Room:
    data = json.loads(DEFAULT_ROOM_PATH.read_text(encoding="utf-8"))
    return Room.model_validate({**data, **fields})


@pytest.fixture
def console_theme(monkeypatch):
    """The apps as started from the repository root, whatever folder pytest runs in."""
    monkeypatch.setattr(common.ui, "console_theme_loaded", lambda: True)


# ---- pieces -----------------------------------------------------------------
def test_host_names_for_a_room_with_and_without_a_host():
    assert host_label(SHELTER) == "MABEL" and host_name(SHELTER) == "MABEL"
    assert host_label(default_room()) == "GAME MASTER"
    assert host_name(default_room()) == "The Game Master"


def test_theme_css_is_the_stylesheet_plus_the_host_label():
    css = theme_css(SHELTER)
    assert '[data-testid="stChatMessage"]' in css
    assert css.rstrip().endswith(':root { --host-label: "MABEL"; }')
    assert '--host-label: "GAME MASTER";' in theme_css(default_room())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("MABEL", '"MABEL"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("</style><b>", '"\\3c /style>\\3c b>"'),
        ("TAB\t9000", '"TAB\\9 9000"'),  # a CSS escape, not "\t", which CSS would read as the letter t
        ("line\nbreak", '"line\\a break"'),
    ],
)
def test_css_string_escapes_what_could_end_the_string_or_the_style_element(text, expected):
    assert css_string(text) == expected


def test_host_label_cannot_break_out_of_the_stylesheet():
    css = theme_css(library_with(host={"name": 'A"B\\C</style><b>', "voice": "Plain."}))
    declaration = css[css.rindex("--host-label:") :]
    assert "</style>" not in declaration and "<" not in declaration


def test_lamps_follow_the_puzzles_one_by_one():
    assert lamps_html([True, True, False]) == '<div class="lamps"><i class="on"></i><i class="on"></i><i></i></div>'
    assert lamps_html([False, False, True]) == '<div class="lamps"><i></i><i></i><i class="on"></i></div>'


def test_status_line_is_escaped_and_optional():
    room = library_with(console_bar=["A <b> & C", 'RIGHT "SIDE"'])
    assert console_bar_html(room) == (
        '<div class="console-bar"><span>A &lt;b&gt; &amp; C</span><span>RIGHT &quot;SIDE&quot;</span></div>'
    )
    assert console_bar_html(default_room()) is None


def test_the_stylesheet_is_tied_to_the_theme_file():
    """The CSS is applied only when Streamlit has loaded this theme (see console_theme_loaded)."""
    config = tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["theme"]["backgroundColor"].lower() == CONSOLE_BACKGROUND


# ---- the apps -----------------------------------------------------------------
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_shelter_apps_draw_the_theme_and_the_status_line(version, use_room, app_llm, console_theme):
    use_room("shelter")
    at = run_app(version)
    assert not at.exception
    blocks = [h.body for h in at.html]
    assert any(body.startswith("<style>") and '--host-label: "MABEL"' in body for body in blocks)
    assert any(body.startswith('<div class="console-bar">') and "LARKFIELD CIVIL SHELTER" in body for body in blocks)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_library_apps_get_the_theme_but_no_status_line(version, app_llm, console_theme):
    at = run_app(version)
    assert not at.exception and at.title[0].value == "The Locked Library"
    blocks = [h.body for h in at.html]
    assert any(body.startswith("<style>") and '--host-label: "GAME MASTER"' in body for body in blocks)
    assert not any(body.startswith('<div class="console-bar">') for body in blocks)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_without_the_theme_file_the_stylesheet_is_left_out(version, app_llm, monkeypatch):
    """Started outside the repository root: dark console text on Streamlit's light page would be unreadable."""
    monkeypatch.setattr(common.ui, "console_theme_loaded", lambda: False)
    at = run_app(version)
    assert not at.exception and at.title[0].value == "The Locked Library"
    assert not any(h.body.startswith("<style>") for h in at.html)
    assert "repository root" in at.info[0].value


def test_progress_panel_lights_the_lamp_of_the_solved_puzzle(use_room, app_llm):
    """The third puzzle solved first lights the third lamp, next to its name."""
    use_room("shelter")
    app_llm.json_objects = [
        {"intent": "examine", "target": "desk"},
        {"intent": "examine", "target": "note"},
        {"intent": "examine", "target": "gauges"},
    ]
    app_llm.texts = ["One.", "Two.", "Three."]
    at = run_app("v2")
    for text in ("examine the desk", "read the note", "examine the gauges"):
        at.chat_input[0].set_value(text).run()
    assert not at.exception
    lamps = next(h.body for h in at.html if h.body.startswith('<div class="lamps">'))
    assert lamps == lamps_html([False, False, True])
    assert [m.value for m in at.sidebar.markdown if m.value.startswith("✓")] == ["✓ The frozen gauges"]


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("room_name, speaker", [("shelter", "MABEL"), ("library", "The Game Master")])
def test_errors_name_the_host(version, room_name, speaker, use_room, app_llm):
    use_room(room_name)
    app_llm.texts = [LLMError("quota")]
    app_llm.json_objects = [LLMError("quota")]
    at = run_app(version)
    at.chat_input[0].set_value("examine the desk").run()
    assert at.error[0].value == f"{speaker} is unavailable: quota"
