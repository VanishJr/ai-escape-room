"""Helpers for the tests that run the Streamlit apps."""

from streamlit.testing.v1 import AppTest

from common.config import PROJECT_ROOT

TIMEOUT = 30


def run_app(version: str) -> AppTest:
    """Start `v1/app.py` or `v2/app.py` and run it once."""
    return AppTest.from_file(str(PROJECT_ROOT / version / "app.py"), default_timeout=TIMEOUT).run()


def chat_texts(at: AppTest) -> list[str]:
    return [m.markdown[0].value for m in at.chat_message]
