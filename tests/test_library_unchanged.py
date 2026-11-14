"""The library's story prompts are the ones the attack evaluation ran on.

docs/evals/ records how V1 and V2 behaved with exactly these prompts, and the README
says so. The hashes were taken on `main` at the submitted version (tag v2.0). If a
prompt of the library has to change, the recorded evaluation no longer describes the
code: update the hash together with the note in the README's Results section.
"""

import hashlib

from common.room import default_room
from v1.prompts import build_system_prompt as v1_prompt
from v2.llm.narrator import build_system_prompt as narrator_prompt

V1_PROMPT_SHA256 = "75a16cdd6ba4b39f3815e45036777465e091049ce8c46cb73a7c116626f7e43d"
NARRATOR_PROMPT_SHA256 = "e19e1c9ce84656159ce32048a6590123137ea6d8e8c496534b739e20f3b379e1"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_v1_prompt_of_the_library_is_the_evaluated_one():
    assert sha256(v1_prompt(default_room())) == V1_PROMPT_SHA256


def test_narrator_prompt_of_the_library_is_the_evaluated_one():
    assert sha256(narrator_prompt(default_room())) == NARRATOR_PROMPT_SHA256
