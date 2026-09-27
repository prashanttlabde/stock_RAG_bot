"""Phase 7 tests: the guard -> retrieve -> generate -> format sequence, and INV-3/INV-4.

Runs against the real `data/chroma/` store, the same convention `tests/test_answer.py`
and `tests/test_retriever.py` already use: this project's central claim is that the
demo works offline against the shipped corpus, so a fact question should be answered
by the real pipeline, not by a mock of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import chat_service, config
from src.chat_service import (
    EXAMPLE_QUESTIONS,
    SAMPLE_QUESTIONS,
    ChatTurn,
    _infer_scheme_from_history,
    _names_any_scheme,
    format_reply,
    handle_message,
    recent_turns,
)
from src.guards import copy
from src.types import Answer, ChatReply, RetrievalHit
from tests.test_policy import FAKE_AADHAAR, FAKE_PAN

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _snapshot(root: Path) -> dict[str, tuple[int, float]]:
    """path -> (size, mtime) for every file under `root`, for a before/after diff."""
    return {
        str(path): (path.stat().st_size, path.stat().st_mtime)
        for path in root.rglob("*")
        if path.is_file()
    }


class TestFactualQuestion:
    def test_reply_has_one_citation_and_a_freshness_line(self) -> None:
        reply = handle_message("What is the exit load of HDFC Large Cap?")
        assert reply.refusal is None
        assert reply.answer is not None
        assert reply.answer.answer_text is not None
        assert reply.text.count("https://groww.in/") == 1
        assert "Last updated from sources:" in reply.text
        assert reply.decision_kind == "answer"

    def test_current_nav_question_answers_nav_not_fund_manager(self) -> None:
        # Regression: "current" used to be the only surviving content term for this
        # question (both "nav" and the scheme name were stripped as entities), and it
        # coincidentally word-matched the unrelated "Current Fund Manager" sentence in
        # the same chunk, which outranked the actual NAV sentence and was returned as
        # a confidently-cited but completely wrong answer.
        reply = handle_message("what is current NAV of HDFC Small Cap Fund Direct Growth?")
        assert reply.answer is not None
        assert reply.answer.answer_text is not None
        assert "fund manager" not in reply.answer.answer_text.lower()
        assert "nav" in reply.answer.answer_text.lower()
        assert "₹159.82" in reply.answer.answer_text

    @pytest.mark.parametrize("question", EXAMPLE_QUESTIONS)
    def test_every_example_question_is_answered(self, question: str) -> None:
        reply = handle_message(question)
        assert reply.answer is not None
        assert reply.answer.answer_text is not None
        assert reply.text.count("https://groww.in/") == 1

    @pytest.mark.parametrize("question", SAMPLE_QUESTIONS)
    def test_every_sample_question_is_answered(self, question: str) -> None:
        reply = handle_message(question)
        assert reply.answer is not None
        assert reply.answer.answer_text is not None, f"no answer for {question!r}"
        assert reply.text.count("https://groww.in/") == 1


class TestAdviceQuestion:
    def test_advice_reply_has_amfi_link_and_no_citation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _spy(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("retrieve() must not be called for a refused query")

        monkeypatch.setattr(chat_service, "retrieve", _spy)

        reply = handle_message("Should I buy HDFC Large Cap?")

        assert reply.refusal is not None
        assert reply.refusal.kind == "advice"
        assert copy.AMFI_INVESTOR_EDUCATION_URL in reply.text
        assert "groww.in" not in reply.text
        assert reply.retrieved == []

    def test_returns_question_is_refused_before_retrieval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _spy(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("retrieve() must not be called for a refused query")

        monkeypatch.setattr(chat_service, "retrieve", _spy)

        reply = handle_message("What is the 1 year return?")
        assert reply.refusal is not None
        assert reply.refusal.kind == "returns"


class TestPiiQuestion:
    def test_pii_reply_never_echoes_the_pan_or_aadhaar(self) -> None:
        reply = handle_message(f"My PAN is {FAKE_PAN} and Aadhaar {FAKE_AADHAAR}")
        assert reply.refusal is not None
        assert reply.refusal.kind == "pii"
        assert FAKE_PAN not in reply.text
        assert FAKE_AADHAAR not in reply.text

    def test_pii_question_never_reaches_the_retriever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _spy(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("retrieve() must not be called for a PII-flagged query")

        monkeypatch.setattr(chat_service, "retrieve", _spy)
        handle_message(f"My PAN is {FAKE_PAN}")


class TestEmptyInput:
    @pytest.mark.parametrize("question", ["", "   ", "\n\t "])
    def test_empty_input_gets_a_friendly_message(self, question: str) -> None:
        reply = handle_message(question)
        assert reply.refusal is not None
        assert reply.refusal.kind == "empty"
        assert reply.text
        assert reply.retrieved == []


class TestOffCorpusQuestion:
    def test_nonsense_question_gets_the_no_answer_message(self) -> None:
        # Not "...SEBI registration number...": that phrase trips the grievance
        # guard (KIND_GRIEVANCE matches "sebi") before retrieval even runs, which
        # is documented, correct behaviour -- see implementation-notes.md's Phase 5
        # "Carried into Phase 7" note. This question has no such guard word, so it
        # reaches retrieval and is rejected there instead, on its own missing merits.
        reply = handle_message("Tell me about the fund manager's education background.")
        assert reply.refusal is None
        assert reply.answer is not None
        assert reply.answer.answer_text is None
        assert reply.decision_kind == "no_answer"
        assert "don't have that" in reply.text


class TestFormatReply:
    def test_format_reply_is_the_only_function_producing_answer_text(self) -> None:
        answer = Answer(
            answer_text="Exit load of 1% if redeemed within 1 year",
            source_url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            fetched_at="2026-09-27",
        )
        text = format_reply(answer)
        lines = text.splitlines()
        assert lines[0] == answer.answer_text
        assert lines[1] == f"Source: {answer.source_url}"
        assert "Last updated from sources: 2026-09-27" in text
        assert copy.DISCLAIMER in text

    def test_no_fetched_at_omits_the_freshness_line(self) -> None:
        answer = Answer(answer_text="fact", source_url="https://groww.in/x", fetched_at=None)
        assert "Last updated from sources:" not in format_reply(answer)


class TestShowRetrievalDebugFlag:
    def test_debug_lines_appear_only_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "SHOW_RETRIEVAL", False)
        reply_off = handle_message("What is the exit load of HDFC Large Cap?")
        assert "retrieved (debug)" not in reply_off.text

        monkeypatch.setattr(config, "SHOW_RETRIEVAL", True)
        reply_on = handle_message("What is the exit load of HDFC Large Cap?")
        assert "retrieved (debug)" in reply_on.text
        assert "hdfc-large-cap-direct-growth" in reply_on.text


class TestChatTurnAndRecentTurns:
    def test_recent_turns_returns_chat_turn_records(self) -> None:
        handle_message("What is the exit load of HDFC Large Cap?")
        turns = recent_turns()
        assert turns
        assert isinstance(turns[-1], ChatTurn)
        assert turns[-1].decision_kind == "answer"
        assert turns[-1].latency_ms >= 0

    def test_recent_turns_is_bounded(self) -> None:
        for _ in range(60):
            handle_message("What is the exit load of HDFC Large Cap?")
        assert len(recent_turns()) <= 50


class TestNothingIsPersisted:
    def test_fifty_mixed_messages_write_no_file_under_data(self) -> None:
        # Warm the Chroma collection and the embedding model *before* snapshotting.
        # Chroma's persistent client touches (but does not resize or change the
        # content of) its own index files the first time a process opens the
        # collection -- an artifact of the library, unrelated to anything this
        # module writes. Snapshotting only after that one-time touch is what makes
        # this test assert "handling messages writes nothing" rather than
        # "opening the store for the first time writes nothing", and it is why
        # this test's outcome must not depend on which other tests ran first.
        handle_message("What is the exit load of HDFC Large Cap?")

        data_dir = config.DATA_DIR
        before = _snapshot(data_dir)

        questions = [
            "What is the exit load of HDFC Large Cap?",
            "Should I buy HDFC Small Cap?",
            f"My PAN is {FAKE_PAN}",
            "What is the 1 year return?",
            "How do I raise a grievance with SEBI?",
            "What is the SEBI registration number of HDFC Mutual Fund?",
            "",
            "What is the minimum SIP amount?",
        ]
        for i in range(50):
            handle_message(questions[i % len(questions)])

        after = _snapshot(data_dir)
        assert before == after

    def test_fifty_messages_write_nothing_under_the_project_root(self) -> None:
        skip_dirs = {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache"}

        def _snapshot_root() -> dict[str, tuple[int, float]]:
            snap: dict[str, tuple[int, float]] = {}
            for path in PROJECT_ROOT.rglob("*"):
                if not path.is_file():
                    continue
                if any(part in skip_dirs for part in path.parts):
                    continue
                snap[str(path)] = (path.stat().st_size, path.stat().st_mtime)
            return snap

        handle_message("What is the exit load of HDFC Large Cap?")  # warm the store first
        before = _snapshot_root()
        for _ in range(50):
            handle_message("What is the exit load of HDFC Large Cap?")
        after = _snapshot_root()
        assert before == after


class TestRetrievalHitsAttached:
    def test_answer_reply_carries_its_retrieval_hits(self) -> None:
        reply = handle_message("What is the exit load of HDFC Large Cap?")
        assert reply.retrieved
        assert all(isinstance(hit, RetrievalHit) for hit in reply.retrieved)

    def test_refusal_reply_carries_no_hits(self) -> None:
        reply = handle_message("Should I buy HDFC Large Cap?")
        assert reply.retrieved == []


def _fake_answer_reply(scheme_id: str) -> ChatReply:
    """A minimal answered `ChatReply`, for building fake history without a real call."""
    answer = Answer(
        answer_text="some fact",
        source_url="https://groww.in/mutual-funds/x",
        fetched_at="2026-09-27",
        scheme_id=scheme_id,
    )
    return ChatReply(text="some fact", answer=answer, decision_kind="answer")


def _fake_refusal_reply() -> ChatReply:
    from src.types import Refusal

    refusal = Refusal(kind="advice", message="no")
    return ChatReply(text="no", refusal=refusal, decision_kind="advice")


class TestInferSchemeFromHistory:
    def test_empty_history_infers_nothing(self) -> None:
        assert _infer_scheme_from_history([]) is None

    def test_most_recent_answered_scheme_wins(self) -> None:
        history = [_fake_answer_reply("hdfc-large-cap-direct-growth"), _fake_answer_reply("hdfc-elss-tax-saver-direct-growth")]
        assert _infer_scheme_from_history(history) == "hdfc-elss-tax-saver-direct-growth"

    def test_a_refusal_turn_is_skipped_looking_further_back(self) -> None:
        history = [_fake_answer_reply("hdfc-large-cap-direct-growth"), _fake_refusal_reply()]
        assert _infer_scheme_from_history(history) == "hdfc-large-cap-direct-growth"

    def test_only_the_configured_window_is_considered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "MEMORY_WINDOW", 2)
        history = [
            _fake_answer_reply("hdfc-large-cap-direct-growth"),
            _fake_refusal_reply(),
            _fake_refusal_reply(),
        ]
        # Only the last 2 entries (both refusals) are in the window, so the scheme
        # from 3 turns back must not be found even though it is the only one present.
        assert _infer_scheme_from_history(history) is None


class TestNamesAnyScheme:
    def test_an_indexed_scheme_counts(self) -> None:
        assert _names_any_scheme("What is the exit load of HDFC Large Cap?")

    def test_a_competing_brand_counts(self) -> None:
        assert _names_any_scheme("What is the exit load of Mirae Asset Large Cap?")

    def test_an_out_of_corpus_hdfc_scheme_counts(self) -> None:
        assert _names_any_scheme("What is the NAV of HDFC Mid Cap?")

    def test_a_scheme_less_question_does_not_count(self) -> None:
        assert not _names_any_scheme("What about the minimum SIP amount?")


class TestConversationMemory:
    def test_a_follow_up_with_no_scheme_resolves_the_prior_one(self) -> None:
        first = handle_message("What is the exit load of HDFC Small Cap?")
        assert first.answer is not None and first.answer.scheme_id == "hdfc-small-cap-direct-growth"

        follow_up = handle_message("What about the minimum SIP amount?", history=[first])
        assert follow_up.answer is not None
        assert follow_up.answer.scheme_id == "hdfc-small-cap-direct-growth"
        assert follow_up.answer.answer_text is not None
        assert "₹100" in follow_up.answer.answer_text

    def test_naming_a_different_scheme_overrides_memory(self) -> None:
        first = handle_message("What is the exit load of HDFC Small Cap?")
        follow_up = handle_message(
            "What is the lock in period for ELSS?", history=[first]
        )
        assert follow_up.answer is not None
        assert follow_up.answer.scheme_id == "hdfc-elss-tax-saver-direct-growth"

    def test_naming_an_out_of_corpus_scheme_is_not_overridden_by_memory(self) -> None:
        first = handle_message("What is the exit load of HDFC Small Cap?")
        follow_up = handle_message(
            "What is the exit load of HDFC Mid Cap?", history=[first]
        )
        assert follow_up.answer is not None
        assert follow_up.answer.answer_text is None
        assert follow_up.answer.scheme_id != "hdfc-small-cap-direct-growth"

    def test_history_beyond_the_window_is_not_consulted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "MEMORY_WINDOW", 2)
        old_scheme_turn = _fake_answer_reply("hdfc-small-cap-direct-growth")
        # Two more turns push the old one outside a window of 2.
        history = [old_scheme_turn, _fake_refusal_reply(), _fake_refusal_reply()]
        follow_up = handle_message("What about the minimum SIP amount?", history=history)
        # With no scheme resolvable, this unscoped attribute question should not
        # confidently land on the (out-of-window) Small Cap scheme.
        assert follow_up.answer is None or follow_up.answer.scheme_id != "hdfc-small-cap-direct-growth"

    def test_guardrails_are_unaffected_by_history(self) -> None:
        first = handle_message("What is the exit load of HDFC Small Cap?")

        def _spy(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("retrieve() must not be called for a refused query")

        import src.chat_service as chat_service_module

        original_retrieve = chat_service_module.retrieve
        chat_service_module.retrieve = _spy
        try:
            reply = handle_message("Should I buy HDFC Large Cap?", history=[first])
        finally:
            chat_service_module.retrieve = original_retrieve
        assert reply.refusal is not None
        assert reply.refusal.kind == "advice"

    def test_no_history_behaves_exactly_as_before(self) -> None:
        with_none = handle_message("What is the exit load of HDFC Small Cap?", history=None)
        without_arg = handle_message("What is the exit load of HDFC Small Cap?")
        assert with_none.answer.answer_text == without_arg.answer.answer_text

    def test_history_is_never_written_to_disk(self) -> None:
        data_dir = config.DATA_DIR
        handle_message("What is the exit load of HDFC Small Cap?")  # warm the store
        before = _snapshot(data_dir)

        history: list[ChatReply] = []
        for q in [
            "What is the exit load of HDFC Small Cap?",
            "What about the minimum SIP amount?",
            "And the risk level?",
        ]:
            history.append(handle_message(q, history=history))

        after = _snapshot(data_dir)
        assert before == after
