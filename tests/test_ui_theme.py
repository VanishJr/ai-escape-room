"""The console look: host labels, the injected CSS and the apps rendering in both rooms."""

import json

import pytest
from streamlit.testing.v1 import AppTest

from common.config import PROJECT_ROOT
from common.llm_client import LLMError
from common.room import DEFAULT_ROOM_PATH, ROOMS, Room, default_room, load_room
from common.ui import host_label, host_name, lamps_html, theme_css

SHELTER = load_room(ROOMS["shelter"])
TIMEOUT = 30


def room_hosted_by(name: str) -> Room:
    data = json.loads(DEFAULT_ROOM_PATH.read_text(encoding="utf-8"))
    data["host"] = {"name": name, "voice": "Plain."}
    return Room.model_validate(data)


def test_host_names_for_a_room_with_and_without_a_host():
    assert host_label(SHELTER) == "MABEL" and host_name(SHELTER) == "MABEL"
    assert host_label(default_room()) == "GAME MASTER"
    assert host_name(default_room()) == "The Game Master"


def test_theme_css_is_the_stylesheet_plus_the_host_label():
    css = theme_css(SHELTER)
    assert '[data-testid="stChatMessage"]' in css
    assert css.rstrip().endswith(':root { --host-label: "MABEL"; }')
    assert '--host-label: "GAME MASTER";' in theme_css(default_room())


def test_host_label_cannot_break_out_of_the_css_string_or_the_style_tag():
    css = theme_css(room_hosted_by('A"B\\C</style><b>'))
    declaration = css[css.rindex("--host-label:") :]
    assert "</style>" not in declaration and "<" not in declaration
    assert '"A\\"B\\\\C' in declaration  # the quote and the backslash are escaped


def test_lamps_show_one_light_per_puzzle():
    html = lamps_html(solved=2, total=3)
    assert html.count("<i") == 3 and html.count('class="on"') == 2
    assert lamps_html(solved=0, total=3).count('class="on"') == 0


@pytest.fixture
def fake(app_llm):
    return app_llm


def run_app(version: str) -> AppTest:
    return AppTest.from_file(str(PROJECT_ROOT / version / "app.py"), default_timeout=TIMEOUT).run()


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_shelter_apps_draw_the_theme_and_the_status_line(version, use_room, fake):
    use_room("shelter")
    at = run_app(version)
    assert not at.exception
    blocks = [h.body for h in at.html]
    assert any(body.startswith("<style>") and "--host-label" in body for body in blocks)
    assert any('class="console-bar"' in body and "LARKFIELD CIVIL SHELTER" in body for body in blocks)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_library_apps_get_the_theme_but_no_status_line(version, fake):
    at = run_app(version)
    assert not at.exception and at.title[0].value == "The Locked Library"
    blocks = [h.body for h in at.html]
    assert any(body.startswith("<style>") for body in blocks)
    assert not any("console-bar" in body and not body.startswith("<style>") for body in blocks)


def test_progress_panel_lights_a_lamp_for_a_solved_puzzle(use_room, fake):
    use_room("shelter")
    fake.json_objects = [
        {"intent": "examine", "target": "shelf"},
        {"intent": "examine", "target": "canisters"},
        {"intent": "enter_code", "target": "footlocker", "code": "3185"},
    ]
    fake.texts = ["One.", "Two.", "Three."]
    at = run_app("v2")
    for text in ("examine the supply shelf", "examine the canisters", "enter 3185 on the footlocker"):
        at.chat_input[0].set_value(text).run()
    assert not at.exception
    lamps = next(h.body for h in at.html if 'class="lamps"' in h.body)
    assert lamps.count("<i") == 3 and lamps.count('class="on"') == 1
    assert any(m.value == "✓ The water ration" for m in at.sidebar.markdown)


@pytest.mark.parametrize(
    "room_name, speaker", [("shelter", "MABEL"), ("library", "The Game Master")]
)
def test_errors_name_the_host(room_name, speaker, use_room, fake):
    use_room(room_name)
    fake.texts = [LLMError("quota")]
    at = run_app("v1")
    at.chat_input[0].set_value("look around").run()
    assert at.error[0].value == f"{speaker} is unavailable: quota"
