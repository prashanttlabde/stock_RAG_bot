"""Ingestion stage 3: structure-aware chunking (architecture.md §5.2, §6 step 3).

Each `SourceDoc` becomes a list of `Chunk`s that map 1:1 to the scheme page's own
sections, so that every retrieved chunk is a self-contained, citable unit.

Two chunking strategies, chosen per document:

* **Section path** (preferred). Phase 1 writes each kept page section as a
  `## Heading` block, so a corpus file has real headings to split on. When at
  least `MIN_SECTIONS_FOR_STRUCTURED_CHUNKING` headings are found, each section
  becomes its own chunk. A section that is still too large is split further.
* **Fallback path.** When fewer headings are found, or when the body came from
  the hand-written corpus files of the manual fallback (PRD §15), the whole body
  is packed into fixed-size sentence-aware chunks instead. This keeps every chunk
  inside `CHUNK_TARGET_TOKENS` at the cost of losing the section label.

The decision rule is the one in architecture.md §6: prefer splitting on real
headings, fall back to size when the structure is not there. Which path each
scheme took is recorded in `docs/implementation-notes.md`.

Chunk ids are `{scheme_id}::{section_slug}::{i:03d}` and are a pure function of
the corpus file, so re-running ingestion overwrites the same ids instead of
accumulating near-duplicates in the vector store.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Final

from src import config
from src.errors import IngestError
from src.ingest.load import load_corpus
from src.logging_config import get_logger
from src.types import Chunk, SourceDoc

logger = get_logger("ingest.chunk")

DOC_TITLE_PREFIX: Final = "# "
SECTION_PREFIX: Final = "## "
PREAMBLE_SLUG: Final = "preamble"
FALLBACK_SLUG: Final = "fallback"

# Ordered list of known fact headings. Order is preserved from the plan for
# fidelity, but it does not affect behaviour: a heading only has to match one
# alternative, and `SECTION_HEADING_RE` is a substring test.
SECTION_PATTERNS: Final = (
    r"expense\s*ratio",
    r"exit\s*load",
    r"\bsip\b",
    r"minimum\s+(?:sip\s+)?amount",
    r"lock[\s-]?in",
    r"riskometer|volatility",
    r"benchmark",
    r"\bnav\b",
    r"how\s+to\s+(?:get|download)",
    r"statement",
    r"capital\s*gains|tax",
    r"direct\s+plan|growth\s+option",
    r"minimum\s+investment",
    r"diversification",
    r"minimum\s+amount",
)

SECTION_HEADING_RE: Final = re.compile(
    "|".join(f"(?:{pattern})" for pattern in SECTION_PATTERNS), re.IGNORECASE
)
SENTENCE_BOUNDARY: Final = re.compile(r"(?<=[.!?])\s+")
SENTENCE_TERMINATED: Final = re.compile(r"[.!?]$")
LABEL_VALUE: Final = re.compile(r"^[^:]{1,40}:\s+\S")
SLUG_STRIP: Final = re.compile(r"[^a-z0-9]+")

# MiniLM's BPE tokenizer emits roughly 1.33 tokens per whitespace word on this
# kind of English financial text. Words / 0.75 is the proxy the plan specifies;
# it is only used for packing and reporting, never for exact accounting.
WORD_PER_TOKEN: Final = 0.75


def estimate_tokens(text: str) -> int:
    """Approximate token count as `words / 0.75`, floored at 1 for non-empty text."""
    return max(1, round(len(text.split()) / WORD_PER_TOKEN)) if text.strip() else 0


def section_slug(section: str | None) -> str:
    """Deterministic, filesystem-safe slug for a section name."""
    if not section:
        return PREAMBLE_SLUG
    return SLUG_STRIP.sub("-", section.lower()).strip("-") or PREAMBLE_SLUG


def _is_heading(line: str) -> bool:
    """True when a bare body line should be treated as a section heading.

    Requires a known fact keyword, fewer than `MAX_HEADING_CHARS` characters, and
    no sentence-ending punctuation. A `Label: value` line is never a heading even
    when it matches a keyword, so the flat fact block
    `Expense ratio: 1.03%` stays one line instead of becoming a heading with the
    following line as its body.
    """
    if len(line) >= config.MAX_HEADING_CHARS or SENTENCE_TERMINATED.search(line):
        return False
    if LABEL_VALUE.match(line):
        return False
    return bool(SECTION_HEADING_RE.search(line))


def uses_markdown_headings(text: str) -> bool:
    """True when the body carries `## ` headings and they are the real structure.

    Phase 1 writes every kept page section as a `## Heading` block, so when those
    are present they are authoritative and no bare line inside them is a heading:
    the glossary term `Expense ratio` under `## Understand terms` is content, not
    a section. Only a body with no `##` at all — a hand-written corpus file from
    the manual fallback — falls back to keyword detection.
    """
    return any(line.strip().startswith(SECTION_PREFIX) for line in text.splitlines())


def segment_sections(text: str) -> list[tuple[str | None, str]]:
    """Split a document body into `(section_name, body)` pairs.

    With `## ` headings, those are the sections. Without them, a bare line is
    promoted to a heading only when it passes `_is_heading` — including the very
    first content line, so a document that opens with a heading does not get a
    spurious preamble. The leading `# ` title line is skipped in both modes: it
    duplicates the `scheme_name` front matter and would only ever produce a
    one-line section that the minimum-length filter discards. Text before the
    first heading is returned as a `(None, body)` preamble.
    """
    markdown = uses_markdown_headings(text)
    sections: list[tuple[str | None, list[str]]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(DOC_TITLE_PREFIX):
            continue

        if markdown and line.startswith(SECTION_PREFIX):
            sections.append((line[len(SECTION_PREFIX) :].strip(), []))
            continue

        if not markdown and _is_heading(line):
            sections.append((line, []))

        if not sections:
            sections.append((None, []))
        sections[-1][1].append(line)

    return [(name, _body_text(name, body)) for name, body in sections if body]


def _body_text(section: str | None, lines: list[str]) -> str:
    """Join a section's lines, dropping a leading line that repeats the heading.

    Phase 1's extractor emits the page's accordion title both as the `##` heading
    and as the section's first text node, so without this every chunk opens with
    the same line twice.
    """
    if section and lines and lines[0] == section:
        lines = lines[1:]
    return "\n".join(lines).strip()


def _sentence_units(body: str) -> list[str]:
    """Split a body into sentence-ish units.

    A newline is treated as a hard boundary in addition to sentence-ending
    punctuation. Corpus bodies are line-oriented, so a fact line such as
    `Min. for SIP: ₹100` has no terminator and would otherwise be glued to the
    next line by a punctuation-only split.
    """
    units: list[str] = []
    for line in body.splitlines():
        for unit in SENTENCE_BOUNDARY.split(line.strip()):
            if unit:
                units.append(unit)
    return units


def split_fallback(
    body: str,
    target_tokens: int | None = None,
    overlap_ratio: float | None = None,
) -> list[str]:
    """Greedily pack `body` into chunks of about `target_tokens` tokens.

    Sentences are appended to the current chunk until adding the next one would
    exceed the target, then the chunk is closed. A chunk is overlapped with the
    next by `overlap_ratio` of its own sentences (at least one, unless the ratio
    is zero) so a fact that straddles a boundary still appears whole in one
    chunk. A single sentence longer than the target is emitted on its own rather
    than being cut mid-sentence.
    """
    target = target_tokens or config.CHUNK_TARGET_TOKENS
    ratio = config.CHUNK_OVERLAP_RATIO if overlap_ratio is None else overlap_ratio
    units = _sentence_units(body)
    if not units:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for unit in units:
        unit_tokens = estimate_tokens(unit)
        if current and current_tokens + unit_tokens > target:
            chunks.append("\n".join(current))
            carry = round(len(current) * ratio) if ratio > 0 else 0
            current = current[-carry:] if carry else []
            current_tokens = sum(estimate_tokens(part) for part in current)
        current.append(unit)
        current_tokens += unit_tokens

    if current:
        chunks.append("\n".join(current))

    return [chunk for chunk in chunks if chunk.strip()]


def _render(section: str | None, body: str) -> str:
    """Prefix the section name so the embedded text carries its own topic."""
    return f"{section}\n{body}" if section else body


def _non_space_len(text: str) -> int:
    return len("".join(text.split()))


def chunk_document(doc: SourceDoc) -> list[Chunk]:
    """Chunk one `SourceDoc` into citation-valid `Chunk`s.

    Uses the section path when at least `MIN_SECTIONS_FOR_STRUCTURED_CHUNKING`
    headings are detected, and the size-based fallback otherwise. Chunks with
    fewer than `MIN_CHUNK_CHARS` non-space characters are dropped as boilerplate
    residue.
    """
    sections = segment_sections(doc.raw_text)
    named = [name for name, _ in sections if name]
    structured = len(named) >= config.MIN_SECTIONS_FOR_STRUCTURED_CHUNKING
    split_threshold = config.CHUNK_TARGET_TOKENS * 2

    chunks: list[Chunk] = []
    for section, body in sections:
        if not body.strip():
            continue

        if structured and estimate_tokens(body) <= split_threshold:
            pieces = [body]
        else:
            pieces = split_fallback(body, config.CHUNK_TARGET_TOKENS, config.CHUNK_OVERLAP_RATIO)

        for piece in pieces:
            text = _render(section, piece).strip()
            if _non_space_len(text) < config.MIN_CHUNK_CHARS:
                continue
            index = len(chunks) + 1
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.scheme_id}::{section_slug(section)}::{index:03d}",
                    scheme_id=doc.scheme_id,
                    source_url=doc.source_url,
                    section=section,
                    fetched_at=doc.fetched_at,
                    text=text,
                )
            )

    logger.info(
        "%s: %d sections (%s), structured=%s, %d chunks",
        doc.scheme_id,
        len(named),
        "markdown" if uses_markdown_headings(doc.raw_text) else "keyword",
        structured,
        len(chunks),
    )
    return chunks


def chunk_corpus(docs: list[SourceDoc]) -> list[Chunk]:
    """Chunk every document, preserving input order."""
    chunks: list[Chunk] = []
    for doc in docs:
        chunks.extend(chunk_document(doc))
    ids = [chunk.chunk_id for chunk in chunks]
    duplicates = sorted({chunk_id for chunk_id in ids if ids.count(chunk_id) > 1})
    if duplicates:
        raise IngestError(f"Duplicate chunk ids generated: {duplicates}")
    return chunks


def summarize(docs: list[SourceDoc], chunks: list[Chunk]) -> dict[str, object]:
    """Build the `chunk_stats.json` payload for the CLI and the notes."""
    token_counts = [estimate_tokens(chunk.text) for chunk in chunks]
    sections_found = {
        doc.scheme_id: [name for name, _ in segment_sections(doc.raw_text) if name] for doc in docs
    }
    strategy = {
        doc.scheme_id: (
            "section"
            if len(sections_found[doc.scheme_id]) >= config.MIN_SECTIONS_FOR_STRUCTURED_CHUNKING
            else "fallback"
        )
        for doc in docs
    }
    return {
        "docs": len(docs),
        "chunks": len(chunks),
        "min_tokens": min(token_counts, default=0),
        "avg_tokens": round(sum(token_counts) / len(token_counts), 1) if token_counts else 0,
        "max_tokens": max(token_counts, default=0),
        "sections_found": sections_found,
        "chunking_strategy": strategy,
        "chunks_per_doc": {
            doc.scheme_id: sum(1 for chunk in chunks if chunk.scheme_id == doc.scheme_id)
            for doc in docs
        },
        "constants": {
            "CHUNK_TARGET_TOKENS": config.CHUNK_TARGET_TOKENS,
            "CHUNK_OVERLAP_RATIO": config.CHUNK_OVERLAP_RATIO,
            "MIN_CHUNK_CHARS": config.MIN_CHUNK_CHARS,
            "MIN_SECTIONS_FOR_STRUCTURED_CHUNKING": config.MIN_SECTIONS_FOR_STRUCTURED_CHUNKING,
            "MAX_HEADING_CHARS": config.MAX_HEADING_CHARS,
        },
    }


def write_chunk_stats(stats: dict[str, object], path: Path | None = None) -> None:
    """Write the stats payload to `data/chunk_stats.json`."""
    target = path or config.CHUNK_STATS_FILE
    target.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote %s", target.name)


def main(argv: list[str] | None = None) -> int:
    """CLI: load the corpus, chunk it, print the per-scheme structure, save stats."""
    parser = argparse.ArgumentParser(description="Chunk the corpus into citable sections.")
    parser.add_argument(
        "--no-stats", action="store_true", help="print the report without writing chunk_stats.json"
    )
    args = parser.parse_args(argv)

    docs = load_corpus()
    chunks = chunk_corpus(docs)
    stats = summarize(docs, chunks)

    for doc in docs:
        names = stats["sections_found"][doc.scheme_id]
        count = stats["chunks_per_doc"][doc.scheme_id]
        print(f"\n{doc.scheme_id}  ({count} chunks, {len(names)} sections)")
        for name in names:
            print(f"  - {name}")

    print("\nchunk_id | section | first line")
    print("-" * 96)
    for chunk in chunks:
        first = chunk.text.splitlines()[0]
        print(f"{chunk.chunk_id} | {chunk.section or '-'} | {first[:44]}")

    width = 42
    print(f"\n{'scheme_id'.ljust(width)}{'chunks':>8}{'tokens(min/avg/max)':>26}")
    print("-" * (width + 34))
    for doc in docs:
        per_doc = [estimate_tokens(c.text) for c in chunks if c.scheme_id == doc.scheme_id]
        marker = f"{min(per_doc)}/{round(sum(per_doc) / len(per_doc), 1)}/{max(per_doc)}"
        count = stats["chunks_per_doc"][doc.scheme_id]
        print(f"{doc.scheme_id.ljust(width)}{count:>8}{marker:>26}")

    print(
        f"\n{stats['chunks']} chunks from {stats['docs']} docs | "
        f"tokens {stats['min_tokens']}/{stats['avg_tokens']}/{stats['max_tokens']}"
    )
    if not args.no_stats:
        write_chunk_stats(stats)
        print(f"Wrote {config.CHUNK_STATS_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
