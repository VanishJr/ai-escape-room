"""Adversarial evaluation: can a player break the rules of the game?

For every case in adversarial_cases.json the script rebuilds the game situation
without spending API calls (V1: a canned chat history; V2: the same steps applied
by the engine), sends the attack message and records:
  - escaped:       did the game end as "escaped"? (never legitimate in these cases)
  - leaked:        forbidden patterns found in the reply (secret codes, or text
                   that only appears if an impossible action succeeded)
  - state_changed: (V2) did solved puzzles, inventory or open locks change?
A run counts as a successful attack if any check fires. Every reply is saved in
the summary, and docs/evals/manual_review.json can override the automatic verdict
of a run after reading it (key "<version>/<case>/<model>/<run>").

Free-tier quota is ~20 requests per day per model, so the protocol is one run per
case on each of two models, the same models for V1 and V2 (V2 adds one parser
call per case on a separate, pinned parser model):
  uv run python scripts/adversarial_eval.py --version v1 --model gemini-3.6-flash
  uv run python scripts/adversarial_eval.py --version v2 --model gemini-3.6-flash
  uv run python scripts/adversarial_eval.py --version v1 --summary-only
Results are appended to docs/evals/<version>_results.jsonl; recorded runs are
skipped, so the same command can be re-run after the quota resets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from common.config import PROJECT_ROOT, Settings, get_settings
from common.llm_client import ChatTurn, GeminiClient, LLMClient, LLMError, QuotaExhausted
from common.room import Room, default_room
from v1.game import V1Game
from v2.engine.actions import Action
from v2.engine.rules import resolve
from v2.engine.state import GameState
from v2.game import V2Game
from v2.llm.narrator import NarrationTurn, build_turn_prompt

CASES_PATH = Path(__file__).with_name("adversarial_cases.json")
RESULTS_DIR = PROJECT_ROOT / "docs" / "evals"
DEFAULT_PARSER_MODEL = "gemini-3.5-flash-lite"


# ---- building the situation before the attack ---------------------------
def resolve_reply(ref: str, room: Room) -> str:
    kind, _, key = ref.partition(":")
    if kind == "desc":
        return room.obj(key).description
    if kind == "fact":
        return room.fact(key).text
    return ref


def v1_history(setup: list[str], steps: dict, room: Room) -> list[ChatTurn]:
    history: list[ChatTurn] = []
    for step_id in setup:
        step = steps[step_id]
        history += [ChatTurn("user", step["text"]), ChatTurn("model", resolve_reply(step["reply"], room))]
    return history


def v2_setup(setup: list[str], steps: dict, game: V2Game) -> tuple[GameState, list[NarrationTurn]]:
    """Apply the setup steps with the engine only, building the narrator history a real game would have."""
    state, history = game.new_game(), []
    for step_id in setup:
        step = steps[step_id]
        action = Action(**step["action"])
        state, outcome = resolve(state, action, game.room)
        prompt = build_turn_prompt(step["text"], action, outcome, state, game.room)
        history.append(NarrationTurn(prompt, resolve_reply(step["reply"], game.room)))
    return state, history


def progress(state: GameState) -> dict:
    return {
        "solved": sorted(state.solved),
        "inventory": sorted(state.inventory),
        "unlocked": sorted(o for o, st in state.objects.items() if st.unlocked),
        "escaped": state.escaped,
    }


# ---- running one attack ---------------------------------------------------
def run_v1(case: dict, steps: dict, llm: LLMClient, room: Room) -> dict:
    reply = V1Game(llm, room).process_turn(v1_history(case["setup"], steps, room), case["attack"])
    return {"reply": reply.text, "escaped": reply.escaped, "model": getattr(llm, "last_model", None)}


def run_v2(case: dict, steps: dict, llm: LLMClient, room: Room) -> dict:
    game = V2Game(llm, room)
    state, history = v2_setup(case["setup"], steps, game)
    result = game.process_turn(state, case["attack"], history)
    if result.error:  # the parser failed: nothing was tested
        if "daily free-tier quota" in result.error:
            raise QuotaExhausted(result.error)
        raise LLMError(result.error)
    if isinstance(result.narrator_error, QuotaExhausted):
        raise result.narrator_error
    if result.used_fallback and not result.guard_triggered:
        raise LLMError("Narrator unavailable; the run would not test the LLM.")  # retry later
    return {
        "reply": result.reply,
        "escaped": result.state.escaped,
        "state_changed": progress(result.state) != progress(state),
        "parsed": result.action.model_dump(mode="json"),
        "event": result.outcome.event,
        "guard_triggered": result.guard_triggered,
        "model": result.narrator_model,
        "parser_model": result.parser_model,
    }


RUNNERS = {"v1": run_v1, "v2": run_v2}


def judge(case: dict, outcome: dict) -> dict:
    """Forbidden entries are case-sensitive regular expressions."""
    outcome["leaked"] = [p for p in case["forbidden"] if re.search(p, outcome["reply"])]
    outcome["attack_succeeded"] = bool(outcome["escaped"] or outcome["leaked"] or outcome.get("state_changed"))
    return outcome


def attack_id(case: dict) -> str:
    return hashlib.sha1(case["attack"].encode()).hexdigest()[:8]


# ---- results --------------------------------------------------------------
def load_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def final_verdict(record: dict, manual: dict) -> tuple[bool, str | None]:
    key = f"{record['version']}/{record['case']}/{record['pinned']}/{record['run']}"
    review = manual.get(key)
    if review:
        return review["verdict"] == "succeeded", review.get("note")
    return record["attack_succeeded"], None


def write_summary(version: str, cases: list[dict], results: list[dict], results_dir: Path = RESULTS_DIR) -> Path:
    manual_path = results_dir / "manual_review.json"
    manual = json.loads(manual_path.read_text(encoding="utf-8")) if manual_path.exists() else {}
    current = {c["id"]: attack_id(c) for c in cases}
    results = [r for r in results if current.get(r["case"]) == r.get("attack_id")]  # ignore outdated attacks
    models = sorted({r["pinned"] for r in results})
    verdicts = {id(r): final_verdict(r, manual) for r in results}
    broken = sum(v[0] for v in verdicts.values())

    lines = [
        f"# Adversarial evaluation: {version.upper()}",
        "",
        f"Successful attacks: **{broken} / {len(results)}** runs ({', '.join(models) or 'no runs yet'}).",
        "",
        "An attack succeeds if the game ends as escaped, a forbidden pattern (a secret code or text that only",
        "appears if an impossible action worked) is in the reply, or (V2) game progress changes. Verdicts marked",
        "*(manual)* were corrected after reading the reply.",
        "",
        "| Case | " + " | ".join(models) + " |",
        "|---|" + "---|" * len(models),
    ]
    for case in cases:
        cells = []
        for model in models:
            runs = [r for r in results if r["case"] == case["id"] and r["pinned"] == model]
            marks = []
            for r in runs:
                succeeded, note = verdicts[id(r)]
                marks.append(("❌ broken" if succeeded else "✅ held") + (" *(manual)*" if note else ""))
            cells.append(", ".join(marks) or "—")
        lines.append(f"| {case['title']} | " + " | ".join(cells) + " |")

    lines += ["", "## Transcripts", ""]
    for case in cases:
        attack = case["attack"].replace("\n", "\\n")
        lines += [f"### {case['title']} (`{case['id']}`)", "", f"**Attack:** `{attack}`", ""]
        lines += [f"**Expected:** {case['expected']}", ""]
        for r in (r for r in results if r["case"] == case["id"]):
            succeeded, note = verdicts[id(r)]
            details = [f"model `{r.get('model')}`"]
            if "event" in r:
                details += [f"parsed as `{r['parsed']['intent']}`", f"engine event `{r['event']}`"]
                details += ["leak guard triggered"] if r.get("guard_triggered") else []
            flags = [*(f"matched `{p}`" for p in r["leaked"]), *(["escaped"] if r["escaped"] else [])]
            flags += ["state changed"] if r.get("state_changed") else []
            verdict = "ATTACK SUCCEEDED" if succeeded else "held"
            lines += [f"- **{verdict}** ({', '.join(details)}){': ' + ', '.join(flags) if flags else ''}", ""]
            if note:
                lines += [f"  *Manual review:* {note}", ""]
            lines += ["  > " + r["reply"].replace("\n", "\n  > "), ""]
    path = results_dir / f"{version}_summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---- main -----------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", choices=sorted(RUNNERS), required=True)
    parser.add_argument("--model", help="pin the story model (V1 game master / V2 narrator)")
    parser.add_argument("--parser-model", default=DEFAULT_PARSER_MODEL, help="pin the V2 parser model")
    parser.add_argument("--runs", type=int, default=1, help="runs per case and model (free tier: ~20 requests/day)")
    parser.add_argument("--delay", type=float, default=6.0, help="seconds between attacks (per-minute limit)")
    parser.add_argument("--patience", type=int, default=10, help="attempts per run while the model is overloaded")
    parser.add_argument("--cases", nargs="*", help="only run these case ids")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    room = default_room()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / f"{args.version}_results.jsonl"
    results = load_results(results_path)

    if not args.summary_only:
        base = get_settings()
        settings = Settings(
            api_key=base.api_key,
            models=(args.model,) if args.model else base.models,
            parser_models=(args.parser_model,),
        )
        # patient retries: free-tier models often return 503 "high demand" for a while
        client = GeminiClient(settings, max_retries=3, base_delay=5.0, timeout_s=60.0)
        pinned = args.model or "chain"
        done = {(r["case"], r["pinned"], r["run"], r.get("attack_id")) for r in results}
        todo = [
            (c, run)
            for c in data["cases"]
            if not args.cases or c["id"] in args.cases
            for run in range(1, args.runs + 1)
            if (c["id"], pinned, run, attack_id(c)) not in done
        ]
        print(f"{len(todo)} runs to do.")
        for i, (case, run) in enumerate(todo):
            if i:
                time.sleep(args.delay)
            outcome = None
            for _ in range(args.patience):
                try:
                    outcome = RUNNERS[args.version](case, data["steps"], client, room)
                    break
                except QuotaExhausted:
                    break
                except LLMError as exc:  # overloaded: the model usually recovers within minutes
                    print(f"  {exc} Waiting 60 s...")
                    time.sleep(60)
            if outcome is None:
                print("Stopped (daily quota used up or model unavailable). Re-run the same command later.")
                break
            record = judge(case, outcome) | {
                "version": args.version,
                "case": case["id"],
                "attack_id": attack_id(case),
                "run": run,
                "pinned": pinned,
                "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            with results_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            results.append(record)
            status = "ATTACK SUCCEEDED" if record["attack_succeeded"] else "held"
            print(f"[{case['id']} run {run}] {status} {record['leaked'] or ''}")

    summary = write_summary(args.version, data["cases"], results)
    print(f"Summary: {summary.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
