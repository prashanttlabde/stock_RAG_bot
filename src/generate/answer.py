"""Grounded generation: retrieval hits in, one cited ≤3-fact answer out.

Implements architecture.md §7 steps 5–6 and enforces INV-1 (exactly one allowlist URL),
INV-2 (≤3 sentences) and INV-5 (nothing but retrieved text).

**What a "sentence" is in this corpus.** The plan defines the 3-sentence cap as a split
on `(?<=[.!?])\\s+`. That assumes prose. Half of this corpus is not prose: the hero block
on every scheme page is a run of label/value lines with almost no terminators --

    Expense ratio: 0.77%
    Min. for SIP: ₹100
    NAV: 25 Sep '26: ₹2,214.57

-- which a terminator-only split treats as a single 8-sentence unit, so the cap would be
silently unenforced on exactly the chunks that carry the numbers people ask for. A
newline is therefore a unit boundary too. `split_units` is the one definition, and both
`generate_template` and `truncate_sentences` use it, so INV-2 is enforced identically on
the template and LLM paths.

**Why generation is extractive by default.** `GENERATION_MODE=template` copies sentences
out of retrieved chunks and never paraphrases, so a number in an answer is a number that
was in the corpus. The LLM path exists behind the same contract, with temperature 0 and
a fallback to the template on any failure.
"""

from __future__ import annotations

import re
from functools import lru_cache

from src import config
from src.generate.prompt import (
    GROUNDING_CHECK,
    NOT_IN_CONTEXT,
    SYSTEM_RULES,
    build_user_prompt,
)
from src.ingest.load import load_sources
from src.logging_config import get_logger
from src.retrieve.retriever import content_terms, matched_terms
from src.types import Answer, RetrievalHit

logger = get_logger("generate")

# A sentence terminator, or a line break. Phase 2's chunker already treats a newline as
# a hard boundary, so this is the same unit definition the corpus was built with.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Abbreviations that end in a period but do not end a sentence. This corpus is full of
# them, and a naive terminator split destroys the facts: `Min. for SIP: ₹100` becomes
# `Min.` and `for SIP: ₹100`, so the amount is separated from its label and the answer
# reads as "Minimum investments for SIP: ₹100 for SIP: ₹500". Splitting a label from its
# value is the single most damaging thing this module could do, because the value is the
# thing the user asked for.
_ABBREVIATIONS = frozenset(
    {
        "min", "max", "no", "rs", "st", "mr", "mrs", "ms", "dr", "vs", "approx",
        "fig", "sec", "art", "sr", "jr", "inc", "ltd", "co", "cr", "per", "a", "p",
        "e", "w.e.f", "i.e", "eg",
    }
)

# A unit that contains a digit is carrying a fact; one that does not is a label.
_HAS_VALUE = re.compile(r"\d")

_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

# The one chunk shape where a label is followed by a definition rather than a value.
_GLOSSARY_SECTION = "understand terms"


def _normalized(section: str | None) -> str:
    """Lowercase a section title and reduce it to words, for tolerant matching."""
    return re.sub(r"[^a-z0-9]+", " ", (section or "").lower()).strip()


def _ends_a_sentence(line: str, boundary: int) -> bool:
    """Whether the terminator at `line[boundary]` genuinely closes a sentence.

    A terminator preceded by an abbreviation, or by a single character, is a label
    ending, not a sentence ending.
    """
    if line[boundary] not in ".!?":
        return False
    word = re.split(r"[\s(\[\"']", line[:boundary])[-1].lower()
    if word in _ABBREVIATIONS:
        return False
    return len(word) > 1


def split_units(text: str) -> list[str]:
    """Split answerable text into its smallest complete units.

    A unit ends at a newline or after a real sentence terminator, and is stripped. This
    is the single definition of a "sentence" for INV-2 purposes; see the module docstring
    for why the terminator alone is not enough here.
    """
    units: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        start = 0
        for match in _SENTENCE_SPLIT.finditer(line):
            if _ends_a_sentence(line, match.start() - 1):
                units.append(line[start : match.start()].strip())
                start = match.end()
        units.append(line[start:].strip())
    return [unit for unit in units if unit]


def _cap_units(text: str, max_units: int) -> tuple[str, bool]:
    """Keep the first `max_units` units. Returns (text, whether_anything_was_dropped)."""
    units = split_units(text)
    if len(units) <= max_units:
        return text.strip(), False
    return " ".join(units[:max_units]), True


