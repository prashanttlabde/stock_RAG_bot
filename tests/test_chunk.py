"""Phase 2 chunking tests: section fidelity, deterministic ids, and the size fallback.

The assertions are written against the real committed corpus, because the point
of Phase 2 is that the chunker matches the structure the corpus actually has.
"""

from __future__ import annotations

import json
from itertools import pairwise

import pytest

from src import config
from src.ingest.chunk import (
    chunk_corpus,
    chunk_document,
    estimate_tokens,
    main,
    section_slug,
    segment_sections,
    split_fallback,
    summarize,
    uses_markdown_headings,
    write_chunk_stats,
)
from src.ingest.load import load_corpus, load_sources
from src.types import Chunk, SourceDoc

EXPECTED_SECTIONS = [
    "Minimum investments",
    "Understand terms",
    "Exit load, stamp duty and tax",
    "Fund house",
]


def _corpus_chunks() -> list[Chunk]:
    return chunk_corpus(load_corpus())


def _non_space_len(text: str) -> int:
    return len("".join(text.split()))


@pytest.fixture(scope="module")
def chunks() -> list[Chunk]:
    return _corpus_chunks()


def test_corpus_produces_thirty_chunks_in_six_sections(chunks: list[Chunk]) -> None:
    assert len(chunks) == 30
    for scheme_id in {chunk.scheme_id for chunk in chunks}:
        sections = [chunk.section for chunk in chunks if chunk.scheme_id == scheme_id]
        assert len(sections) == 6
        assert all(sections)


