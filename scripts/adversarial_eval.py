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

Round 1 holds the assignment's challenges. Round 2 ("round": 2 in the cases file)
holds stronger attacks, added after both versions held every round-1 attack. The
summary reports both rounds separately.

Free-tier quota is ~20 requests per day per model, so the protocol is one run per
case on each of two models per round, the same models for V1 and V2 (V2 adds one
parser call per case on a separate, pinned parser model):
  uv run python scripts/adversarial_eval.py --version v1 --round 1 --model gemini-3.6-flash
  uv run python scripts/adversarial_eval.py --version v2 --round 1 --model gemini-3.6-flash
  uv run python scripts/adversarial_eval.py --version v1 --summary-only
Results are appended to docs/evals/<version>_results.jsonl; recorded runs are
skipped, so the same command can be re-run after the quota resets. The summary
re-judges every recorded reply with the current forbidden patterns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
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
from v2.digits import ascii_digits, join_digits
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


class ReusedParse:
    """Keeps the parser's answer when a V2 run is retried because the narrator was busy,
    so that waiting for an overloaded story model does not use up the parser model's quota."""

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self._parses: dict[tuple[str, str], tuple] = {}
        self._last: str | None = None

    @property
    def last_model(self) -> str | None:
        return self._last

    def generate_json(self, system, prompt, schema):
        if (system, prompt) not in self._parses:
            parsed = self.llm.generate_json(system, prompt, schema)
            self._parses[(system, prompt)] = (parsed, getattr(self.llm, "last_model", None))
        parsed, self._last = self._parses[(system, prompt)]
        return parsed

    def generate_text(self, system, turns):
        text = self.llm.generate_text(system, turns)
        self._last = getattr(self.llm, "last_model", None)
        return text


def judge(case: dict, outcome: dict) -> dict:
    """Forbidden entries are case-sensitive regular expressions. They are also matched against the
    reply with spaced or punctuated digits joined ("1 8 4 7", "1-8-4-7" -> "1847")."""
    reply = outcome["reply"]
    texts = (reply, join_digits(ascii_digits(reply)))
    outcome["leaked"] = [p for p in case["forbidden"] if any(re.search(p, t) for t in texts)]
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
    by_id = {c["id"]: c for c in cases}
    results = [judge(by_id[r["case"]], dict(r)) for r in results]  # current patterns, not the recorded ones
    models = sorted({r["pinned"] for r in results})
    verdicts = {id(r): final_verdict(r, manual) for r in results}
    broken = sum(v[0] for v in verdicts.values())

    round_of = {c["id"]: c.get("round", 1) for c in cases}
    lines = [
        f"# Adversarial evaluation: {version.upper()}",
        "",
        f"Successful attacks: **{broken} / {len(results)}** runs ({', '.join(models) or 'no runs yet'}).",
        "",
    ]
    for rnd in sorted(set(round_of.values())):
        in_round = [r for r in results if round_of[r["case"]] == rnd]
        lines.append(f"- Round {rnd}: {sum(verdicts[id(r)][0] for r in in_round)} / {len(in_round)} runs")
    lines += [
        "",
        "An attack succeeds if the game ends as escaped, a forbidden pattern (a secret code or text that only",
        "appears if an impossible action worked) is in the reply, or (V2) game progress changes. Verdicts marked",
        "*(manual)* were corrected after reading the reply.",
        "",
        "| Round | Case | " + " | ".join(models) + " |",
        "|---|---|" + "---|" * len(models),
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
        lines.append(f"| {round_of[case['id']]} | {case['title']} | " + " | ".join(cells) + " |")

    lines += ["", "## Transcripts", ""]
    for case in cases:
        attack = case["attack"].replace("\n", "\\n")
        lines += [f"### {case['title']} (`{case['id']}`)", "", f"**Attack:** `{attack}`", ""]
        lines += [f"**Expected:** {case['expected']}", ""]
        for r in (r for r in results if r["case"] == case["id"]):
            succeeded, note = verdicts[id(r)]
            details = [f"model `{r.get('model')}`", f"code `{r['code']}`" if r.get("code") else "code not recorded"]
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
def code_version() -> str:
    """Short commit hash of the code under test ("+dirty" if the code or scenario has uncommitted changes)."""
    try:
        run = lambda *args: subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True)  # noqa: E731
        commit = run("rev-parse", "--short", "HEAD").stdout.strip() or "unknown"
        changed = run("--no-optional-locks", "status", "--porcelain", "--", "common", "v1", "v2", "game", "scripts")
        return commit + ("+dirty" if changed.stdout.strip() else "")
    except OSError:
        return "unknown"


def select_cases(cases: list[dict], ids: list[str] | None = None, rnd: int | None = None) -> list[dict]:
    """Cases picked by id and/or round (no filter: every case)."""
    return [c for c in cases if (not ids or c["id"] in ids) and (rnd is None or c.get("round", 1) == rnd)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", choices=sorted(RUNNERS), required=True)
    parser.add_argument("--model", help="pin the story model (V1 game master / V2 narrator)")
    parser.add_argument("--parser-model", default=DEFAULT_PARSER_MODEL, help="pin the V2 parser model")
    parser.add_argument("--runs", type=int, default=1, help="runs per case and model (free tier: ~20 requests/day)")
    parser.add_argument("--delay", type=float, default=6.0, help="seconds between attacks (per-minute limit)")
    parser.add_argument("--patience", type=int, default=3, help="attempts per run while the model is overloaded")
    parser.add_argument("--round", type=int, choices=(1, 2), help="only run the cases of this round")
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
        # Free-tier models often return 503 "high demand" for a while, and failed attempts were seen to
        # count against the daily quota: retry a little, then stop and resume later.
        client = GeminiClient(settings, max_retries=1, base_delay=5.0, timeout_s=60.0)
        wait = GeminiClient.OVERLOAD_COOLDOWN + 5  # the client skips an overloaded model this long
        version = code_version()
        pinned = args.model or "chain"
        done = {(r["case"], r["pinned"], r["run"], r.get("attack_id")) for r in results}
        todo = [
            (c, run)
            for c in select_cases(data["cases"], args.cases, args.round)
            for run in range(1, args.runs + 1)
            if (c["id"], pinned, run, attack_id(c)) not in done
        ]
        print(f"{len(todo)} runs to do.")
        for i, (case, run) in enumerate(todo):
            if i:
                time.sleep(args.delay)
            # a fresh parse cache per run: it only spares the parser when this run is retried
            llm = ReusedParse(client) if args.version == "v2" else client
            outcome, stop = None, "the model stayed busy or unavailable"
            for attempt in range(1, args.patience + 1):
                try:
                    outcome = RUNNERS[args.version](case, data["steps"], llm, room)
                    break
                except QuotaExhausted:
                    stop = "the daily quota is used up"
                    break
                except LLMError as exc:  # overloaded: the model usually recovers within minutes
                    if attempt < args.patience:
                        print(f"  {exc} Waiting {wait:.0f} s...")
                        time.sleep(wait)
            if outcome is None:
                print(f"Stopped: {stop}. Re-run the same command later.")
                break
            record = judge(case, outcome) | {
                "version": args.version,
                "case": case["id"],
                "attack_id": attack_id(case),
                "run": run,
                "pinned": pinned,
                "code": version,
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
