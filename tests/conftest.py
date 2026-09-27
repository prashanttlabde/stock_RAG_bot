"""Shared test fixtures and safety nets.

A local `.env` with `GENERATION_MODE=llm` is deliberately supported for manual,
ad hoc testing against a real Groq key -- but the automated suite must never
depend on whatever a contributor's `.env` happens to be set to. Without this
fixture, a `.env` left in `llm` mode makes every test that calls
`answer_question`/`generate_llm` without overriding the mode itself send a real
request to Groq using whatever key is configured, which is slow, non-deterministic,
and burns real API quota -- exactly what happened once during manual testing (see
docs/implementation-notes.md's Groq addendum).
"""

from __future__ import annotations

import pytest

from src import config


@pytest.fixture(autouse=True)
def _default_to_template_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin `GENERATION_MODE` to `template` for every test.

    A test that specifically wants `llm` mode (e.g. `tests/test_answer.py`'s
    `TestGenerateLlm`) sets it explicitly inside the test body, which runs after
    this fixture and so still wins.
    """
    monkeypatch.setattr(config, "GENERATION_MODE", "template")
