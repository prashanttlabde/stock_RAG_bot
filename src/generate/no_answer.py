"""The no-answer path (architecture.md §7 step 4, Phase 6 task 3).

Shown when retrieval found nothing above the floor, or when the retrieved text contains
no sentence that answers the question. Its job is to be honest and useful: say what is
missing, point at the closest page that does exist, and list what the assistant *can*
talk about. It must never approximate an answer, so it carries no figures of any kind
except the `fetched_at` dates it reads from the manifest.
"""

from __future__ import annotations

from src.ingest.load import load_corpus, load_sources
from src.logging_config import get_logger
from src.types import RetrievalHit, SourceDoc

logger = get_logger("generate")

NO_ANSWER_HEADLINE = "I don't have that in the indexed scheme pages."


def no_answer_message(
    hits: list[RetrievalHit] | None = None,
    detected_scheme_id: str | None = None,
) -> str:
    """Explain that the answer is not indexed, and say what is.

    `detected_scheme_id` is what makes "the closest page I have is ..." possible: when
    the floor rejects everything the hits list is empty, so the only surviving evidence
    that the question named a scheme is the retriever's own detection. Passing it is how
    Phase 7 keeps the two routes -- "nothing retrieved" and "retrieved but ungrounded" --
    reading the same.

    The 5 covered schemes are listed by name only. The one URL that can appear is the
    page of the detected scheme, and it is framed as a place to look rather than as
    support: there is no fact here for a URL to support, so `Answer.source_url` stays
    `None` and Phase 7 renders this as guidance, not as a citation line.
    """
    parts = [NO_ANSWER_HEADLINE]
    if not hits:
        parts.append(
            "Nothing I retrieved comes close to this question, and I would rather say "
            "that than guess."
        )

    sources = _allowlist()
    scheme_id = detected_scheme_id or (hits[0].chunk.scheme_id if hits else None)
    if scheme_id and scheme_id in sources:
        ref = sources[scheme_id]
        parts.append(f"The closest page I have is {ref.scheme_name}: {ref.source_url}")
    if sources:
        listed = ", ".join(sources[key].scheme_name for key in sorted(sources))
        parts.append(f"I only cover these {len(sources)} scheme pages: {listed}.")
    parts.append(
        "Try asking for expense ratio, exit load, minimum SIP, lock-in, benchmark, or "
        "risk level for one of them."
    )
    return " ".join(parts)


def _allowlist() -> dict[str, SourceDoc]:
    """scheme_id -> source record for the allowlist, or an empty map if unreadable."""
    try:
        return {ref.scheme_id: ref for ref in load_sources()}
    except Exception as exc:  # the message must render even without the manifest
        logger.info("generate: allowlist unavailable for no-answer (%s)", type(exc).__name__)
        return {}


def covered_schemes_with_dates() -> list[tuple[str, str]]:
    """(scheme_name, fetched_at) for the sidebar's coverage list (architecture.md §9)."""
    try:
        return sorted((doc.scheme_name, doc.fetched_at) for doc in load_corpus())
    except Exception as exc:  # sidebar coverage is a nicety, never a blocker
        logger.info("generate: corpus coverage unavailable (%s)", type(exc).__name__)
        return []
