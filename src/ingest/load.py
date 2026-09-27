"""Ingestion stages 1-2: load and clean the 5-URL corpus (architecture.md §6).

Pipeline for one scheme: fetch the public page -> extract the fact-bearing
sections -> clean them -> validate fact coverage -> write
`data/corpus/<scheme_id>.md`.

Extraction is DOM-section-aware rather than a whole-page dump. Groww renders the
scheme page as a stack of h2/h3 sections, and a flat text scrape of the whole
page is ~90% holdings rows, return tables, and fund-comparison tables that would
bury the handful of facts a FAQ user asks about. So `_extract_sections` walks the
page in document order, keeps only the sections that carry scheme facts, and
emits them as `## Heading` blocks. That gives the chunker real headings to split
on (architecture.md §6 chunking decision rule) and keeps the corpus auditable by
a human, which is what makes the one-citation promise defensible.

`trafilatura` is still consulted first, as the plan specifies, but the winner is
chosen by fact-anchor coverage rather than length: on the current layout
trafilatura returns only the returns and holdings tables and drops every scheme
fact, so the main-container walk wins and trafilatura becomes a fallback that
takes over if the page structure ever changes.

Manual fallback (PRD §15, scraping-block risk): if a fetch or a coverage check
fails, paste the cleaned public text into `data/corpus/<scheme_id>.md` by hand
with this front matter and set `fetched_at` to the date you saved it. Nothing
downstream fetches anything, so a hand-written file is a first-class corpus
document.

    ---
    scheme_id: hdfc-large-cap-direct-growth
    scheme_name: HDFC Large Cap Fund – Direct Growth
    category: large_cap
    source_url: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth
    fetched_at: 2026-09-27
    ---

    # HDFC Large Cap Fund – Direct Growth

    ## Minimum investments

    Min. for 1st investment: ₹100
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Final

import requests
import yaml
from bs4 import BeautifulSoup, NavigableString, Tag

from src import config
from src.errors import CorpusError
from src.logging_config import get_logger
from src.types import SourceDoc, SourceRef

logger = get_logger("ingest.load")

FRONT_MATTER_DELIM: Final = "---"
SECTION_HEADING_LEVELS: Final = r"^h[1-3]$"
DROP_TAGS: Final = ("script", "style", "noscript", "svg", "template", "iframe")

MAIN_CONTENT_SELECTORS: Final = (
    "div.container.web-align",
    "div.pw14ContentWrapper",
    "main",
    "article",
)

DROP_SECTION_TITLE: Final = re.compile(
    r"^holdings\b"
    r"|^return calculator$"
    r"|^returns and rankings$"
    r"|^compare similar funds$"
    r"|^fund management$"
    r"|view details"
    r"|^exit load$",
    re.IGNORECASE,
)

DROP_SECTION_LINE: Final = re.compile(r"^annualised returns$|^absolute returns$", re.IGNORECASE)

HERO_SECTION_TITLE: Final = re.compile(r"^hdfc\b.*fund", re.IGNORECASE)

HERO_ARTIFACT_LINES: Final = re.compile(
    r"^(?:\d+[DMY]|all|%\+?|\+?[\d,]+(?:\.\d+)?%?|\d+y annualised|1d|1m|6m|1y|3y|5y)$",
    re.IGNORECASE,
)

NOISE_LINES: Final = (
    r"^(check past data|view details|others|all|stocks|indices|ipo|fno|mtfs|pledge|terminal)$",
    r"^(in|out|deliverables?)$",
    r"^[\W_]+$",
)

FACT_ANCHORS: Final[dict[str, str]] = {
    "expense_ratio": r"expense\s*ratio",
    "exit_load": r"exit\s*load",
    "minimum_investment": r"min\.?\s*for\s*(?:1st|2nd|sip|lump)|minimum\s*(?:sip|investment|lumpsum)",
    "benchmark": r"benchmark",
    "risk_rating": r"is\s+rated\s+[\w\s]{1,24}?risk|riskometer",
    "tax_guidance": r"capital\s*gains|\btax\b|taxed",
}

ELSS_FACT_ANCHORS: Final[dict[str, str]] = {"lock_in": r"lock[-\s]?in"}

FACT_LABELS: Final = re.compile(
    r"^expense\s*ratio$"
    r"|^fund\s*benchmark$"
    r"|^min\.?\s*for\s*(?:1st|2nd|sip|lump)"
    r"|^minimum\s*(?:sip|lumpsum|investment)"
    r"|^fund\s*size\s*\(AUM\)$"
    r"|^AUM$"
    r"|^NAV\b"
    r"|^rating$"
    r"|^exit\s*load$"
    r"|^tax$"
    r"|^stamp duty on investment:?$",
    re.IGNORECASE,
)

SECTION_MARKER: Final = "## "
PREFORMATTED_LABELS: Final = frozenset({"nav", "aum"})
MAX_LABEL_CHARS: Final = 60
LABEL_TERMINAL_PUNCT: Final = re.compile(r"[.!?;,:]$")
VALUE_LIKE: Final = re.compile(r"^[\s\d.,%₹$+\-/()]*$")


def load_sources(sources_file: Path | None = None) -> list[SourceRef]:
    """Read the allowlist from `data/sources.yaml`. Never touches the network."""
    path = sources_file or config.SOURCES_FILE
    if not path.exists():
        raise CorpusError(f"Source allowlist not found at {path}. It is the citation contract.")

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = payload.get("sources") or []
    if not entries:
        raise CorpusError(f"No `sources:` entries found in {path}.")

    required = {"scheme_id", "scheme_name", "category", "source_url"}
    refs: list[SourceRef] = []
    seen: set[str] = set()
    for entry in entries:
        missing = required - set(entry)
        if missing:
            raise CorpusError(f"Entry {entry!r} in {path} is missing {sorted(missing)}.")
        ref = SourceRef(**{key: entry[key] for key in required})
        if ref.scheme_id in seen:
            raise CorpusError(f"Duplicate scheme_id {ref.scheme_id!r} in {path}.")
        seen.add(ref.scheme_id)
        refs.append(ref)

    logger.info("loaded %d sources from %s", len(refs), path.name)
    return refs


def fetch_page(url: str) -> str:
    """Fetch one public page and return its HTML.

    Browser-like User-Agent, 30s timeout, one retry. Raises `CorpusError` on
    failure so the caller can fall back to a manually saved corpus file.
    """
    last_error: Exception | None = None
    for attempt in range(config.FETCH_RETRIES + 1):
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": config.USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-IN,en;q=0.9",
                },
                timeout=config.FETCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("fetch failed for %s (attempt %d): %s", url, attempt + 1, exc)
    raise CorpusError(f"Could not fetch {url}: {last_error}")


def required_anchors(category: str) -> dict[str, str]:
    """Fact anchors a scheme must satisfy; ELSS additionally needs a lock-in."""
    anchors = dict(FACT_ANCHORS)
    if category == "elss":
        anchors.update(ELSS_FACT_ANCHORS)
    return anchors


def _is_noise_line(line: str) -> bool:
    """True for nav chrome, footer alphabet rows, and separator glyphs."""
    stripped = line.strip()
    if not stripped:
        return True
    if len(stripped) == 1 and stripped.isalpha():
        return True
    return any(re.match(pattern, stripped, re.IGNORECASE) for pattern in NOISE_LINES)


def _normalize(text: str) -> str:
    """NFKC-normalize and strip zero-width, bidi, and bullet glyphs."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"[-‏‪-‮⁠﻿\xa0]", " ", normalized)
    return normalized.replace("•", " ").replace("|", " ")


