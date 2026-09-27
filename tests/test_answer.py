"""Phase 6 generation tests: citation, the unit cap, grounding, and the sentinel.

The unit-level tests build `RetrievalHit` objects by hand so each rule can be broken in
isolation. The end-to-end tests run against the real `data/chroma/` store, because two
acceptance criteria are only meaningful against the corpus that ships: that no number in
a template answer is invented, and that the demo works with no API key and no network.
"""

from __future__ import annotations

import re

import pytest

from src import config
from src.generate import answer as answer_module
from src.generate.answer import (
    answer_question,
    compose_context,
    generate_llm,
    generate_template,
    is_ambiguous_source,
    select_primary_source,
    split_units,
    strip_urls,
    truncate_sentences,
)
from src.generate.no_answer import NO_ANSWER_HEADLINE, covered_schemes_with_dates, no_answer_message
from src.generate.prompt import GROUNDING_CHECK, NOT_IN_CONTEXT, SYSTEM_RULES, build_user_prompt
from src.ingest import store as store_module
from src.retrieve.retriever import retrieve
from src.types import Chunk, RetrievalHit

URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
OTHER_URL = "https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth"


def _chunk(
    text: str,
    url: str = URL,
    scheme_id: str = "hdfc-large-cap-direct-growth",
    section: str = "test",
) -> Chunk:
    return Chunk(
        chunk_id=f"{scheme_id}::test::001",
        scheme_id=scheme_id,
        source_url=url,
        section=section,
        fetched_at="2026-09-27",
        text=text,
    )


def _hit(
    text: str,
    score: float = 0.9,
    url: str = URL,
    scheme_id: str = "hdfc-large-cap-direct-growth",
    section: str = "test",
) -> RetrievalHit:
    return RetrievalHit(chunk=_chunk(text, url, scheme_id, section), score=score)


class TestSplitUnits:
    def test_splits_on_newlines(self) -> None:
        assert split_units("a\nb\nc") == ["a", "b", "c"]

    def test_splits_on_sentence_terminators(self) -> None:
        assert split_units("One. Two! Three?") == ["One.", "Two!", "Three?"]

    def test_keeps_a_label_attached_to_its_value(self) -> None:
        """`Min.` ends in a period but does not end a sentence.

        Splitting here separated the label from the amount, and the answer read
        "Minimum investments for SIP: 100 for SIP: 500" -- two unrelated facts welded
        together, with the number the user asked for no longer attached to its name.
        """
        assert split_units("Min. for SIP: ₹100") == ["Min. for SIP: ₹100"]

    def test_splits_a_real_sentence_after_an_abbreviation_line(self) -> None:
        assert split_units("Min. for SIP: ₹100. Lock in is 3 years.") == [
            "Min. for SIP: ₹100.",
            "Lock in is 3 years.",
        ]

    def test_blank_lines_are_dropped(self) -> None:
        assert split_units("a\n\n\n  \nb") == ["a", "b"]


class TestStripUrls:
    def test_removes_http_and_www(self) -> None:
        assert strip_urls(f"see {URL} and www.groww.in/x") == "see  and"

    def test_leaves_text_without_urls_alone(self) -> None:
        assert strip_urls("Expense ratio: 0.78%") == "Expense ratio: 0.78%"


class TestTruncateSentences:
    def test_keeps_short_text_whole(self) -> None:
        assert truncate_sentences("One. Two.") == "One. Two."

    def test_caps_at_three_units(self) -> None:
        text = "One. Two. Three. Four. Five."
        assert split_units(truncate_sentences(text)) == ["One.", "Two.", "Three."]

    def test_honours_an_explicit_limit(self) -> None:
        assert truncate_sentences("One. Two. Three.", max_sentences=1) == "One."

    def test_never_exceeds_the_cap_for_prose(self) -> None:
        prose = " ".join(f"Sentence number {n} says something." for n in range(1, 13))
        assert len(split_units(truncate_sentences(prose))) <= config.MAX_ANSWER_SENTENCES

    def test_never_exceeds_the_cap_for_label_value_lines(self) -> None:
        """The corpus is mostly label/value lines, and a line is a unit."""
        block = "\n".join(f"Field {n}: {n * 10}" for n in range(1, 11))
        assert len(split_units(truncate_sentences(block))) <= config.MAX_ANSWER_SENTENCES


