"""Prompt construction for the optional LLM path (architecture.md §7 step 5).

This module is the single source of truth for the grounding contract. `SYSTEM_RULES` is
what an LLM is told; `NOT_IN_CONTEXT` is how it says "the context does not answer this",
and `GROUNDING_CHECK` is how that claim is detected. Nothing else in the project is
allowed to hardcode the sentinel string.

The default path never reaches any of this -- `GENERATION_MODE=template` is extractive
and deterministic, so a demo on a machine with no API key and no network behaves
identically. This module exists so the LLM path is a genuine, reviewable alternative
rather than a stub.
"""

from __future__ import annotations

import re

from src.types import Chunk

SYSTEM_RULES = (
    "You answer only from the provided context. Use only facts present in the context. "
    "Never invent numbers, dates, or scheme names. Maximum 3 sentences. "
    "No investment advice, no recommendations, no performance comparison. "
    "If the context does not contain the answer, reply exactly: NOT_IN_CONTEXT."
)

# The sentinel. An LLM returns this string verbatim; the template path returns it when
# no retrieved sentence shares a single content word with the question. Both mean the
# same thing to `answer_question`, which maps it to `answer_text=None`.
NOT_IN_CONTEXT = "NOT_IN_CONTEXT"

GROUNDING_CHECK = re.compile(rf"\b{NOT_IN_CONTEXT}\b")


def render_chunk(index: int, chunk: Chunk, scheme_name: str | None = None) -> str:
    """One context block: `[i] (scheme: <name> · section: <section>)` then the text."""
    name = scheme_name or chunk.scheme_id
    section = chunk.section or "general"
    return f"[{index}] (scheme: {name} · section: {section})\n{chunk.text}"


def build_user_prompt(question: str, chunks: list[Chunk], scheme_names: dict | None = None) -> str:
    """Render the full user turn: question, labelled context, then the source list.

    `source_url` is listed once at the end rather than per chunk so the model has no
    way to quote several URLs into the answer body -- the citation is chosen
    deterministically by `select_primary_source`, not by the model (INV-1).
    """
    names = scheme_names or {}
    blocks = [render_chunk(i, chunk, names.get(chunk.scheme_id)) for i, chunk in enumerate(chunks)]
    urls: list[str] = []
    for chunk in chunks:
        if chunk.source_url and chunk.source_url not in urls:
            urls.append(chunk.source_url)
    source_list = "\n".join(f"- {url}" for url in urls)
    return (
        "Context blocks:\n\n"
        + "\n\n".join(blocks)
        + f"\n\nSource pages:\n{source_list}"
        + f"\n\nQuestion: {question}\n\nAnswer using only the context blocks above."
    )