def _base_label(line: str) -> str:
    """The label part of a line, i.e. everything before the first colon.

    The page renders "NAV: 25 Sep '26" as one node, so matching on the text
    before the colon is what lets it pair with its value on the next line.
    """
    return line.split(":", 1)[0].strip()


def _pairable_label(line: str) -> str | None:
    """Return the label text if this line may own the next line's value.

    A line that already carries a joined value is refused, which is what keeps
    the pass idempotent. Two labels are exempt because the page ships them with
    their own colon, and only while they still hold exactly one: "NAV: 25 Sep
    '26" may pair, "NAV: 25 Sep '26: ₹1,189.08" already has its value and may
    not.
    """
    candidate = line.rstrip(":").strip()
    if ":" in candidate:
        if candidate.count(":") != 1 or _base_label(candidate).lower() not in PREFORMATTED_LABELS:
            return None
        candidate = _base_label(candidate)
    return candidate if FACT_LABELS.match(candidate) else None


def _is_pairable(label: str, value: str | None) -> bool:
    """True when `label` is a known fact label that owns `value` on the next line.

    Deliberately conservative. Only `FACT_LABELS` may own a value, the value
    must be non-empty and must not already carry a colon, and it must not itself
    be a label or a heading. A generic "short line followed by short line"
    heuristic mispaired the exit-load date table into "Exit load of 1% if
    redeemed within 1 year: 16 Feb 2015", inventing a relationship the page
    never states.
    """
    if value is None or not value.strip() or _pairable_label(label) is None:
        return False
    if label.startswith(SECTION_MARKER) or value.startswith(SECTION_MARKER):
        return False
    if ":" in value or LABEL_TERMINAL_PUNCT.search(_pairable_label(label) or ""):
        return False
    if FACT_LABELS.match(_base_label(value)) or VALUE_LIKE.match(label):
        return False
    return len(value) <= MAX_LABEL_CHARS