class TestSelectPrimarySource:
    def test_single_url_when_all_hits_agree(self) -> None:
        hits = [_hit("a", 0.9), _hit("b", 0.8)]
        assert select_primary_source(hits) == URL
        assert is_ambiguous_source(hits) is False

    def test_top_hit_url_when_sources_differ_and_scores_are_far_apart(self) -> None:
        hits = [_hit("a", 0.90, URL), _hit("b", 0.40, OTHER_URL)]
        assert select_primary_source(hits) == URL
        assert is_ambiguous_source(hits) is False

    def test_ambiguous_returns_one_url_and_flags_it(self) -> None:
        hits = [_hit("a", 0.900, URL), _hit("b", 0.895, OTHER_URL)]
        assert select_primary_source(hits) == URL
        assert is_ambiguous_source(hits) is True

    def test_boundary_of_the_ambiguity_window(self) -> None:
        inside = [_hit("a", 0.900, URL), _hit("b", 0.855, OTHER_URL)]
        outside = [_hit("a", 0.900, URL), _hit("b", 0.849, OTHER_URL)]
        assert is_ambiguous_source(inside) is True
        assert is_ambiguous_source(outside) is False

    def test_never_returns_more_than_one_url(self) -> None:
        hits = [_hit("a", 0.9, URL), _hit("b", 0.89, OTHER_URL), _hit("c", 0.88, URL)]
        assert select_primary_source(hits).count("http") == 1


class TestGenerateTemplate:
    def test_no_hits_is_the_sentinel(self) -> None:
        assert generate_template("What is the exit load?", []) == NOT_IN_CONTEXT

    def test_off_topic_hits_are_the_sentinel(self) -> None:
        hits = [_hit("Small Cap Fund. Equity. Very High Risk.")]
        assert generate_template("What is the expense ratio?", hits) == NOT_IN_CONTEXT

    def test_picks_the_unit_carrying_the_value(self) -> None:
        """Both units match "expense ratio"; only one of them is an answer."""
        hits = [_hit("Expense ratio\nExpense ratio: 0.78%")]
        assert generate_template("What is the expense ratio?", hits) == (
            "HDFC Large Cap Fund – Direct Growth: Expense ratio: 0.78%"
        )

    def test_does_not_mix_two_schemes(self) -> None:
        """Two schemes' SIP minimums in one answer is a contradiction, not an answer."""
        hits = [
            _hit("Min. for SIP: ₹100", 0.90, URL, "hdfc-large-cap-direct-growth"),
            _hit("Min. for SIP: ₹500", 0.60, OTHER_URL, "hdfc-elss-tax-saver-direct-growth"),
        ]
        answer = generate_template("What is the minimum SIP amount?", hits)
        assert "₹100" in answer
        assert "₹500" not in answer

    def test_a_bare_label_is_completed_from_the_glossary(self) -> None:
        hits = [
            _hit(
                "Expense ratio\nA fee payable to a mutual fund house.",
                score=0.5,
                section="Understand terms",
            )
        ]
        assert generate_template("What is the expense ratio?", hits) == (
            "HDFC Large Cap Fund – Direct Growth: Expense ratio A fee payable to a mutual fund house."
        )

    def test_the_page_title_is_not_repeated_after_the_scheme_prefix(self) -> None:
        hits = [_hit("HDFC Large Cap Fund Direct Growth\nNAV: 25 Sep '26: ₹1,189.08")]
        answer = generate_template("What is the NAV of HDFC Large Cap?", hits)
        assert answer == (
            "HDFC Large Cap Fund – Direct Growth: NAV: 25 Sep '26: ₹1,189.08"
        )

    def test_a_title_that_is_the_whole_answer_is_kept(self) -> None:
        """With nothing else selected, the title is the answer and must survive."""
        hits = [_hit("HDFC Flexi Cap Fund\nNIFTY 500 Total Return Index")]
        assert generate_template("flexi cap", hits) == (
            "HDFC Large Cap Fund – Direct Growth: HDFC Flexi Cap Fund"
        )

    def test_a_valueless_label_outside_the_glossary_is_left_alone(self) -> None:
        """`Very High Risk` is the whole answer to a risk-level question.

        Completing a label with the next line made this answer "Very High Risk NAV: 25
        Sep '26: Rs159.82" -- a category with an unrelated figure stapled to it.
        """
        hits = [_hit("Small Cap\nVery High Risk\nNAV: 25 Sep '26: ₹159.82")]
        assert generate_template("What is the risk level?", hits) == (
            "HDFC Large Cap Fund – Direct Growth: Very High Risk"
        )

    def test_entity_only_question_does_not_quote_a_repeated_heading(self) -> None:
        """Every word of "Is it a large cap fund?" is a scheme-name word.

        With no content word left to match on, the old fallback took the first three lines
        verbatim and answered with the heading printed twice.
        """
        hits = [_hit("About HDFC Large Cap\nAbout HDFC Large Cap\nHDFC Large Cap is a Equity Mutual Fund Scheme.")]
        answer = generate_template("Is it a large cap fund?", hits)
        assert answer.count("About HDFC Large Cap") <= 1

    def test_result_never_exceeds_the_unit_cap(self) -> None:
        hits = [_hit("\n".join(f"Exit load note {n}: {n}%" for n in range(6)))]
        assert len(split_units(generate_template("exit load", hits))) <= config.MAX_ANSWER_SENTENCES

    def test_every_number_in_the_answer_appears_in_the_chunk(self) -> None:
        text = "Min. for SIP: ₹100\nExpense ratio: 0.78%\nExit load of 1% if redeemed within 1 year"
        answer = generate_template("What is the minimum SIP amount?", [_hit(text)])
        for number in re.findall(r"[\d.]+", answer):
            assert number in text


