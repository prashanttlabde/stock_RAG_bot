"""Streamlit demo UI: the one surface a person interacts with (architecture.md §9,
PRD §6.2 / §9).

This file contains no business logic. Every message goes through exactly one call,
`src.chat_service.handle_message`, and every other function here only renders what
that call already returned. Chat history lives in `st.session_state` for the life of
the browser tab and nowhere else: nothing in this module writes to disk, and the
history is gone the moment the session ends (architecture.md §17, INV-3).

**Load order is deliberate**, for memory-constrained hosts (Render's free tier and
similar): the page shell renders before this project's own modules are imported at
all, and the embedding model + Chroma collection are warmed up explicitly, behind a
spinner, right after -- see the comments at each step below.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run src/app.py` executes this file directly, and Streamlit puts only
# this file's own directory (src/) on sys.path, not the project root -- so `import
# src.xxx` fails with "No module named 'src'" unless the project root is added
# first. `python -m` and pytest both handle this automatically; a bare `streamlit
# run` does not, so it has to happen here. Must run before any `from src...` import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

# The page shell renders here, before a single line of this project's own code is
# imported. Importing the retrieval/generation chain pulls in chromadb and
# sentence-transformers (torch) transitively -- profiled at ~5 seconds of pure
# import time on a fast local machine, before the embedding model has even loaded,
# and slower on a constrained host. Streamlit streams each st.* call to the
# browser as it executes rather than batching them at the end of the script, so
# everything below is visible immediately instead of behind that import cost.
st.set_page_config(page_title="HDFC MF Facts Assistant", layout="centered")
st.title("HDFC MF Facts Assistant")
st.caption(
    "Ask about HDFC mutual fund scheme facts — expense ratio, exit load, minimum "
    "SIP, lock-in, benchmark, riskometer."
)

import re  # noqa: E402

from src.chat_service import EXAMPLE_QUESTIONS, handle_message  # noqa: E402
from src.generate.no_answer import covered_schemes_with_dates  # noqa: E402
from src.guards.copy import DISCLAIMER  # noqa: E402
from src.ingest.embed import embed_query  # noqa: E402
from src.ingest.store import collection_exists, get_collection  # noqa: E402
from src.logging_config import get_logger  # noqa: E402
from src.types import ChatReply  # noqa: E402

logger = get_logger("app")

st.info(DISCLAIMER)

INGEST_MISSING_MESSAGE = (
    "The search index hasn't been built yet. Run `make ingest` from the project "
    "root, then reload this page."
)


@st.cache_resource(show_spinner="Loading the embedding model and search index…")
def _warm_up() -> int:
    """Load the embedding model and open the Chroma collection once per process.

    `st.cache_resource` caches the *return value* for the life of the server
    process, across every session and rerun -- the same lifetime `embed.py`'s and
    `store.py`'s own module-level singletons already keep, so this changes
    nothing about how many times the model or collection actually load. What it
    adds is the spinner: without this, that first load happens silently inside
    whatever the first real query is, which is exactly what a "blank page"
    complaint on a slow host looks like from the outside.

    Raises rather than catches its own failure on purpose: `st.cache_resource`
    does not cache a raised exception, so a transient failure (a slow model
    download, a cold disk) is retried on the next page load instead of being
    stuck returning the same failure until the process restarts. The caller below
    is what decides what a user sees when this fails.

    Returns the collection's chunk count, which doubles as the "is the store
    actually built" check the rest of the page uses -- `get_collection` is only
    called after `collection_exists` confirms it will not have to *create* one:
    `get_collection` uses `get_or_create_collection`, so calling it against a
    name that does not exist yet would silently create an empty one on disk.
    """
    embed_query("warm up")
    return get_collection().count() if collection_exists() else 0


try:
    _store_chunk_count: int | None = _warm_up()
except Exception as exc:
    logger.warning("app: startup warm-up failed (%s)", type(exc).__name__)
    _store_chunk_count = None
    st.error(
        "Something went wrong loading the model or the search index. Please "
        "reload the page."
    )

if _store_chunk_count == 0:
    st.error(INGEST_MISSING_MESSAGE)

# Neutralizes markdown syntax (links, emphasis, headings) in a string before it is
# rendered with `st.markdown`. Applied only to the user's own typed message: an
# assistant reply is always one of this project's own curated strings (an
# extractive or LLM-grounded answer, a refusal, or a no-answer message), never raw
# user input, so it is rendered as-is.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+.!-])")


def _escape_markdown(text: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def _render_reply(reply: ChatReply, show_retrieved: bool) -> None:
    """Render one assistant turn from an already-computed `ChatReply`.

    Branches on `reply.answer`/`reply.refusal` rather than re-parsing `reply.text`,
    so the citation and freshness lines get their own widgets (a clickable link, a
    muted caption) exactly as `format_reply`'s docstring anticipates, instead of
    being split back out of a formatted string.
    """
    if reply.refusal is not None and reply.refusal.kind == "retrieval_error":
        st.error(INGEST_MISSING_MESSAGE)
    elif reply.answer is not None and reply.answer.answer_text:
        st.markdown(reply.answer.answer_text)
        st.info(f"Source: {reply.answer.source_url}")
        if reply.answer.fetched_at:
            st.caption(f"Last updated from sources: {reply.answer.fetched_at}")
    elif reply.refusal is not None:
        st.markdown(reply.refusal.message)
        if reply.refusal.link_url:
            st.caption(f"[{reply.refusal.link_label or 'Link'}]({reply.refusal.link_url})")
    else:
        st.markdown(reply.text)

    if show_retrieved and reply.retrieved:
        with st.expander(f"Retrieved chunks ({len(reply.retrieved)})"):
            for hit in reply.retrieved:
                st.write(f"`{hit.scheme_id}` · {hit.section or '—'} · score={hit.score:.3f}")


def _turn_counts() -> tuple[int, int]:
    """(answered, refused-or-no-answer) over the current session's messages."""
    answered = 0
    other = 0
    for message in st.session_state["messages"]:
        reply = message.get("reply")
        if reply is None:
            continue
        if reply.answer is not None and reply.answer.answer_text:
            answered += 1
        else:
            other += 1
    return answered, other


