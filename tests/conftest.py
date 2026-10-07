"""Shared by every test: none of them asks a real model for anything."""

from __future__ import annotations

import pytest


class _NoOllama:
    """In place of the client the title stage makes for itself when a run gives
    it none: a test that leaves the stage without a stand-in gets what a
    machine without Ollama gets, whatever is running on this one."""

    def __init__(self, *_, **__):
        raise ConnectionError("no Ollama in the tests")


@pytest.fixture(autouse=True)
def _no_real_model_for_the_title_stage(monkeypatch):
    monkeypatch.setattr("book_agent.stages.title.OllamaClient", _NoOllama)