class TestGenerateLlm:
    def test_falls_back_to_template_without_a_client(self) -> None:
        hits = [_hit("Min. for SIP: ₹100")]
        assert generate_llm("What is the minimum SIP amount?", hits) == generate_template(
            "What is the minimum SIP amount?", hits
        )

    def test_falls_back_when_the_client_raises(self) -> None:
        class Broken:
            def complete(self, **_: object) -> str:
                raise RuntimeError("no network")

        hits = [_hit("Min. for SIP: ₹100")]
        assert generate_llm("What is the minimum SIP amount?", hits, client=Broken()) == (
            generate_template("What is the minimum SIP amount?", hits)
        )

    def test_uses_the_client_when_one_works(self) -> None:
        class Working:
            def complete(self, **_: object) -> str:
                return "Min. for SIP: ₹100."

        hits = [_hit("Min. for SIP: ₹100")]
        assert generate_llm("What is the minimum SIP amount?", hits, client=Working()) == (
            "Min. for SIP: ₹100."
        )

    def test_a_sentinel_from_the_model_is_preserved(self) -> None:
        class Refusing:
            def complete(self, **_: object) -> str:
                return NOT_IN_CONTEXT

        hits = [_hit("Min. for SIP: ₹100")]
        assert GROUNDING_CHECK.search(generate_llm("What is the NAV?", hits, client=Refusing()))


class TestPromptContract:
    def test_system_rules_forbid_the_three_things_that_break_grounding(self) -> None:
        for rule in ("only", "never invent", "3 sentences", "advice", NOT_IN_CONTEXT):
            assert rule.lower() in SYSTEM_RULES.lower()

    def test_prompt_carries_every_chunk_and_each_url_once(self) -> None:
        hits = [_hit("first", 0.9, URL), _hit("second", 0.8, URL, "hdfc-elss-tax-saver-direct-growth")]
        prompt = build_user_prompt("What is the exit load?", [hit.chunk for hit in hits])
        assert prompt.count(URL) == 1
        assert "first" in prompt and "second" in prompt
        assert "What is the exit load?" in prompt

    def test_compose_context_contains_no_question(self) -> None:
        context = compose_context([_hit("Expense ratio: 0.78%")])
        assert "Expense ratio: 0.78%" in context


class TestAnswerQuestionUnits:
    def test_empty_hits_produce_a_no_answer(self) -> None:
        result = answer_question("What is the exit load?", [])
        assert result.answer_text is None
        assert result.source_url is None

    def test_off_topic_hits_produce_a_no_answer_without_a_citation(self) -> None:
        result = answer_question("What is the exit load?", [_hit("Small Cap. Equity. Very High Risk.")])
        assert result.answer_text is None
        assert result.source_url is None

    def test_answer_body_carries_no_url(self) -> None:
        result = answer_question("What is the exit load?", [_hit("Exit load of 1% if redeemed within 1 year")])
        assert result.answer_text is not None
        assert "http" not in result.answer_text
        assert result.source_url == URL

    def test_metadata_comes_from_the_primary_hit(self) -> None:
        result = answer_question("What is the exit load?", [_hit("Exit load of 1%")])
        assert result.scheme_id == "hdfc-large-cap-direct-growth"
        assert result.section == "test"
        assert result.fetched_at == "2026-09-27"
        assert result.generation_mode == config.GENERATION_MODE

    def test_ambiguity_is_reported_on_the_answer(self) -> None:
        hits = [_hit("Exit load of 1%", 0.900, URL), _hit("Exit load of 2%", 0.899, OTHER_URL, "hdfc-elss-tax-saver-direct-growth")]
        result = answer_question("What is the exit load?", hits)
        assert result.ambiguous_source is True
        assert result.source_url.count("http") == 1

    def test_a_url_in_the_source_text_is_stripped_from_the_body(self) -> None:
        text = "Exit load of 1% if redeemed within 1 year. See http://www.hdfcfund.com for details."
        result = answer_question("What is the exit load?", [_hit(text)])
        assert "hdfcfund.com" not in (result.answer_text or "")

    def test_generation_mode_llm_uses_the_llm_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "GENERATION_MODE", "llm")
        calls: list[str] = []

        class Working:
            def complete(self, **_: object) -> str:
                calls.append("called")
                return "Expense ratio: 0.78%."

        monkeypatch.setattr(answer_module, "_build_client", lambda: Working())
        result = answer_question("What is the expense ratio?", [_hit("Expense ratio: 0.78%")])
        assert calls == ["called"]
        assert result.answer_text == "Expense ratio: 0.78%."


