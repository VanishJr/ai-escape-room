---
title: "AI Escape Room: from an LLM Game Master to a reliable rule-validated game"
subtitle: "Mini-project 1, Deep Learning course"
---

## 1. Problem

The goal was an interactive escape-room game in which a Large Language Model acts as the Game Master. The
player types natural-language actions, the Game Master describes the room, reacts to actions and recognises
when the player has escaped. The project was built in two versions. V1 is a working, engaging LLM application.
V2 keeps the experience but makes the game consistent and resistant to players who try to break the rules.

## 2. Game design

The scenario, *The Locked Library*, is stored in `game/room.json` and shared by both versions. It has seven
visible objects (desk, desk drawer, bookshelf, portrait, grandfather clock, globe, iron door) and three hidden
ones (four marked volumes, a note, a UV flashlight). The puzzles form a chain:

1. Four volumes on the bookshelf each carry a digit; read in volume order they give the drawer code (7294).
2. The drawer contains a UV flashlight; shining it on the portrait reveals "FOUNDED IN 18__".
3. A note says the last two digits are where the clock stopped; the hands point at IV and VII.
4. The founding year, 1847, opens the iron door.

The chain covers every interaction the assignment asks for: examining objects, collecting an item, using it,
solving puzzles and a final escape challenge.

## 3. Version 1: LLM as the whole game

V1 (`v1/`) renders the entire scenario, including all codes and a solution walkthrough, into the system
prompt, together with rules such as "only allow items the player has picked up" and "never reveal codes".
Every turn sends the system prompt and the complete chat history to Gemini. The chat history is the only game
state. Escape detection uses a marker: the model is told to end its message with `[ESCAPED]` when the door
opens, and the Streamlit app strips the marker and shows the victory screen. The app has an introduction,
instructions and a restart button.

For a cooperative player V1 works well: a full playthrough was narrated correctly, including the multi-step
puzzle chain. Its weakness is structural. Every rule is a request to the model, the model holds every
secret, and progress exists only as text in the conversation.

## 4. Problems found in V1

<!-- RESULTS: filled in from docs/evals/v1_summary.md -->

## 5. Version 2: Python decides, the LLM describes

V2 (`v2/`) separates the game into a deterministic core and two narrow LLM roles.

**Game state.** `GameState` is a Pydantic model holding discovered objects, inventory, open locks,
solved puzzles, known clues, failed code attempts, lockouts, the turn counter, the escaped flag and an action
log. It is shown as JSON in the app's debug panel.

**Action validation.** A rules engine, `resolve(state, action) -> (new_state, outcome)`, is a pure function.
It checks that the target has been discovered, that a used item is in the inventory, that a code is correct
and that the lock is not jammed. Three wrong codes in a row jam a lock for three turns. The engine is the only
code that can open a lock, solve a puzzle or set `escaped`, and it never modifies the previous state, so a
failure later in the turn cannot corrupt the game.

**LLM parser.** Gemini's structured output converts the player's text into one `Action` from a fixed schema
(`examine`, `take`, `use`, `open`, `enter_code`, `inventory`, `hint`, `help`, `meta`, `unknown`). Claims about
progress, impersonation and instructions to the system are classified as `meta`, which the engine refuses.
The parser only sees objects the player has already discovered and never sees codes. Digits are copied from
the player's text with a regular expression rather than from the model.

**LLM narrator.** A second Gemini call turns the engine's `Outcome` into an atmospheric reply. It receives
what happened, the clue texts just revealed and the visible room state, and is told to describe only that.
Unsolved codes and undiscovered clues are never in its context, so it cannot leak them however it is
manipulated.

**Secrets guard and fallback.** Every reply is checked for the codes of locks that are still closed, including
spaced or hyphenated forms. A leaking reply is regenerated once and then replaced by the engine's plain text.
The same plain text is used when the narrator is unavailable, so the game stays correct without the LLM.

## 6. Testing and evaluation

**Unit tests.** 52 pytest tests run without an API key, using a scripted fake LLM. They cover the complete
walkthrough, all assignment challenges at engine level (uncollected items, wrong codes, false claims, admin
requests, nonexistent objects), lockout and recovery, state consistency over long sessions, the parser's
handling of codes, the secrets guard, and the pipeline's behaviour when the LLM fails or leaks.

**Adversarial evaluation.** `scripts/adversarial_eval.py` runs nine attack cases against both versions on the
same models. The situation before each attack is rebuilt without API calls: a canned chat history for V1,
engine actions for V2. A run counts as a successful attack if the game ends as escaped, a forbidden string
appears in the reply (a secret code, or text that only appears if an impossible action worked), or (V2) game
progress changes.

<!-- RESULTS: comparison table from docs/evals -->

## 7. Engineering problems encountered

- **Free-tier quota.** The Gemini free tier allows about 20 requests per day per model. A single playthrough
  of V1 used most of a day's quota for one model. The client therefore supports a fallback chain of models,
  and the evaluation is resumable and pins one model per run so that V1 and V2 are compared fairly.
- **Overloaded models and hidden retries.** Free models often answered 503 "high demand" or hung. The SDK's
  built-in retries kept retrying even daily-quota errors and, without a timeout, a single turn once took five
  minutes. Built-in retries were disabled in favour of a quota-aware policy: wait for the server's
  `retryDelay` on per-minute limits, skip a model whose daily quota is spent, time out after 30 s, and fall
  back to the next model.
- **SDK client lifetime.** In `google-genai` 3.x a client object that is garbage-collected closes its HTTP
  connection, which broke a one-line `genai.Client(...).models.list()` call; the client must be kept alive.

## 8. Limitations and future work

The narrator is still a language model: it can describe a scene imprecisely, although it cannot change the
state or reveal secrets it never receives. The leak guard checks digit sequences, not codes written in words.
The parser classifies a message as a whole, so a sentence mixing a real action with a meta request is
refused entirely, which is safe but occasionally strict. Future work: several rooms loaded from JSON, a
save/load feature for the game state, and an LLM-as-judge to grade narrator faithfulness automatically.