if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "pending_question" not in st.session_state:
    st.session_state["pending_question"] = None

example_cols = st.columns(len(EXAMPLE_QUESTIONS))
for i, (col, example) in enumerate(zip(example_cols, EXAMPLE_QUESTIONS, strict=True)):
    if col.button(example, key=f"example_{i}"):
        st.session_state["pending_question"] = example

# The sidebar is written in two passes. The corpus list and checkbox go first,
# since the history loop right below needs `show_retrieved`. The counts and reset
# button are deliberately written in a *second* `with st.sidebar:` block, after the
# new turn below has been appended to `st.session_state["messages"]` -- Streamlit
# appends to the same sidebar container across multiple `with` blocks in one script
# run, so this still renders as one sidebar, but with counts computed from the
# fully updated state. Reading them here instead, before the new turn exists, made
# the counters permanently lag one turn behind what the chat area showed.
with st.sidebar:
    st.subheader("Corpus coverage")
    for scheme_name, fetched_at in covered_schemes_with_dates():
        st.write(f"- {scheme_name} — updated {fetched_at}")

    st.divider()
    show_retrieved = st.checkbox("Show retrieved chunks", value=False)

for message in st.session_state["messages"]:
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(_escape_markdown(message["content"]))
        else:
            _render_reply(message["reply"], show_retrieved)

typed_question = st.chat_input("Ask about a scheme fact…")
question = st.session_state.pop("pending_question", None) or typed_question

if question:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(_escape_markdown(question))

    history = [
        m["reply"]
        for m in st.session_state["messages"]
        if m["role"] == "assistant" and m.get("reply") is not None
    ]
    reply = handle_message(question, history=history)
    st.session_state["messages"].append({"role": "assistant", "reply": reply})
    with st.chat_message("assistant"):
        _render_reply(reply, show_retrieved)

with st.sidebar:
    st.divider()
    answered, other = _turn_counts()
    col_a, col_b = st.columns(2)
    col_a.metric("Answered", answered)
    col_b.metric("Refused / no answer", other)

    st.divider()
    if st.button("Reset conversation"):
        st.session_state["messages"] = []
        st.rerun()
