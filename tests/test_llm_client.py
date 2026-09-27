"""Tests for the Groq client wired into `_build_client` (architecture.md §7 step 5).

No test in this file makes a real network call: `requests.post` is always replaced
with a fake before `GroqClient.complete` runs, which is what keeps this project's
"no network calls at query time" guarantee true of the test suite too.
"""

from __future__ import annotations

import pytest
import requests

from src import config
from src.generate import llm_client
from src.generate.llm_client import GroqClient, build_groq_client


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


def _ok_payload(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class TestBuildGroqClient:
    def test_returns_none_with_no_key_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "GROQ_API_KEY", "")
        monkeypatch.setattr(config, "LLM_API_KEY", "")
        assert build_groq_client() is None

    def test_returns_a_client_when_groq_key_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_fake")
        client = build_groq_client()
        assert isinstance(client, GroqClient)

    def test_falls_back_to_the_generic_llm_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "GROQ_API_KEY", "")
        monkeypatch.setattr(config, "LLM_API_KEY", "generic_fake")
        client = build_groq_client()
        assert isinstance(client, GroqClient)


class TestGroqClientComplete:
    def test_sends_the_expected_request_and_returns_the_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        def _fake_post(url: str, headers: dict, json: dict, timeout: int) -> _FakeResponse:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["timeout"] = timeout
            return _FakeResponse(_ok_payload("Exit load of 1% if redeemed within 1 year."))

        monkeypatch.setattr(llm_client.requests, "post", _fake_post)

        client = GroqClient(api_key="gsk_fake", model="llama-3.1-8b-instant")
        result = client.complete(system="be grounded", user="What is the exit load?", temperature=0)

        assert result == "Exit load of 1% if redeemed within 1 year."
        assert captured["headers"]["Authorization"] == "Bearer gsk_fake"
        assert captured["json"]["model"] == "llama-3.1-8b-instant"
        assert captured["json"]["messages"] == [
            {"role": "system", "content": "be grounded"},
            {"role": "user", "content": "What is the exit load?"},
        ]
        assert captured["json"]["temperature"] == 0
        assert captured["url"] == config.GROQ_API_URL

    def test_raises_on_an_http_error_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fake_post(*_args: object, **_kwargs: object) -> _FakeResponse:
            return _FakeResponse({}, status=401)

        monkeypatch.setattr(llm_client.requests, "post", _fake_post)
        client = GroqClient(api_key="bad_key", model="llama-3.1-8b-instant")
        with pytest.raises(requests.HTTPError):
            client.complete(system="s", user="u")

    def test_raises_on_a_malformed_response_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fake_post(*_args: object, **_kwargs: object) -> _FakeResponse:
            return _FakeResponse({"unexpected": "shape"})

        monkeypatch.setattr(llm_client.requests, "post", _fake_post)
        client = GroqClient(api_key="k", model="m")
        with pytest.raises(ValueError):
            client.complete(system="s", user="u")


class TestGenerateLlmFallsBackOnGroqFailure:
    def test_a_raising_groq_client_still_yields_a_template_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.generate.answer import generate_llm, generate_template
        from src.types import Chunk, RetrievalHit

        def _fake_post(*_args: object, **_kwargs: object) -> _FakeResponse:
            return _FakeResponse({}, status=500)

        monkeypatch.setattr(llm_client.requests, "post", _fake_post)
        client = GroqClient(api_key="k", model="m")

        chunk = Chunk(
            chunk_id="x::001",
            scheme_id="hdfc-large-cap-direct-growth",
            source_url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            section="test",
            fetched_at="2026-09-27",
            text="Min. for SIP: ₹100",
        )
        hits = [RetrievalHit(chunk=chunk, score=0.9)]
        question = "What is the minimum SIP amount?"
        assert generate_llm(question, hits, client=client) == generate_template(question, hits)