def truncate_sentences(text: str, max_sentences: int | None = None) -> str:
    """Cap `text` at `max_sentences` (default `config.MAX_ANSWER_SENTENCES`) units.

    Logs a warning when it drops anything, because a dropped unit usually means the
    generator returned more than the contract allows rather than that the answer was
    simply long.
    """
    limit = config.MAX_ANSWER_SENTENCES if max_sentences is None else max_sentences
    capped, dropped = _cap_units(text, limit)
    if dropped:
        logger.warning(
            "generate: answer exceeded %d units and was truncated", limit
        )
    return capped


def strip_urls(text: str) -> str:
    """Remove any URL from answer prose (INV-1: the citation is a separate field)."""
    return _URL.sub("", text).strip()


# --- Citation --------------------------------------------------------------------


def select_primary_source(hits: list[RetrievalHit]) -> str:
    """The one allowlist URL to cite. Never returns more than one.

    All hits sharing a URL is the easy case: the answer is about one scheme page even if
    the chunks came from several sections of it. Otherwise the top hit decides, because
    ranking is the only signal about which page the question was really about.
    """
    if not hits:
        return ""
    urls = [hit.chunk.source_url for hit in hits if hit.chunk.source_url]
    if not urls:
        return ""
    if len(set(urls)) == 1:
        return urls[0]
    return urls[0]


def is_ambiguous_source(hits: list[RetrievalHit]) -> bool:
    """Whether the top hits are near-tied across different pages.

    Reported rather than acted on: the plan still requires exactly one citation, so the
    tie is surfaced in the debug panel and the samples file instead of being resolved by
    inventing a second link or by silently pretending one page is clearly the answer.
    """
    if len(hits) < 2:
        return False
    top = hits[0]
    runner = hits[1]
    if not top.chunk.source_url or not runner.chunk.source_url:
        return False
    if top.chunk.source_url == runner.chunk.source_url:
        return False
    return (top.score - runner.score) <= config.AMBIGUOUS_SOURCE_DELTA


@lru_cache(maxsize=16)
def _scheme_names() -> dict[str, str]:
    """scheme_id -> human name from the allowlist, with an id-derived fallback.

    Generation needs a display name to prefix an answer, and the name lives in
    `data/sources.yaml` rather than in the chunk metadata. The fallback keeps generation
    working if the manifest is unreadable, which must not turn a fact question into an
    error.
    """
    try:
        return {ref.scheme_id: ref.scheme_name for ref in load_sources()}
    except Exception as exc:  # a missing manifest must not break answering
        logger.info("generate: scheme names unavailable (%s)", type(exc).__name__)
        return {}


def _display_name(scheme_id: str | None) -> str:
    if not scheme_id:
        return ""
    known = _scheme_names().get(scheme_id)
    if known:
        return known
    return scheme_id.replace("hdfc-", "").replace("-direct-growth", "").replace("-", " ").title()


# --- Context ---------------------------------------------------------------------


def compose_context(hits: list[RetrievalHit]) -> str:
    """The retrieved text handed to a generator, labelled by scheme and section."""
    from src.generate.prompt import render_chunk

    names = _scheme_names()
    return "\n\n".join(
        render_chunk(i, hit.chunk, names.get(hit.chunk.scheme_id))
        for i, hit in enumerate(hits)
    )


# --- Template (default, deterministic) --------------------------------------------