def _join_label_value_pairs(lines: list[str]) -> list[str]:
    """Flatten `label` / `value` line pairs into `label: value` lines.

    Joining never chains more than one value. Each line is examined exactly
    once, so a line already joined in this pass is never re-paired.
    """
    joined: list[str] = []
    index = 0
    while index < len(lines):
        label = lines[index]
        value = lines[index + 1] if index + 1 < len(lines) else None
        if _is_pairable(label, value):
            joined.append(f"{label.rstrip(':')}: {value}")
            index += 2
        else:
            joined.append(label)
            index += 1
    return joined


def _clean_lines(raw: str, *, drop_numeric_residue: bool = False) -> list[str]:
    """Normalize text into self-describing fact lines.

    Label/value pairs are joined before bare numbers are dropped, so
    "Expense ratio" / "1.03%" becomes one kept line while the hero block's
    orphaned return figures ("+8.71", "3Y annualised") become droppable residue.
    Section markers keep a blank line before them so the chunker still sees
    section boundaries after a second normalization pass.
    """
    lines: list[str] = []
    for raw_line in _normalize(raw).splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip(" -–—")
        if not line:
            continue
        if line.startswith(SECTION_MARKER):
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(line)
            continue
        if _is_noise_line(line) or (lines and line == lines[-1]):
            continue
        lines.append(line)

    while lines and lines[0] == "":
        lines.pop(0)

    lines = _join_label_value_pairs(lines)
    if drop_numeric_residue:
        lines = [line for line in lines if not HERO_ARTIFACT_LINES.match(line.strip())]
    return lines


def clean_text(raw: str) -> str:
    """Normalize extracted text into newline-separated, self-describing fact lines."""
    return "\n".join(_clean_lines(raw))


