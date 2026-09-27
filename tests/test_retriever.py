"""Phase 4 retriever tests: scoping, the tuned floor, and duplicate collapse.

These run against the real `data/chroma/` store rather than a synthetic one,
because the acceptance criteria are about the corpus that actually ships. Tests that
need an empty store point `CHROMA_PATH` at a fresh temporary directory instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src import config
from src.errors import RetrievalError
from src.ingest import store as store_module
from src.ingest.chunk import chunk_corpus
from src.ingest.load import load_corpus
from src.retrieve import retriever
from src.retrieve.retriever import (
    OUT_OF_CORPUS_FUND_HOUSES,
    OUT_OF_CORPUS_SCHEMES,
    SCHEME_ALIASES,
    _collection,
    _is_lexically_grounded,
    content_terms,
    detect_out_of_corpus_fund_house,
    detect_out_of_corpus_scheme,
    detect_scheme_id,
    normalize_query,
    retrieve,
)


@pytest.fixture(scope="module", autouse=True)
def built_store() -> None:
    """Skip the module with a clear reason when `make ingest` has not been run."""
    if not store_module.collection_exists():
        pytest.skip("no vector store; run `make ingest` first")
    _collection.cache_clear()


@pytest.fixture
def empty_store(tmp_path: Path):
    """Point the retriever at a path with no store, then restore the real one."""
    patch = pytest.MonkeyPatch()
    patch.setattr(config, "CHROMA_PATH", tmp_path / "absent")
    store_module._CLIENTS.clear()
    _collection.cache_clear()
    yield tmp_path / "absent"
    patch.undo()
    store_module._CLIENTS.clear()
    _collection.cache_clear()


class TestNormalizeQuery:
    @pytest.mark.parametrize(
        "raw",
        [
            "What is the exit load of HDFC Large Cap?",
            "what is the exit load of hdfc large cap",
            "  WHAT   is the EXIT LOAD of HDFC Large Cap  ",
            "what is the exit load of hdfc-large-cap",
        ],
    )
    def test_case_and_punctuation_do_not_matter(self, raw: str) -> None:
        assert normalize_query(raw) == "what is the exit load of hdfc large cap"


class TestDetectSchemeId:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("What is the exit load of HDFC Large Cap?", "hdfc-large-cap-direct-growth"),
            ("exit load of large cap", "hdfc-large-cap-direct-growth"),
            ("ELSS lock in period", "hdfc-elss-tax-saver-direct-growth"),
            ("tax saver lock in", "hdfc-elss-tax-saver-direct-growth"),
            ("80c lock in", "hdfc-elss-tax-saver-direct-growth"),
            ("expense ratio of hdfc equity fund", "hdfc-equity-flexi-cap-direct-growth"),
            ("flexi cap minimum sip", "hdfc-equity-flexi-cap-direct-growth"),
            ("minimum sip for small cap", "hdfc-small-cap-direct-growth"),
            ("is balanced advantage right", "hdfc-balanced-advantage-direct-growth"),
            ("a hybrid fund", "hdfc-balanced-advantage-direct-growth"),
        ],
    )
    def test_it_recognises_each_scheme(self, query: str, expected: str) -> None:
        assert detect_scheme_id(query) == expected

    def test_no_scheme_mention_is_none(self) -> None:
        assert detect_scheme_id("what is the exit load") is None

    def test_two_schemes_are_ambiguous_not_guessed(self) -> None:
        assert detect_scheme_id("Compare HDFC Large Cap and HDFC Small Cap") is None

    def test_tax_and_large_cap_together_are_ambiguous(self) -> None:
        assert detect_scheme_id("Do I pay tax on HDFC Large Cap?") is None

    def test_taxation_is_not_the_tax_alias(self) -> None:
        assert detect_scheme_id("what is the taxation policy") is None

    def test_every_alias_maps_to_an_allowlisted_scheme(self) -> None:
        allowed = {ref.scheme_id for ref in _allowlist()}
        assert set(SCHEME_ALIASES.values()) <= allowed


class TestOutOfCorpusFundHouses:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("What is the exit load of Mirae Asset Large Cap?", "Mirae Asset"),
            ("tell me about Parag Parikh Flexi Cap", "Parag Parikh"),
            ("is SBI Bluechip a good fund", "SBI Mutual Fund"),
            ("should I buy Kotak Flexicap", "Kotak Mutual Fund"),
        ],
    )
    def test_it_names_the_competing_fund_house(self, query: str, expected: str) -> None:
        assert detect_out_of_corpus_fund_house(query) == expected

    def test_an_indexed_fund_house_is_not_reported_missing(self) -> None:
        assert detect_out_of_corpus_fund_house("exit load of HDFC Large Cap") is None

    def test_a_brand_beats_a_simultaneously_named_indexed_alias(self) -> None:
        # "large cap" is an indexed alias, so scheme detection finds a scheme here.
        # The brand check runs first, because no reading of "Mirae Asset Large Cap"
        # means HDFC's fund.
        assert detect_scheme_id("What is the exit load of Mirae Asset Large Cap?")
        assert detect_out_of_corpus_fund_house("What is the exit load of Mirae Asset Large Cap?")

    def test_it_returns_no_hits_even_with_an_indexed_alias_present(self) -> None:
        # This is the false answer the two-tier split exists to prevent: HDFC Large
        # Cap's exit load, confidently given as Mirae Asset's.
        result = retrieve("What is the exit load of Mirae Asset Large Cap?")
        assert result.hits == []
        assert result.detected_scheme_id is None
        assert result.considered_count == 0

    def test_a_category_word_alone_still_defers_to_an_indexed_alias(self) -> None:
        # The opposite rule: "mid cap" must not veto a comparison the corpus can
        # partly answer, so the brand tier blocks and the category tier does not.
        assert detect_out_of_corpus_fund_house("Compare HDFC Mid Cap and HDFC Large Cap") is None
        result = retrieve("Compare HDFC Mid Cap and HDFC Large Cap exit load")
        assert result.detected_scheme_id == "hdfc-large-cap-direct-growth"
        assert result.hits


class TestOutOfCorpusSchemes:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("What is the NAV of HDFC Mid Cap?", "HDFC Mid-Cap Opportunities"),
            ("expense ratio of HDFC Floating Rate Fund", "HDFC Floating Rate"),
            ("tell me about the dividend yield fund", "HDFC Dividend Yield"),
            ("is the pharma fund any good", "HDFC Pharma"),
        ],
    )
    def test_it_names_the_missing_scheme(self, query: str, expected: str) -> None:
        assert detect_out_of_corpus_scheme(query) == expected

    def test_an_indexed_scheme_is_not_reported_missing(self) -> None:
        assert detect_out_of_corpus_scheme("exit load of HDFC Large Cap") is None

    def test_benchmark_wording_is_not_mistaken_for_an_index_fund(self) -> None:
        # The Balanced Advantage chunk literally contains "NIFTY 50 Hybrid Composite
        # Debt 50:50 Index", so a bare "index" alias would break this query.
        assert detect_out_of_corpus_scheme("what is the benchmark") is None

    def test_naming_a_missing_scheme_alongside_an_indexed_one_does_not_block(self) -> None:
        result = retrieve("Compare HDFC Mid Cap and HDFC Large Cap exit load")
        assert result.detected_scheme_id == "hdfc-large-cap-direct-growth"
        assert result.hits

    def test_out_of_corpus_entries_are_distinct_from_the_allowlist(self) -> None:
        # A phrase in both tables would claim a scheme is simultaneously indexed
        # and missing, which is a contradiction rather than a preference.
        assert not set(OUT_OF_CORPUS_SCHEMES) & set(SCHEME_ALIASES)
        assert not set(OUT_OF_CORPUS_FUND_HOUSES) & set(SCHEME_ALIASES)
        assert not set(OUT_OF_CORPUS_FUND_HOUSES) & set(OUT_OF_CORPUS_SCHEMES)

    def test_no_denied_phrase_appears_in_the_corpus(self) -> None:
        """The denylists are only safe while the corpus cannot contain them.

        A single-word entry like "axis" or "union" is a plausible English word, so
        adding one carelessly would let a question about an indexed fund be rejected
        on a word that occurs in its own text. This test fails the moment that
        happens.
        """
        corpus = "\n".join(chunk.text for chunk in chunk_corpus(load_corpus())).lower()
        for table in (OUT_OF_CORPUS_SCHEMES, OUT_OF_CORPUS_FUND_HOUSES):
            for phrase in table:
                assert not re.search(rf"\b{re.escape(phrase)}\b", corpus), (
                    f"{phrase!r} is denied but occurs in the corpus"
                )


class TestContentTerms:
    def test_entity_and_question_words_are_removed(self) -> None:
        assert content_terms("What is the exit load of HDFC Large Cap?") == ["exit", "load"]

    def test_a_pure_entity_question_has_no_content_terms(self) -> None:
        assert content_terms("tell me about HDFC Large Cap") == []

    def test_manager_is_treated_as_entity_not_content(self) -> None:
        # "manager" appears in the corpus, so leaving it in would let a question
        # about a manager's education ground on the word alone.
        assert "manager" not in content_terms("fund manager education background")

    def test_nav_is_a_content_word_not_an_entity(self) -> None:
        # Unlike "fund"/"tax", "nav" is confined to the chunks that actually carry a
        # NAV figure, so it is safe -- and necessary -- to keep as real content.
        assert content_terms("What is the NAV of HDFC Small Cap?") == ["nav"]

    def test_generic_time_modifiers_are_stopwords(self) -> None:
        # "current" used to survive stripping as the *only* content term for "what is
        # current NAV of HDFC Small Cap Fund Direct Growth?", and it happened to
        # word-match the unrelated "Current Fund Manager" sentence, outranking the
        # actual NAV sentence in generation. None of these words should ever be
        # asked to carry retrieval or ranking signal.
        for word in ("current", "currently", "latest", "recent", "recently", "now", "today"):
            assert word not in content_terms(f"what is the {word} NAV of HDFC Small Cap?")


class TestLexicalGrounding:
    def test_a_present_term_is_grounded(self) -> None:
        assert _is_lexically_grounded(["exit", "load"], "Exit load: 1% within 1 year")

    def test_an_absent_term_is_not_grounded(self) -> None:
        assert not _is_lexically_grounded(["sebi", "registration"], "HDFC Mutual Fund")

    def test_a_term_inside_a_longer_word_does_not_count(self) -> None:
        # "operation" and "Corporation" both contain the substring "ratio".
        assert not _is_lexically_grounded(["ratio"], "Registrar & Transfer Agent, operation")
        assert not _is_lexically_grounded(["ratio"], "HDFC Corporation profile")
        assert _is_lexically_grounded(["ratio"], "Expense ratio: 0.77%")

    def test_no_terms_means_no_opinion(self) -> None:
        assert _is_lexically_grounded([], "anything at all")

    def test_the_required_match_count_comes_from_config(self, monkeypatch) -> None:
        # "Exit load" matches both terms; the second sentence matches only one.
        text = "Exit load: 1% within one year. No charge after that."
        assert _is_lexically_grounded(["exit", "load"], text)
        monkeypatch.setattr(config, "MIN_ANSWER_TERM_MATCH", 2)
        assert _is_lexically_grounded(["exit", "load"], text)
        monkeypatch.setattr(config, "MIN_ANSWER_TERM_MATCH", 2)
        assert not _is_lexically_grounded(["exit", "stamp", "duty"], "Exit load: 1%")


class TestRetrieve:
    def test_exit_load_of_large_cap_returns_large_cap_hits(self) -> None:
        result = retrieve("What is the exit load of HDFC Large Cap?")
        assert result.detected_scheme_id == "hdfc-large-cap-direct-growth"
        assert result.hits
        assert all(hit.scheme_id == "hdfc-large-cap-direct-growth" for hit in result.hits)

    def test_elss_lock_in_returns_elss_hits(self) -> None:
        result = retrieve("ELSS lock in period")
        assert result.detected_scheme_id == "hdfc-elss-tax-saver-direct-growth"
        assert result.hits
        assert all(hit.scheme_id == "hdfc-elss-tax-saver-direct-growth" for hit in result.hits)
        assert "3Y Lock in" in result.hits[0].chunk.text

    def test_an_off_corpus_query_returns_zero_hits_at_the_tuned_floor(self) -> None:
        assert retrieve("What is the NAV of HDFC Mid Cap?").hits == []

    @pytest.mark.parametrize(
        "query",
        [
            "What is the NAV of HDFC Mid Cap?",
            "expense ratio of HDFC Floating Rate Fund",
            "What is the SEBI registration number of HDFC Mutual Fund?",
            "Tell me about the fund manager's education background.",
            "How can I update my bank account details?",
            "What is the eligibility criteria for the tax saver fund?",
        ],
    )
    def test_every_labelled_out_of_corpus_query_is_empty(self, query: str) -> None:
        assert retrieve(query).hits == []

    def test_a_missing_scheme_short_circuits_before_searching(self) -> None:
        result = retrieve("What is the NAV of HDFC Mid Cap?")
        assert result.considered_count == 0
        assert result.is_empty

    def test_hits_are_sorted_by_descending_score(self) -> None:
        scores = [hit.score for hit in retrieve("expense ratio of HDFC Small Cap?").hits]
        assert scores == sorted(scores, reverse=True)

    def test_every_hit_carries_a_citation_and_metadata(self) -> None:
        for hit in retrieve("What is the exit load of HDFC Large Cap?").hits:
            assert hit.chunk.chunk_id
            assert hit.chunk.source_url.startswith("https://groww.in/mutual-funds/")
            assert hit.chunk.section
            assert hit.chunk.fetched_at
            assert 0.0 <= hit.score <= 1.0

    def test_top_k_is_respected(self) -> None:
        assert len(retrieve("minimum sip", top_k=2).hits) <= 2

    def test_explicit_scheme_id_overrides_detection(self) -> None:
        result = retrieve("tell me about the exit load", scheme_id="hdfc-small-cap-direct-growth")
        assert result.detected_scheme_id == "hdfc-small-cap-direct-growth"
        assert all(hit.scheme_id == "hdfc-small-cap-direct-growth" for hit in result.hits)

    def test_a_high_floor_empties_the_result(self) -> None:
        assert retrieve("What is the exit load of HDFC Large Cap?", similarity_floor=0.99).hits == []

    def test_considered_count_exceeds_returned_hits(self) -> None:
        result = retrieve("what is the exit load")
        assert result.considered_count > len(result.hits)

    def test_the_floor_is_reported_back(self) -> None:
        assert retrieve("exit load of large cap", similarity_floor=0.33).floor == 0.33


class TestDuplicateCollapse:
    def test_an_unscoped_boilerplate_question_returns_one_copy(self) -> None:
        # "Understand terms" is byte-identical across all 5 schemes. Without
        # collapsing, all TOP_K slots hold the same sentence.
        result = retrieve("understand terms", top_k=4)
        texts = [hit.chunk.text for hit in result.hits]
        assert len(texts) == len(set(texts))

    def test_collapse_preserves_the_best_scoring_copy(self) -> None:
        hits = [
            _hit("hdfc-large-cap-direct-growth", "A", 0.9),
            _hit("hdfc-small-cap-direct-growth", "A", 0.7),
            _hit("hdfc-flexi-cap-direct-growth", "B", 0.6),
        ]
        collapsed = retriever._dedupe_by_text(hits)
        assert [hit.scheme_id for hit in collapsed] == [
            "hdfc-large-cap-direct-growth",
            "hdfc-flexi-cap-direct-growth",
        ]

    def test_whitespace_differences_still_count_as_duplicates(self) -> None:
        hits = [_hit("hdfc-large-cap-direct-growth", "same text", 0.9), _hit("hdfc-small-cap-direct-growth", "  same text\n", 0.8)]
        assert len(retriever._dedupe_by_text(hits)) == 1

    def test_overfetch_keeps_a_full_budget_of_distinct_facts(self) -> None:
        # Asking Chroma for exactly TOP_K rows would return 4 copies of one fact.
        result = retrieve("exit load", top_k=4)
        assert result.considered_count >= 4 * config.RETRIEVE_OVERFETCH
        assert len(result.hits) == len({hit.chunk.text for hit in result.hits})


class TestFailureModes:
    def test_a_missing_store_raises_retrieval_error(self, empty_store: Path) -> None:
        with pytest.raises(RetrievalError, match="make ingest"):
            retrieve("what is the exit load")

    def test_an_empty_query_raises_retrieval_error(self) -> None:
        with pytest.raises(RetrievalError, match="empty query"):
            retrieve("   ")

    def test_the_message_is_user_safe(self, empty_store: Path) -> None:
        with pytest.raises(RetrievalError) as caught:
            retrieve("what is the exit load")
        message = str(caught.value)
        assert "make ingest" in message
        assert "Traceback" not in message
        assert "chromadb" not in message.lower()


class TestCaching:
    def test_the_same_path_reuses_one_handle(self) -> None:
        path = str(config.CHROMA_PATH)
        assert _collection(path) is _collection(path)

    def test_a_different_path_gets_a_different_handle(self, empty_store: Path) -> None:
        # `config.CHROMA_PATH` is the temporary path inside this fixture, so the
        # comparison needs a third, genuinely distinct location.
        other = empty_store.parent / "other-absent"
        assert _collection(str(other)) is not _collection(str(config.CHROMA_PATH))


def _allowlist():
    import yaml

    raw = yaml.safe_load((config.PROJECT_ROOT / "data" / "sources.yaml").read_text(encoding="utf-8"))
    entries = raw["sources"] if isinstance(raw, dict) else raw
    return [type("R", (), entry)() for entry in entries]


def _hit(scheme_id: str, text: str, score: float):
    from src.types import Chunk, RetrievalHit

    return RetrievalHit(
        chunk=Chunk(
            chunk_id=f"{scheme_id}::x::001",
            scheme_id=scheme_id,
            source_url="https://groww.in/mutual-funds/x",
            section="s",
            fetched_at="2026-09-27",
            text=text,
        ),
        score=score,
    )
