"""Phase 1 corpus tests: allowlist, cleaning, coverage, and the offline read path.

The network-touching parts of ingestion are not re-run here. These tests
exercise the pure cleaning and parsing logic against the committed corpus, plus
the invariants that keep citations inside the allowlist.
"""

from __future__ import annotations

import re

import pytest

from src import config
from src.errors import CorpusError
from src.ingest.load import (
    FACT_LABELS,
    _base_label,
    _clean_lines,
    _is_pairable,
    _keep_section,
    clean_text,
    extract_text,
    load_corpus,
    load_sources,
    required_anchors,
    validate_fact_coverage,
    write_manifest,
)
from src.types import SourceDoc

EXPECTED_SCHEME_IDS = {
    "hdfc-large-cap-direct-growth",
    "hdfc-equity-flexi-cap-direct-growth",
    "hdfc-elss-tax-saver-direct-growth",
    "hdfc-small-cap-direct-growth",
    "hdfc-balanced-advantage-direct-growth",
}


def test_allowlist_has_the_five_prd_schemes() -> None:
    refs = load_sources()
    assert {ref.scheme_id for ref in refs} == EXPECTED_SCHEME_IDS
    assert all(ref.source_url.startswith("https://groww.in/mutual-funds/") for ref in refs)
    assert len({ref.source_url for ref in refs}) == 5


def test_load_corpus_is_offline_and_complete() -> None:
    docs = load_corpus()
    assert {doc.scheme_id for doc in docs} == EXPECTED_SCHEME_IDS
    for doc in docs:
        assert doc.scheme_name and doc.category and doc.fetched_at
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc.fetched_at)
        assert doc.file_path is not None and doc.file_path.exists()
        assert len(doc.raw_text) > 500


def test_every_corpus_url_is_on_the_allowlist() -> None:
    allowlist = {ref.source_url for ref in load_sources()}
    for doc in load_corpus():
        assert doc.source_url in allowlist


def test_corpus_covers_every_required_fact_anchor() -> None:
    for doc in load_corpus():
        report = validate_fact_coverage(doc.scheme_id, doc.raw_text, doc.category)
        missing = [anchor for anchor, present in report.items() if not present]
        assert not missing, f"{doc.scheme_id} is missing {missing}"


def test_elss_corpus_states_a_lock_in() -> None:
    elss = next(d for d in load_corpus() if d.category == "elss")
    assert re.search(r"lock[-\s]?in", elss.raw_text, re.IGNORECASE)
    assert "lock_in" in required_anchors("elss")
    assert "lock_in" not in required_anchors("large_cap")


def test_corpus_has_no_holdings_or_return_tables() -> None:
    for doc in load_corpus():
        low = doc.raw_text.lower()
        assert "holdings (" not in low
        assert "compare similar funds" not in low
        assert "icici bank ltd" not in low


def test_corpus_uses_section_headings_for_the_chunker() -> None:
    for doc in load_corpus():
        headings = re.findall(r"^## (.+)$", doc.raw_text, re.MULTILINE)
        assert len(headings) >= 4
        assert any("exit load" in h.lower() for h in headings)
        assert any("benchmark" in doc.raw_text for _ in [0])


def test_label_value_pairing_is_conservative() -> None:
    assert _is_pairable("Expense ratio", "1.03%")
    assert _is_pairable("NAV: 25 Sep '26", "₹1,189.08")
    assert _is_pairable("Stamp duty on investment:", "0.005% (from July 1st, 2020)")
    assert not _is_pairable("Exit load of 1% if redeemed within 1 year", "16 Feb 2015")
    assert not _is_pairable("Minimum investments", "Min. for 1st investment")
    assert not _is_pairable("Rating", "")
    assert not _is_pairable("Rating", "## Minimum investments")
    assert not _is_pairable("Rating", "Min. for SIP: ₹100")


def test_a_long_value_is_left_unjoined_rather_than_swallowed() -> None:
    description = (
        "Exit Load for units in excess of 15% of the investment,1% will be charged for "
        "redemption within 1 year."
    )
    assert len(description) > 60
    assert not _is_pairable("Exit load", description)
    out = _clean_lines(f"Exit load\n{description}")
    assert out == ["Exit load", description]


def test_pairing_never_chains_or_doubles_a_colon() -> None:
    out = _clean_lines("Rating\n4\n\n## Minimum investments\n\nMin. for SIP\n₹100")
    assert "Rating: 4" in out
    assert "Min. for SIP: ₹100" in out
    assert not any(line.endswith(":") for line in out)
    assert not any(line.count(":") > 1 and "NAV" not in line for line in out)


def test_clean_text_is_idempotent() -> None:
    for doc in load_corpus():
        once = clean_text(doc.raw_text)
        assert clean_text(once) == once, f"{doc.scheme_id} is not idempotent under cleaning"


def test_clean_text_strips_chrome_and_zero_width() -> None:
    out = _clean_lines("Check past data\nA\n\n   \nStocks\nMin. for SIP\n₹100")
    assert out == ["Min. for SIP: ₹100"]