def test_every_chunk_is_fully_populated(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        assert chunk.chunk_id
        assert chunk.scheme_id
        assert chunk.source_url
        assert chunk.text.strip()
        assert chunk.fetched_at


def test_every_chunk_url_is_on_the_allowlist(chunks: list[Chunk]) -> None:
    allowlist = {ref.source_url for ref in load_sources()}
    for chunk in chunks:
        assert chunk.source_url in allowlist


def test_no_chunk_is_a_bare_heading_or_a_stub(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        assert _non_space_len(chunk.text) >= config.MIN_CHUNK_CHARS
        assert chunk.text != chunk.section


def test_chunk_ids_are_unique_and_deterministic(chunks: list[Chunk]) -> None:
    ids = [chunk.chunk_id for chunk in chunks]
    assert len(set(ids)) == len(ids)
    assert [c.chunk_id for c in _corpus_chunks()] == ids


def test_chunk_ids_have_the_documented_shape(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        scheme_id, slug, index = chunk.chunk_id.split("::")
        assert scheme_id == chunk.scheme_id
        assert slug and slug == slug.lower()
        assert index.isdigit() and len(index) == 3


def test_no_chunk_repeats_its_own_heading(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        lines = chunk.text.splitlines()
        assert lines[0] == chunk.section
        assert lines[1] != chunk.section


def test_each_chunk_carries_its_section_label_into_the_text(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        assert chunk.text.startswith(f"{chunk.section}\n")


def test_the_expected_fact_sections_all_survive(chunks: list[Chunk]) -> None:
    for scheme_id in {chunk.scheme_id for chunk in chunks}:
        names = [chunk.section for chunk in chunks if chunk.scheme_id == scheme_id]
        for expected in EXPECTED_SECTIONS:
            assert expected in names


def test_chunks_never_carry_holdings_or_return_tables(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        low = chunk.text.lower()
        assert "holdings (" not in low
        assert "1 year:" not in low
        assert "icici bank" not in low


def test_no_chunk_exceeds_the_configured_target(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        assert estimate_tokens(chunk.text) <= config.CHUNK_TARGET_TOKENS


def test_uses_markdown_headings_on_the_real_corpus() -> None:
    assert all(uses_markdown_headings(doc.raw_text) for doc in load_corpus())


def test_segment_sections_finds_the_six_real_sections() -> None:
    doc = next(d for d in load_corpus() if d.scheme_id == "hdfc-large-cap-direct-growth")
    names = [name for name, _ in segment_sections(doc.raw_text) if name]
    assert len(names) == 6
    assert names[0] == "HDFC Large Cap Fund Direct Growth"
    assert names[-1] == "Fund house"
    assert "About HDFC Large Cap Fund Direct Growth" in names


def test_segment_sections_skips_the_document_title() -> None:
    sections = segment_sections("# My Scheme\n\n## Facts\nExpense ratio: 1.03%")
    assert sections == [("Facts", "Expense ratio: 1.03%")]


def test_segment_sections_keeps_a_keyword_heading_body_together() -> None:
    body = "## Understand terms\nExpense ratio\nA fee payable to an AMC.\nTax\nCapital gains tax."
    assert segment_sections(body) == [
        ("Understand terms", "Expense ratio\nA fee payable to an AMC.\nTax\nCapital gains tax.")
    ]


def test_a_glossary_term_is_not_promoted_inside_a_markdown_section() -> None:
    body = "## Understand terms\nExpense ratio\nA fee payable to an AMC.\nTax\nCapital gains tax."
    names = [name for name, _ in segment_sections(body) if name]
    assert names == ["Understand terms"]


def test_segment_sections_promotes_keyword_headings_without_markdown() -> None:
    body = "Expense ratio\nA fee payable to an AMC.\nExit load\nA fee for exiting early."
    names = [name for name, _ in segment_sections(body)]
    assert names == ["Expense ratio", "Exit load"]


def test_a_label_value_line_is_never_a_heading() -> None:
    body = "Expense ratio: 1.03%\nExit load: 1%\nBenchmark: NIFTY 100 Total Return Index"
    assert segment_sections(body) == [(None, body)]


def test_segment_sections_returns_a_preamble_before_the_first_heading() -> None:
    body = "Intro sentence.\n## Facts\nExpense ratio: 1.03%"
    sections = segment_sections(body)
    assert sections[0] == (None, "Intro sentence.")
    assert sections[1] == ("Facts", "Expense ratio: 1.03%")


def _flat_doc(body: str) -> SourceDoc:
    return SourceDoc(
        scheme_id="hdfc-test-direct-growth",
        scheme_name="Test Fund",
        category="large_cap",
        source_url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        fetched_at="2026-09-27",
        raw_text=body,
    )


def test_a_body_with_no_headings_falls_back_to_size_packing() -> None:
    body = "\n".join(
        f"The minimum investment for this scheme is {n} rupees per instalment." for n in range(1, 40)
    )
    chunks = chunk_document(_flat_doc(body))
    assert chunks
    assert all(chunk.section is None for chunk in chunks)
    assert all(chunk.chunk_id.split("::")[1] == "preamble" for chunk in chunks)


def test_fewer_than_three_headings_uses_the_fallback_path() -> None:
    body = (
        "## Exit load\nExit load: 1% if redeemed within 1 year\n"
        "## Benchmark\nFund benchmark: NIFTY 100 Total Return Index\n"
    )
    assert (
        len([n for n, _ in segment_sections(body) if n]) < config.MIN_SECTIONS_FOR_STRUCTURED_CHUNKING
    )
    chunks = chunk_document(_flat_doc(body))
    assert [chunk.section for chunk in chunks] == ["Exit load", "Benchmark"]
    stats = summarize([_flat_doc(body)], chunks)
    assert stats["chunking_strategy"]["hdfc-test-direct-growth"] == "fallback"


def test_a_chunk_under_the_character_floor_is_dropped() -> None:
    assert chunk_document(_flat_doc("## Exit load\nExit load: 1%")) == []
    assert chunk_document(_flat_doc("## Exit load\nExit load: 1% if redeemed within 1 year"))


def test_an_oversized_section_is_split_further() -> None:
    long_body = "\n".join(
        f"The minimum investment for this scheme is {n * 7} rupees per instalment." for n in range(120)
    )
    chunks = chunk_document(_flat_doc(f"## Minimum investments\n{long_body}"))
    assert len(chunks) > 1
    assert all(chunk.section == "Minimum investments" for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)


def test_chunks_carry_the_parent_document_metadata() -> None:
    doc = _flat_doc("## Exit load\nExit load: 1% if redeemed within 1 year")
    chunk = chunk_document(doc)[0]
    assert chunk.scheme_id == doc.scheme_id
    assert chunk.source_url == doc.source_url
    assert chunk.fetched_at == doc.fetched_at


def test_chunk_metadata_never_contains_none() -> None:
    chunk = chunk_document(_flat_doc("Some plain fact line with no heading at all here."))[0]
    metadata = chunk.to_metadata()
    assert all(value is not None for value in metadata.values())
    assert metadata["section"] == ""
    assert set(metadata) == {"chunk_id", "scheme_id", "source_url", "section", "fetched_at"}
    assert metadata["chunk_id"] == chunk.chunk_id


def test_split_fallback_packs_to_the_target() -> None:
    body = " ".join(f"This is sentence {n} in the body." for n in range(40))
    pieces = split_fallback(body, 40, 0.0)
    assert len(pieces) > 1
    assert all(estimate_tokens(piece) <= 40 + estimate_tokens("This is sentence 0.") for piece in pieces)


def test_split_fallback_overlaps_consecutive_pieces() -> None:
    sentences = [f"Sentence {n} carries marker{n}." for n in range(20)]
    pieces = split_fallback(" ".join(sentences), 60, 0.15)
    assert len(pieces) > 1
    for earlier, later in pairwise(pieces):
        assert set(earlier.splitlines()) & set(later.splitlines())


def test_split_fallback_without_overlap_repeats_nothing() -> None:
    sentences = [f"Sentence {n} carries marker{n}." for n in range(20)]
    pieces = split_fallback(" ".join(sentences), 60, 0.0)
    for earlier, later in pairwise(pieces):
        assert not set(earlier.splitlines()) & set(later.splitlines())


def test_split_fallback_keeps_an_oversized_sentence_intact() -> None:
    body = "word " * 400 + "end."
    pieces = split_fallback(body, 50, 0.0)
    assert len(pieces) == 1
    assert pieces[0].endswith("end.")


def test_split_fallback_never_loses_content() -> None:
    sentences = [f"Fact {n} is recorded here." for n in range(30)]
    pieces = split_fallback(" ".join(sentences), 50, 0.2)
    joined = " ".join(pieces)
    for n in range(30):
        assert f"Fact {n} is recorded here." in joined


def test_split_fallback_on_empty_text() -> None:
    assert split_fallback("", 100, 0.15) == []
    assert split_fallback("   \n  ", 100, 0.15) == []


def test_estimate_tokens_uses_the_documented_proxy() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("one two three four") == round(4 / 0.75)
    assert estimate_tokens("word") == 1


def test_section_slug_is_stable_and_safe() -> None:
    assert section_slug(None) == "preamble"
    assert section_slug("Exit load, stamp duty and tax") == "exit-load-stamp-duty-and-tax"
    assert section_slug("About HDFC ELSS Tax Saver Fund") == "about-hdfc-elss-tax-saver-fund"
    assert section_slug("...") == "preamble"


def test_summarize_reports_the_structure_and_constants(chunks: list[Chunk]) -> None:
    stats = summarize(load_corpus(), chunks)
    assert stats["docs"] == 5
    assert stats["chunks"] == 30
    assert stats["min_tokens"] > 0
    assert stats["min_tokens"] <= stats["avg_tokens"] <= stats["max_tokens"]
    assert set(stats["sections_found"]) == {ref.scheme_id for ref in load_sources()}
    assert set(stats["chunking_strategy"].values()) == {"section"}
    assert stats["constants"]["CHUNK_TARGET_TOKENS"] == config.CHUNK_TARGET_TOKENS
    assert stats["constants"]["CHUNK_OVERLAP_RATIO"] == config.CHUNK_OVERLAP_RATIO


def test_write_chunk_stats_produces_valid_json(tmp_path) -> None:
    target = tmp_path / "chunk_stats.json"
    write_chunk_stats(summarize(load_corpus(), _corpus_chunks()), target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["chunks"] == 30
    assert payload["docs"] == 5


def test_cli_writes_the_stats_file(monkeypatch, tmp_path, capsys) -> None:
    target = tmp_path / "chunk_stats.json"
    monkeypatch.setattr(config, "CHUNK_STATS_FILE", target)
    assert main([]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["chunks"] == 30
    assert "30 chunks from 5 docs" in capsys.readouterr().out


def test_cli_can_skip_writing_stats(monkeypatch, tmp_path) -> None:
    target = tmp_path / "chunk_stats.json"
    monkeypatch.setattr(config, "CHUNK_STATS_FILE", target)
    assert main(["--no-stats"]) == 0
    assert not target.exists()