def _soup_of(html: str) -> BeautifulSoup:
    """Parse HTML and drop tags that never hold user-visible scheme facts."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    return soup


def _walk_sections(container: Tag) -> list[tuple[str, list[str]]]:
    """Split a container into `(heading, lines)` sections in document order.

    A flat walk over descendants is used rather than sibling traversal because
    the collapsible sections nest their body inside the accordion wrapper, so
    sibling-based section detection returns empty bodies for them.
    """
    boundary = re.compile(SECTION_HEADING_LEVELS)
    sections: list[tuple[str, list[str]]] = [("(header)", [])]
    for node in container.descendants:
        if isinstance(node, Tag):
            if boundary.match(node.name or ""):
                sections.append((node.get_text(" ", strip=True) or "(untitled)", []))
        elif isinstance(node, NavigableString):
            text = str(node).strip()
            if text:
                sections[-1][1].append(text)
    return sections


def _keep_section(title: str, lines: list[str]) -> bool:
    """Decide whether a section carries scheme facts worth indexing."""
    if not lines:
        return False
    if DROP_SECTION_TITLE.search(title):
        return False
    return not any(DROP_SECTION_LINE.match(line) for line in lines)


def _sections_text(html: str) -> str:
    """Render the fact-bearing sections of a page as `## Heading` blocks."""
    soup = _soup_of(html)
    container = None
    for selector in MAIN_CONTENT_SELECTORS:
        found = soup.select_one(selector)
        if found is not None:
            container = found
            logger.info("main content matched selector %r", selector)
            break
    if container is None:
        container = soup.body or soup
        logger.warning("no main-content selector matched; falling back to the whole page")

    blocks: list[str] = []
    for title, lines in _walk_sections(container):
        if not _keep_section(title, lines):
            logger.debug("dropping section %r (%d lines)", title, len(lines))
            continue
        is_hero = bool(HERO_SECTION_TITLE.match(title)) or title == "(header)"
        body = _clean_lines("\n".join(lines), drop_numeric_residue=is_hero)
        if not body:
            continue
        heading = "" if title == "(header)" else f"{SECTION_MARKER}{title}\n"
        blocks.append(f"{heading}{chr(10).join(body)}")
        logger.info("kept section %r -> %d lines", title, len(body))

    if not blocks:
        raise CorpusError("Section extraction produced no usable scheme facts.")
    return "\n\n".join(blocks)


def _trafilatura_text(html: str) -> str:
    """Main-content extraction via trafilatura, or an empty string if unavailable."""
    try:
        import trafilatura
    except ImportError:
        logger.warning("trafilatura is not installed; skipping that extraction strategy")
        return ""
    try:
        return trafilatura.extract(
            html, output_format="txt", include_tables=True, include_comments=False
        ) or ""
    except Exception as exc:
        logger.warning("trafilatura extraction failed: %s", exc)
        return ""


def _score(text: str, category: str) -> tuple[int, int]:
    """Return `(anchors covered, sections kept)` for one extraction candidate."""
    low = text.lower()
    covered = sum(1 for pattern in required_anchors(category).values() if re.search(pattern, low))
    return covered, text.count("\n## ") + text.startswith("## ")


def _pick_best_candidate(candidates: dict[str, str], category: str) -> str:
    """Choose the extraction covering the most scheme facts, most sections."""
    best_name, best_text, best_key = "", "", (-1, -1)
    for name, text in candidates.items():
        if not text.strip():
            continue
        covered, sections = _score(text, category)
        logger.info("candidate %r: %d anchors, %d sections", name, covered, sections)
        if (covered, sections) > best_key:
            best_name, best_text, best_key = name, text, (covered, sections)

    if best_key[0] == 0:
        raise CorpusError(
            f"No extraction strategy recovered any scheme facts for category {category!r}."
        )
    logger.info("using extraction %r for category %r", best_name, category)
    return best_text


def extract_text(html: str, category: str = "large_cap") -> str:
    """Extract the scheme's fact sections from a page's HTML, already cleaned.

    Races the section-aware walk against trafilatura and keeps whichever covers
    more scheme facts, so a layout change degrades the corpus rather than
    breaking ingestion. Each candidate is normalized exactly once: re-cleaning
    an already-joined line would pair it a second time with whatever follows it.
    """
    return _pick_best_candidate(
        {
            "trafilatura": clean_text(_trafilatura_text(html)),
            "sections": _sections_text(html),
        },
        category,
    )


def validate_fact_coverage(
    scheme_id: str, text: str, category: str = "large_cap"
) -> dict[str, bool]:
    """Report which scheme facts are present. Raises `CorpusError` if 2+ are missing.

    Two missing anchors means the page was not really retrieved, so the caller
    should use a manual corpus file rather than index a hollow document.
    """
    low = text.lower()
    report = {
        anchor: bool(re.search(pattern, low))
        for anchor, pattern in required_anchors(category).items()
    }
    missing = sorted(anchor for anchor, present in report.items() if not present)
    if len(missing) >= 2:
        raise CorpusError(
            f"{scheme_id}: {len(missing)} fact anchors missing ({', '.join(missing)}). "
            "The page was not retrieved properly — save cleaned text manually into "
            f"{config.CORPUS_DIR / f'{scheme_id}.md'} and re-run."
        )
    logger.info("%s coverage: %d/%d anchors", scheme_id, sum(report.values()), len(report))
    return report


def load_raw(sources: list[SourceRef]) -> dict[str, str]:
    """Fetch, extract, clean, and validate every source, keyed by `scheme_id`."""
    raw_map: dict[str, str] = {}
    for ref in sources:
        logger.info("fetching %s -> %s", ref.scheme_id, ref.source_url)
        text = extract_text(fetch_page(ref.source_url), ref.category)
        validate_fact_coverage(ref.scheme_id, text, ref.category)
        raw_map[ref.scheme_id] = text
    return raw_map


def _render_corpus_file(ref: SourceRef, text: str, fetched_at: str) -> str:
    """Render one corpus document as YAML front matter plus sectioned fact lines."""
    front = yaml.safe_dump(
        {
            "scheme_id": ref.scheme_id,
            "scheme_name": ref.scheme_name,
            "category": ref.category,
            "source_url": ref.source_url,
            "fetched_at": fetched_at,
        },
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    title = f"# {ref.scheme_name}\n\n" if text else ""
    return f"{FRONT_MATTER_DELIM}\n{front}\n{FRONT_MATTER_DELIM}\n\n{title}{text}\n"


def write_corpus(
    sources: list[SourceRef], raw_map: dict[str, str], fetched_at: str | None = None
) -> list[SourceDoc]:
    """Write every corpus file and return the resulting `SourceDoc` records."""
    stamp = fetched_at or date.today().isoformat()
    config.CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    docs: list[SourceDoc] = []
    for ref in sources:
        text = raw_map.get(ref.scheme_id, "")
        path = config.CORPUS_DIR / f"{ref.scheme_id}.md"
        path.write_text(_render_corpus_file(ref, text, stamp), encoding="utf-8")
        logger.info("wrote %s (%d chars)", path.name, len(text))
        docs.append(
            SourceDoc(
                scheme_id=ref.scheme_id,
                scheme_name=ref.scheme_name,
                category=ref.category,
                source_url=ref.source_url,
                fetched_at=stamp,
                file_path=path,
                raw_text=text,
            )
        )
    return docs


def write_manifest(docs: list[SourceDoc]) -> None:
    """Regenerate `data/sources.md`, the human-readable source list (PRD deliverable 2)."""
    lines = [
        "# Source list",
        "",
        "Regenerated by `python -m src.ingest.load`. These 5 URLs are the only",
        "sources the chatbot may cite.",
        "",
        "| Scheme | Category | Source URL | Last updated |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {doc.scheme_name} | `{doc.category}` | {doc.source_url} | {doc.fetched_at} |"
        for doc in docs
    ]
    config.SOURCES_MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("wrote %s", config.SOURCES_MANIFEST.name)


def _parse_corpus_file(path: Path) -> SourceDoc:
    """Parse one `data/corpus/*.md` file into a `SourceDoc`."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith(FRONT_MATTER_DELIM):
        raise CorpusError(f"{path.name} has no YAML front matter.")

    parts = raw.split(FRONT_MATTER_DELIM, 2)
    if len(parts) < 3:
        raise CorpusError(f"{path.name} has an unterminated YAML front matter block.")

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise CorpusError(f"{path.name} has invalid YAML front matter: {exc}") from exc

    required = {"scheme_id", "scheme_name", "category", "source_url", "fetched_at"}
    missing = required - set(meta)
    if missing:
        raise CorpusError(f"{path.name} front matter is missing {sorted(missing)}.")

    return SourceDoc(
        scheme_id=meta["scheme_id"],
        scheme_name=meta["scheme_name"],
        category=meta["category"],
        source_url=meta["source_url"],
        fetched_at=str(meta["fetched_at"]),
        file_path=path,
        raw_text=parts[2].strip(),
    )


