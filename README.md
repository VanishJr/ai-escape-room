# AI Escape Room

An interactive escape-room game where a Large Language Model (Google Gemini) acts as the Game Master.
The player wakes up in a locked library and must explore, collect items and solve puzzles to escape.

The project is built in two versions:

- **V1 — Interactive AI Escape Room:** the LLM runs the whole game from a single system prompt.
- **V2 — Reliable AI Escape Room:** Python owns the game state and validates every action; the LLM only parses player input and narrates outcomes.

> Work in progress: V2, test cases and the full write-up are being added.

## Quick start

Requirements: Python 3.12+ and a free Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).

```bash
git clone https://github.com/VanishJr/ai-escape-room.git
cd ai-escape-room
cp .env.example .env          # then put your key into GEMINI_API_KEY

# with uv (recommended)
uv sync
uv run streamlit run v1/app.py

# or with pip
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run v1/app.py
```

Check that the key and models work: `uv run python scripts/check_gemini.py`.
Run the tests (no API key needed): `uv run pytest`.

### Free-tier limits

The Gemini free tier allows only about 20 requests per day per model. `GEMINI_MODEL` in `.env` is therefore a
comma-separated fallback chain: when one model's daily quota is used up, the client switches to the next one.
Per-minute limits are handled by waiting for the delay the API asks for.

## The game: The Locked Library

Objects: an oak desk with a locked drawer, a bookshelf, a portrait, a grandfather clock, a globe and an iron door
with a 4-digit keypad. Hidden objects (four marked volumes, a note, a UV flashlight) appear as the player explores.

| Puzzle | Clue | Unlocks |
|---|---|---|
| The four volumes | Each volume has a digit; read them in volume order, not shelf order | Desk drawer (contains a UV flashlight) |
| The hidden message | UV light on the portrait reveals "FOUNDED IN 18__" | First half of the door code |
| The stopped clock | The note says the clock's hands give the last two digits | Second half of the door code |
| The iron door | The founding year | Escape |

The whole scenario lives in [`game/room.json`](game/room.json) and is shared by both versions.

## Version 1: Interactive AI Escape Room

V1 gives the LLM the complete scenario, including every code and puzzle solution, and lets it run the game.
The conversation history is the only game state: each turn sends the system prompt and the full chat history to
Gemini. When the player opens the iron door, the model ends its message with an `[ESCAPED]` marker, which the
app detects to show the victory screen.

```mermaid
flowchart LR
    P([Player]) -->|message| UI["Streamlit UI<br/>v1/app.py"]
    UI -->|history + message| G["V1Game<br/>v1/game.py"]
    R[("game/room.json")] --> SP["build_system_prompt<br/>v1/prompts.py"]
    SP -->|"whole room, incl. all codes"| G
    G -->|"system prompt + full history"| LLM{{"Gemini<br/>Game Master"}}
    LLM -->|"narration + escape marker"| G
    G -->|"reply, escaped?"| UI
```

This works well for players who cooperate, but every rule is only a request to the model: the LLM decides
whether an action succeeds, keeps track of progress and is trusted to keep the codes secret.

## Project structure

```
common/     shared code: config, Gemini client, room schema, Streamlit helpers
game/       room.json (the scenario)
v1/         Version 1: prompt-only Game Master
v2/         Version 2: game engine + LLM parser and narrator (in progress)
scripts/    utility scripts (API check, adversarial evaluation)
tests/      pytest suite (runs without an API key)
```
