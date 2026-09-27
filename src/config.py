"""Project-wide configuration constants (architecture.md §12).

Every tunable used by the ingestion and query pipelines lives here so that no
magic number appears in a logic module. Values are read from the environment
where the plan calls for it; `.env` is loaded once at import time if present.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
CORPUS_DIR = DATA_DIR / "corpus"
CHROMA_PATH = DATA_DIR / "chroma"
SOURCES_FILE = DATA_DIR / "sources.yaml"
SOURCES_MANIFEST = DATA_DIR / "sources.md"
CHUNK_STATS_FILE = DATA_DIR / "chunk_stats.json"
EMBEDDINGS_DUMP_FILE = DATA_DIR / "embeddings_dump.txt"

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
EMBED_BATCH_SIZE = 32

CHROMA_COLLECTION = "mf_faq_hdfc"
CHROMA_SPACE = "cosine"

# How many previous turns `chat_service.handle_message` will look back through to
# resolve a scheme for a follow-up question that names none itself ("what about the
# minimum SIP?" after asking about HDFC Small Cap). Only ever used when the current
# question names no scheme at all -- in-corpus or out-of-corpus -- so a follow-up
# that switches funds, or that names a competing brand, is never overridden by it.
MEMORY_WINDOW = 10

TOP_K = 4
# Tuned by `python scripts/tune_floor.py` against a 15-query labelled set on
# 2026-09-27: 8/8 answerable answered and 0 false answers across 0.30-0.50, so 0.40
# is the midpoint of that band. See docs/implementation-notes.md for the sweep table.
SIMILARITY_FLOOR = 0.40
AMBIGUOUS_SOURCE_DELTA = 0.05

# Groww renders identical boilerplate on all 5 scheme pages, so 16 of the 30 stored
# chunks are byte-identical copies of another chunk (see docs/implementation-notes.md).
# Returning TOP_K raw rows would spend the whole budget on one fact cited under five
# scheme ids, so retrieval asks Chroma for this many rows per requested hit and then
# keeps the first TOP_K *distinct* texts. 5 matches the worst-case duplicate run
# (`Understand terms` exists once per scheme), so TOP_K distinct rows are always
# reachable from TOP_K * 5 candidates.
RETRIEVE_OVERFETCH = 5

# A question's *content* words, once the scheme name is removed, must overlap the
# retrieved text by at least this many terms. Measured need: MiniLM cosine on a
# 30-chunk same-domain corpus has an overlapping range, so the floor alone cannot
# separate answerable from out-of-corpus (see docs/implementation-notes.md). Asking
# for "the SEBI registration number" scores 0.716 against the HDFC fund-house chunk
# while the answerable "lock in period for ELSS" scores 0.433, so no floor separates
# them. This gate catches it instead: "sebi" and "registration" appear nowhere in the
# corpus, so the question cannot be grounded in any chunk.
MIN_ANSWER_TERM_MATCH = 1

CHUNK_TARGET_TOKENS = 500
CHUNK_OVERLAP_RATIO = 0.15
MIN_CHUNK_CHARS = 30
MIN_SECTIONS_FOR_STRUCTURED_CHUNKING = 3
MAX_HEADING_CHARS = 80

MAX_ANSWER_SENTENCES = 3
MAX_QUERY_CHARS = 500

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
FETCH_TIMEOUT_SECONDS = 30
FETCH_RETRIES = 1

GENERATION_MODE = os.getenv("GENERATION_MODE", "template").strip().lower()
# Generic names, kept for whatever provider a caller wires into `_build_client()`
# next; `GROQ_API_KEY`/`GROQ_MODEL` below are the concrete ones this project's own
# client actually reads. Either one being set is enough to make GENERATION_MODE=llm
# call a real model instead of the always-None default seam.
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_TEMPERATURE = 0

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# "llama-3.1-8b-instant" (the originally requested model) was retired by Groq; this
# is its closest replacement in the account's current catalog (fast, small,
# instruction-tuned) as of 2026-09-27. See implementation-notes.md.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TIMEOUT_SECONDS = 30

SHOW_RETRIEVAL = os.getenv("SHOW_RETRIEVAL", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