def generate_template(question: str, hits: list[RetrievalHit]) -> str:
    """Extractive answer: the retrieved sentences that best answer the question.

    Selection uses **one** hit -- the first one that can answer at all. This follows the
    plan's "the best sentence(s) from the top hit", and it is not merely literal: the five
    schemes are five different funds, so borrowing a line from a second hit mixes two
    schemes' numbers into one answer with no way for the reader to tell which is which.
    That is exactly what happened before this was fixed -- "what is the minimum SIP
    amount" answered "Min. for SIP: ₹100 Min. for SIP: ₹500", two different schemes, one
    citation, and nothing to say which figure belonged to it. An answer always comes from
    one page, and the page is named in the prefix and cited, so a cross-scheme question
    gets one honest answer instead of a contradiction.

    Within a hit, units are ranked by how many of the question's content words they
    contain, ties broken toward the unit that carries a value. Asking "what is the
    minimum SIP amount" splits `minimum` and `sip` across two units -- the heading
    `Minimum investments` and the fact `Min. for SIP: ₹100` -- and both match one term. A
    unit with a digit in it is the one that answers a question about an amount, ratio, or
    period, so it wins.

    The budget is a cap, not a target. The best unit is always taken, but a further unit
    is added only if it contributes a content word not already covered **and** carries a
    value. That keeps a bare heading out of the answer -- "Min. for SIP: ₹100 Minimum
    investments" is the same fact with a label stapled to it -- while still allowing a
    genuinely second fact through when the question asks for two.

    Returns `NOT_IN_CONTEXT` when the question has content words and no retrieved unit
    shares even one of them. That is the template path's equivalent of the LLM's
    sentinel: the context genuinely does not contain the answer, and inventing one would
    violate INV-5.
    """
    if not hits:
        return NOT_IN_CONTEXT

    # Entity stripping can consume the whole question -- "Is it a large cap fund?" is
    # built entirely from words in a scheme name, so nothing is left to match on. The
    # answer to that is *not* to quote the first few lines of the best chunk, which is
    # how this used to answer "Is it a large cap fund?" with a repeated heading
    # ("About HDFC Large Cap Fund Direct Growth About HDFC Large Cap Fund Direct
    # Growth"). Falling back to the question's own words keeps a usable signal.
    terms = content_terms(question) or content_terms(question, strip_entities=False)
    if not terms:
        return NOT_IN_CONTEXT

    for hit in hits:
        units = split_units(hit.chunk.text)
        ranked = sorted(
            enumerate(units),
            key=lambda pair: (
                -len(matched_terms(terms, pair[1])),
                -bool(_HAS_VALUE.search(pair[1])),
                -len(pair[1]),
                pair[0],
            ),
        )
        selected: list[tuple[int, str]] = []
        seen: list[str] = []
        covered: set[str] = set()
        for position, unit in ranked:
            if len(selected) >= config.MAX_ANSWER_SENTENCES:
                break
            unit_terms = set(matched_terms(terms, unit))
            if not unit_terms:
                continue
            if selected and (unit_terms <= covered or not _HAS_VALUE.search(unit)):
                continue
            key = " ".join(unit.lower().split())
            if any(key in other or other in key for other in seen):
                continue
            seen.append(key)
            covered |= unit_terms
            selected.append((position, unit))
        if not selected:
            continue
        selected = _drop_redundant_title(selected, units)
        selected = _complete_label(selected, units, hit.chunk.section)
        selected.sort(key=lambda item: item[0])
        body = " ".join(unit for _, unit in selected)
        return _prefixed(hit.chunk.scheme_id, body)

    return NOT_IN_CONTEXT


def _drop_redundant_title(
    selected: list[tuple[int, str]], units: list[str]
) -> list[tuple[int, str]]:
    """Drop a leading chunk title when a better-matched unit was selected too.

    The answer is already prefixed with the scheme name, so repeating the page title adds
    nothing: "HDFC Large Cap Fund - Direct Growth: HDFC Large Cap Fund Direct Growth NAV:
    25 Sep '26: Rs1,189.08". The title is only dropped when something else was selected as
    well, so a question whose answer genuinely *is* the title still gets one.
    """
    if len(selected) < 2 or not units:
        return selected
    title = " ".join(units[0].lower().split())
    if " ".join(selected[0][1].lower().split()) == title:
        return selected[1:]
    return selected


def _prefixed(scheme_id: str | None, body: str) -> str:
    """Prefix the body with the scheme name so an answer is never a bare number."""
    name = _display_name(scheme_id)
    return f"{name}: {body}" if name else body


# --- LLM (optional) ----------------------------------------------------------------


def generate_llm(question: str, hits: list[RetrievalHit], client=None) -> str:
    """Call the configured chat model, falling back to the template on any failure.

    `client` is injected rather than constructed here: this project ships no LLM SDK, so
    the LLM path is a documented seam a caller wires up, and `GENERATION_MODE=template`
    -- the default -- never touches it. Temperature is `config.LLM_TEMPERATURE` (0), so
    two runs of the same question produce the same answer.

    Having no client is treated as a failure, not an error to propagate. `llm` mode with
    no SDK wired up is a configuration the operator can easily reach, and the plan asks
    for a template fallback on *any* failure; raising here instead would turn a missing
    integration into a broken chat turn.
    """
    if not hits:
        return NOT_IN_CONTEXT

    if client is None:
        logger.info("generate: no llm client wired up; using template")
        return generate_template(question, hits)

    names = _scheme_names()
    prompt = build_user_prompt(question, [hit.chunk for hit in hits], names)
    try:
        response = client.complete(
            system=SYSTEM_RULES,
            user=prompt,
            temperature=config.LLM_TEMPERATURE,
        )
    except Exception as exc:
        logger.info("generate: llm call failed (%s); using template", type(exc).__name__)
        return generate_template(question, hits)
    return str(response)