class TestNoAnswerMessage:
    def test_starts_with_the_headline(self) -> None:
        assert no_answer_message([]).startswith(NO_ANSWER_HEADLINE)

    def test_names_the_detected_scheme_and_its_page(self) -> None:
        message = no_answer_message([], detected_scheme_id="hdfc-large-cap-direct-growth")
        assert "HDFC Large Cap Fund" in message
        assert URL in message

    def test_lists_the_five_covered_schemes(self) -> None:
        message = no_answer_message([])
        for name in ("HDFC Large Cap Fund", "HDFC Equity (Flexi Cap) Fund", "HDFC Balanced Advantage Fund", "HDFC Small Cap Fund"):
            assert name in message

    def test_contains_no_numbers_of_its_own(self) -> None:
        """Only the count of covered schemes may appear."""
        message = no_answer_message([])
        assert set(re.findall(r"\d+", message)) <= {"5"}

    def test_an_allowlisted_url_is_the_only_url(self) -> None:
        message = no_answer_message([], detected_scheme_id="hdfc-large-cap-direct-growth")
        assert re.findall(r"https?://\S+", message) == [URL]

    def test_an_unknown_scheme_is_not_invented(self) -> None:
        message = no_answer_message([], detected_scheme_id="mirae-asset-large-cap")
        assert "Mirae" not in message

    def test_coverage_dates_come_from_the_corpus(self) -> None:
        coverage = covered_schemes_with_dates()
        assert len(coverage) == 5
        assert all(date == "2026-09-27" for _, date in coverage)


@pytest.fixture(scope="module", autouse=True)
def built_store() -> None:
    if not store_module.collection_exists():
        pytest.skip("no vector store; run `make ingest` first")


PLAN_QUESTIONS = [
    "What is the exit load of HDFC Large Cap?",
    "What is the minimum SIP amount?",
    "What is the lock in period for ELSS?",
    "What is the expense ratio of HDFC Small Cap?",
]


class TestAgainstTheRealCorpus:
    @pytest.mark.parametrize("question", PLAN_QUESTIONS)
    def test_plan_questions_produce_one_cited_answer(self, question: str) -> None:
        answer = answer_question(question, retrieve(question).hits)
        assert answer.answer_text is not None
        assert answer.source_url in _allowlist_urls()
        assert answer.answer_text.count("http") == 0

    @pytest.mark.parametrize("question", PLAN_QUESTIONS)
    def test_no_answer_exceeds_three_units(self, question: str) -> None:
        answer = answer_question(question, retrieve(question).hits)
        assert len(split_units(answer.answer_text or "")) <= config.MAX_ANSWER_SENTENCES

    @pytest.mark.parametrize("question", PLAN_QUESTIONS)
    def test_no_number_is_invented(self, question: str) -> None:
        """Every digit-group in the answer must exist in a retrieved chunk."""
        result = retrieve(question)
        answer = answer_question(question, result.hits)
        corpus_text = " ".join(hit.chunk.text for hit in result.hits)
        for number in re.findall(r"[\d][\d,.]*", answer.answer_text or ""):
            assert number in corpus_text, f"{number!r} is not in the retrieved text"

    def test_off_corpus_questions_refuse(self) -> None:
        for question in ("Who is the CEO of HDFC AMC?", "How do I contact customer support?"):
            result = retrieve(question)
            assert result.hits == []
            assert answer_question(question, result.hits).answer_text is None

    def test_template_mode_needs_no_api_key_and_no_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "GENERATION_MODE", "template")
        monkeypatch.setenv("LLM_API_KEY", "")

        def _no_network(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("template mode must not touch the network")

        monkeypatch.setattr(answer_module, "_build_client", _no_network)
        answer = answer_question(PLAN_QUESTIONS[0], retrieve(PLAN_QUESTIONS[0]).hits)
        assert answer.answer_text is not None

    def test_the_same_question_is_answered_identically_twice(self) -> None:
        question = PLAN_QUESTIONS[1]
        hits = retrieve(question).hits
        assert answer_question(question, hits).answer_text == answer_question(
            question, hits
        ).answer_text


def _allowlist_urls() -> set[str]:
    from src.ingest.load import load_sources

    return {ref.source_url for ref in load_sources()}