def load_corpus() -> list[SourceDoc]:
    """Read every corpus file from disk. This is what all later phases call.

    Offline by design: no network, no cache warming. Raises `CorpusError` when
    the corpus is empty, malformed, or contains a URL outside the allowlist, so
    the UI can show a setup hint instead of a stack trace.
    """
    if not config.CORPUS_DIR.exists():
        raise CorpusError(
            f"Corpus directory {config.CORPUS_DIR} does not exist. "
            "Run `python -m src.ingest.load` first."
        )

    paths = sorted(config.CORPUS_DIR.glob("*.md"))
    if not paths:
        raise CorpusError(
            f"No corpus files in {config.CORPUS_DIR}. Run `python -m src.ingest.load` first."
        )

    docs = [_parse_corpus_file(path) for path in paths]
    allowlist = {ref.source_url for ref in load_sources()}
    strays = sorted({doc.source_url for doc in docs} - allowlist)
    if strays:
        raise CorpusError(f"Corpus contains URLs outside the allowlist: {strays}")

    logger.info("loaded %d corpus documents", len(docs))
    return docs


def main(argv: list[str] | None = None) -> int:
    """CLI: fetch, extract, clean, validate, write the corpus, print a coverage table."""
    parser = argparse.ArgumentParser(description="Load and clean the 5-URL scheme corpus.")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="write what was fetched even if a scheme failed its coverage check",
    )
    args = parser.parse_args(argv)

    sources = load_sources()
    reports: dict[str, dict[str, bool]] = {}
    bodies: dict[str, str] = {}
    failures: list[str] = []

    for ref in sources:
        text = ""
        try:
            text = extract_text(fetch_page(ref.source_url), ref.category)
            reports[ref.scheme_id] = validate_fact_coverage(ref.scheme_id, text, ref.category)
        except CorpusError as exc:
            if not args.allow_missing:
                print(f"\nERROR: {exc}", file=sys.stderr)
                print(
                    f"\nSave cleaned text manually into "
                    f"{config.CORPUS_DIR / f'{ref.scheme_id}.md'} with the front matter "
                    "documented in src/ingest/load.py, then re-run.",
                    file=sys.stderr,
                )
                return 1
            logger.warning("continuing past %s without a validated body", ref.scheme_id)
            failures.append(ref.scheme_id)
            reports[ref.scheme_id] = {}
        bodies[ref.scheme_id] = text

    docs = write_corpus(sources, bodies)
    write_manifest(docs)

    width = max(len(ref.scheme_id) for ref in sources) + 2
    print(f"\n{'scheme_id'.ljust(width)}{'chars':>8}{'lines':>8}  fact anchors")
    print("-" * (width + 44))
    for doc in docs:
        report = reports.get(doc.scheme_id, {})
        found = sum(1 for present in report.values() if present)
        marker = f"{found}/{len(report)}" if report else "NOT VALIDATED"
        print(
            f"{doc.scheme_id.ljust(width)}{len(doc.raw_text):>8}"
            f"{len(doc.raw_text.splitlines()):>8}  {marker}"
        )
    print(f"\nWrote {len(docs)} corpus files to {config.CORPUS_DIR}")
    print(f"Wrote {config.SOURCES_MANIFEST.name}")
    if failures:
        print(f"\nWARNING: no validated body for {', '.join(failures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
