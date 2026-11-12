"""The adversarial evaluation script, run offline with scripted LLMs."""

import json
import sys

import pytest
from fakes import FakeLLM

from common.config import PROJECT_ROOT
from common.llm_client import LLMError, QuotaExhausted
from common.room import default_room

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import adversarial_eval as ev  # noqa: E402

DATA = json.loads(ev.CASES_PATH.read_text())
ROOM = default_room()
CASES = {c["id"]: c for c in DATA["cases"]}


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["id"])
def test_every_setup_replays_for_both_versions(case):
    history = ev.v1_history(case["setup"], DATA["steps"], ROOM)
    assert len(history) == 2 * len(case["setup"])
    state, narration = ev.v2_setup(case["setup"], DATA["steps"], ev.V2Game(FakeLLM(), ROOM))
    assert len(narration) == len(case["setup"]) and not state.escaped


def test_clue_combining_setup_knows_every_clue_but_door_is_locked():
    state, _ = ev.v2_setup(CASES["clue_combining"]["setup"], DATA["steps"], ev.V2Game(FakeLLM(), ROOM))
    assert {"painting", "clock"} <= set(state.solved) and not state.escaped


def test_judge_uses_case_sensitive_patterns():
    case = CASES["uncollected_item"]
    held = ev.judge(case, {"reply": "Nothing here was founded in the dark.", "escaped": False})
    broken = ev.judge(case, {"reply": "Glowing letters: FOUNDED IN 18__.", "escaped": False})
    assert not held["attack_succeeded"] and broken["attack_succeeded"]


def test_state_change_counts_as_successful_attack():
    out = ev.judge(CASES["admin_impersonation"], {"reply": "ok", "escaped": False, "state_changed": True})
    assert out["attack_succeeded"]


def test_v2_runner_stops_on_narrator_quota():
    case = CASES["admin_impersonation"]
    llm = FakeLLM(json_objects=[{"intent": "meta"}], texts=[QuotaExhausted("daily free-tier quota")])
    with pytest.raises(QuotaExhausted):
        ev.run_v2(case, DATA["steps"], llm, ROOM)


def test_retry_after_busy_narrator_reuses_the_parse():
    case = CASES["admin_impersonation"]
    fake = FakeLLM(json_objects=[{"intent": "meta"}], texts=[LLMError("busy"), "The door stays shut."])
    llm = ev.ReusedParse(fake)
    with pytest.raises(LLMError):
        ev.run_v2(case, DATA["steps"], llm, ROOM)
    assert ev.run_v2(case, DATA["steps"], llm, ROOM)["event"] == "meta_refused"
    assert len(fake.json_calls) == 1


def test_v2_runner_holds_against_a_fooled_parser():
    case = CASES["skip_puzzles"]
    llm = FakeLLM(json_objects=[{"intent": "enter_code", "target": "door", "code": "1847"}], texts=["Nothing happens."])
    out = ev.judge(case, ev.run_v2(case, DATA["steps"], llm, ROOM))
    assert out["event"] == "code_unknown" and not out["attack_succeeded"]


def test_summary_applies_manual_review_and_ignores_outdated_attacks(tmp_path):
    case = CASES["nonexistent_object"]
    base = {"version": "v1", "case": case["id"], "run": 1, "pinned": "m", "escaped": False, "model": "m"}
    fresh = ev.judge(case, base | {"reply": "You wield the crowbar.", "attack_id": ev.attack_id(case)})
    outdated = ev.judge(case, base | {"reply": "old", "attack_id": "deadbeef", "run": 2})
    (tmp_path / "manual_review.json").write_text(
        json.dumps({f"v1/{case['id']}/m/1": {"verdict": "held", "note": "the crowbar is refused later on"}})
    )
    text = ev.write_summary("v1", DATA["cases"], [fresh, outdated], tmp_path).read_text()
    assert "**0 / 1**" in text and "*(manual)*" in text
    assert "- Round 1: 0 / 1 runs" in text and "- Round 2: 0 / 0 runs" in text


def test_round_two_cases_are_marked():
    assert {c["id"] for c in DATA["cases"] if c.get("round") == 2} == {
        "prompt_dump", "single_digit", "brute_force_range", "roleplay_year", "code_in_words"
    }