def _build_client():
    """Return the chat client for `GENERATION_MODE=llm`, or `None` if none is wired up.

    Delegates to `src.generate.llm_client.build_groq_client`, which returns `None`
    when no key is configured, so a fresh clone with no `.env` still runs the
    template path exactly as before. This stays a separate, named seam (rather
    than inlining the Groq call here) so a test can install a fake client without
    an API key, and so `answer.py` itself never needs to import `requests`.
    """
    from src.generate.llm_client import build_groq_client

    return build_groq_client()


# --- Entry point --------------------------------------------------------------------


def _complete_label(
    selected: list[tuple[int, str]], units: list[str], section: str | None
) -> list[tuple[int, str]]:
    """Give a valueless label the definition that follows it, in a glossary.

    The "Understand terms" chunks are a term followed by its definition, so a bare
    `Expense ratio` is half an answer to "what is the expense ratio?". Appending the next
    line turns it into the glossary's own wording, which is verbatim from the chunk and so
    still extractive.

    This is restricted to the glossary on purpose. Applied to any chunk it answers
    "what is the risk level?" with `Very High Risk NAV: 25 Sep '26: Rs159.82` -- the risk
    category is a complete answer, and the NAV line is a different fact stapled to it.

    Only the immediate successor is used, and only when the answer is still within the
    unit budget, so a label can never pull in an unrelated line or blow INV-2.
    """
    if _normalized(section) != _GLOSSARY_SECTION:
        return selected
    if len(selected) >= config.MAX_ANSWER_SENTENCES or _HAS_VALUE.search(selected[0][1]):
        return selected
    position = selected[0][0]
    follower = position + 1
    if follower < len(units):
        return [*selected, (follower, units[follower])]
    return selected


def answer_question(question: str, hits: list[RetrievalHit]) -> Answer:
    """Turn retrieved hits into one cited, ≤3-unit, facts-only answer.

    Order matters: source selection and generation both need the raw hits, the unit cap
    is applied to whatever came back (template or LLM), and the sentinel is checked
    before any citation is attached so a no-answer carries no URL that looks like
    support for a fact nobody supplied.
    """
    if not hits:
        return Answer(
            answer_text=None,
            source_url=None,
            fetched_at=None,
            used_chunks=[],
            generation_mode=config.GENERATION_MODE,
        )

    primary = hits[0]
    mode = config.GENERATION_MODE
    raw = (
        generate_llm(question, hits, client=_build_client())
        if mode == "llm"
        else generate_template(question, hits)
    )

    if GROUNDING_CHECK.search(raw):
        return Answer(
            answer_text=None,
            source_url=None,
            fetched_at=None,
            scheme_id=primary.chunk.scheme_id,
            scheme_name=_display_name(primary.chunk.scheme_id),
            section=primary.chunk.section,
            used_chunks=[hit.chunk for hit in hits],
            generation_mode=mode,
        )

    body, truncated = _cap_units(strip_urls(raw), config.MAX_ANSWER_SENTENCES)
    if not body:
        return Answer(
            answer_text=None,
            source_url=None,
            fetched_at=None,
            scheme_id=primary.chunk.scheme_id,
            scheme_name=_display_name(primary.chunk.scheme_id),
            section=primary.chunk.section,
            used_chunks=[hit.chunk for hit in hits],
            generation_mode=mode,
        )

    return Answer(
        answer_text=body,
        source_url=select_primary_source(hits),
        fetched_at=primary.chunk.fetched_at,
        scheme_id=primary.chunk.scheme_id,
        scheme_name=_display_name(primary.chunk.scheme_id),
        section=primary.chunk.section,
        used_chunks=[hit.chunk for hit in hits],
        ambiguous_source=is_ambiguous_source(hits),
        truncated=truncated,
        generation_mode=mode,
    )
