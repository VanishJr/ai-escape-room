"""Fixtures shared by the whole suite.

The apps play the room named by ESCAPE_ROOM. Every test is pinned to the library,
the reference room of the suite, so neither the apps' default nor a local `.env`
can change what a test runs on. A test that wants another room asks for `use_room`.
"""

import pytest
import streamlit as st

from common.config import get_settings


@pytest.fixture(autouse=True)
def library_room(monkeypatch):
    monkeypatch.setenv("ESCAPE_ROOM", "library")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def use_room(monkeypatch):
    """Call `use_room("shelter")` to make the apps and `active_room()` load that room."""

    def select(name: str) -> None:
        monkeypatch.setenv("ESCAPE_ROOM", name)
        get_settings.cache_clear()
        st.cache_resource.clear()  # the V1 app caches its game together with the room

    yield select
    st.cache_resource.clear()