def test_hero_block_drops_orphaned_return_figures() -> None:
    out = _clean_lines("+8.71\n%\n3Y annualised\n1D\nAll\nVery High Risk", drop_numeric_residue=True)
    assert out == ["Very High Risk"]


def test_keep_section_rejects_empty_and_returns_sections() -> None:
    assert _keep_section("Minimum investments", ["Min. for SIP: ₹100"])
    assert not _keep_section("Minimum investments", [])
    assert not _keep_section("Holdings ( 50 )", ["ICICI Bank Ltd: Financial"])
    assert not _keep_section("Returns and rankings", ["1 year: 8.71%"])
    assert not _keep_section("Understand terms", ["Annualised returns", "Average of yearly returns"])
    assert _keep_section("Understand terms", ["Expense ratio", "A fee payable to an AMC."])


def test_validate_fact_coverage_raises_when_page_is_hollow() -> None:
    with pytest.raises(CorpusError, match="fact anchors missing"):
        validate_fact_coverage("hdfc-x", "Buy stocks. Trade with us.", "large_cap")


def test_validate_fact_coverage_tolerates_one_missing_anchor() -> None:
    text = (
        "Expense ratio: 1.03%\nExit load: 1% if redeemed within 1 year\n"
        "Min. for SIP: ₹100\nIt is rated Very High risk.\n"
        "Taxation is categorized as long term capital gains."
    )
    report = validate_fact_coverage("hdfc-x", text, "large_cap")
    assert report["expense_ratio"] and report["exit_load"] and report["risk_rating"]
    assert not report["benchmark"]


def test_extract_text_recovers_facts_from_a_minimal_page() -> None:
    html = """
    <html><body><div class="container web-align">
      <h1>HDFC Flexi Cap Direct Plan Growth</h1><p>Very High Risk</p>
      <p>Min. for SIP</p><p>&#8377;100</p>
      <p>Expense ratio</p><p>0.77%</p>
      <h2>Minimum investments</h2><p>Min. for 1st investment</p><p>&#8377;100</p>
      <h2>Exit load, stamp duty and tax</h2><p>Exit load</p><p>Exit load of 1% if redeemed within 1 year</p>
      <h2>About HDFC Flexi Cap Direct Plan Growth</h2>
      <p>It is rated Very High risk. Minimum SIP Investment is set to &#8377;100.</p>
      <p>Fund benchmark: NIFTY 500 Total Return Index</p>
      <p>Taxation is categorized as long term capital gains.</p>
      <h2>Holdings ( 50 )</h2><p>ICICI Bank Ltd</p>
      <h2>Returns and rankings</h2><p>1 year: 9.1%</p>
    </div></body></html>
    """
    text = extract_text(html, "flexi_cap")
    assert "Expense ratio: 0.77%" in text
    assert "Exit load: Exit load of 1% if redeemed within 1 year" in text
    assert "Fund benchmark: NIFTY 500 Total Return Index" in text
    assert "is rated Very High risk" in text
    assert "ICICI Bank Ltd" not in text
    assert "9.1%" not in text
    assert validate_fact_coverage("hdfc-flexi", text, "flexi_cap")["exit_load"]


def test_extract_text_raises_when_nothing_is_recoverable() -> None:
    with pytest.raises(CorpusError, match="No extraction strategy"):
        extract_text("<html><body><p>Nothing useful here.</p></body></html>", "large_cap")


def test_write_manifest_lists_every_source(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "SOURCES_MANIFEST", tmp_path / "sources.md")
    write_manifest(load_corpus())
    text = (tmp_path / "sources.md").read_text(encoding="utf-8")
    for ref in load_sources():
        assert ref.source_url in text
        assert ref.scheme_name in text


def test_parse_rejects_a_file_without_front_matter(tmp_path) -> None:
    from src.ingest.load import _parse_corpus_file

    path = tmp_path / "broken.md"
    path.write_text("no front matter here", encoding="utf-8")
    with pytest.raises(CorpusError, match="no YAML front matter"):
        _parse_corpus_file(path)


def test_load_corpus_rejects_a_non_allowlist_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "CORPUS_DIR", tmp_path)
    (tmp_path / "rogue.md").write_text(
        "---\nscheme_id: rogue\nscheme_name: Rogue\ncategory: large_cap\n"
        "source_url: https://example.com/rogue\nfetched_at: '2026-09-27'\n---\n\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(CorpusError, match="outside the allowlist"):
        load_corpus()


def test_source_doc_round_trips_through_the_corpus_file() -> None:
    doc = load_corpus()[0]
    assert isinstance(doc, SourceDoc)
    assert doc.scheme_id in doc.file_path.name


def test_fact_labels_never_match_a_prose_sentence() -> None:
    prose = "The scheme charges an expense ratio of 1.03% every year."
    assert not FACT_LABELS.match(_base_label(prose))
