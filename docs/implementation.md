# Implementation Plan: Mutual Fund Facts-Only RAG Chatbot

**Based on:** [architecture.md](./architecture.md) · [PRD.md](./PRD.md)
**Purpose:** Phase-by-phase build instructions to drive an AI coding agent (Cursor) in ordered, verifiable slices.
**Status:** In progress — Phases 1–8 complete
**Last updated:** 2026-09-27

---

## 0. How to use this document

1. Implement **one phase at a time**, in order. Do not start Phase N+1 until Phase N's *Verification* passes.
2. Paste the **Cursor prompt block** for a phase as the task instruction.
3. Run the **Verification** commands yourself before moving on.
4. Tick the phase's **Acceptance criteria** as you go.
5. After every phase, run `git add -A && git commit -m "phase N: <name>"` so each phase is a revertable unit.

### Non-negotiable invariants (every phase must preserve these)

| ID | Invariant | Enforced in |
|----|-----------|-------------|
| INV-1 | No answer without exactly one `source_url` from the 5-URL allowlist | Phase 6 |
| INV-2 | Factual answers ≤ 3 sentences | Phase 6 |
| INV-3 | No user message or PII is ever persisted to disk | Phase 5, 7 |
| INV-4 | Advice and returns questions never reach the generator | Phase 5 |
| INV-5 | All generation is grounded in retrieved chunk text only | Phase 6 |
| INV-6 | The whole demo works offline after `ingest` has been run once | Phase 3 |

### Global conventions for all phases

- Python 3.11+, `src/` layout, module-level type hints, dataclasses for records.
- No classes with business logic — prefer functions + small pure helpers.
- Every module gets a module docstring naming the architecture section it implements (e.g. `"""Ingestion stage 2: structure-aware chunking (architecture.md §6)."""`).
- No inline comments unless the code is genuinely non-obvious. Use docstrings instead.
- Config values live in `src/config.py` as module constants. No magic numbers in logic modules.
- Errors surface as domain exceptions in `src/errors.py` (`CorpusError`, `IngestError`, `RetrievalError`, `GenerationError`, `PolicyRefusal`). The UI catches them and renders a friendly message.
- Path handling uses `pathlib.Path`, never string concat.
- No network calls at query time. No `print()` in library code — use the `logging` module (`src/logging_config.py`).

---

## Phase 0 — Scaffolding & environment

**Goal:** A repo that runs, lints, and imports cleanly, with zero product logic.

### Tasks

1. Create the directory skeleton from architecture.md §10.
2. `requirements.txt` with pinned-ish versions:
   - `chromadb`
   - `sentence-transformers`
   - `streamlit`
   - `requests`, `beautifulsoup4`, `trafilatura` (ingestion only)
   - `python-dotenv`
   - dev: `ruff`, `pytest`
3. `src/config.py` — all tunables as constants:
   - `EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"`
   - `CHROMA_COLLECTION = "mf_faq_hdfc"`
   - `CHROMA_PATH = DATA_DIR / "chroma"`
   - `TOP_K = 4`
   - `SIMILARITY_FLOOR = 0.35` (tuned in Phase 4)
   - `CHUNK_TARGET_TOKENS = 500`, `CHUNK_OVERLAP_RATIO = 0.15`
   - `MAX_ANSWER_SENTENCES = 3`
   - `DATA_DIR`, `CORPUS_DIR`, `SOURCES_FILE`, `LOG_LEVEL`
4. `src/errors.py` — exception classes (see conventions).
5. `src/logging_config.py` — `setup_logging()` returning a configured logger; levels via `LOG_LEVEL`.
6. `src/types.py` — shared dataclasses used across every later phase:
   - `SourceDoc(scheme_id, scheme_name, category, source_url, fetched_at, file_path)`
   - `Chunk(chunk_id, scheme_id, source_url, section, fetched_at, text)`
   - `RetrievalHit(chunk, score)`
   - `Answer(answer_text, source_url, fetched_at, scheme_id, section, used_chunks)`
   - `ChatReply(answer: Answer | None, refusal: Refusal | None, retrieved: list[RetrievalHit])` — a turn is *either* an answer *or* a refusal, never both.
   - `Refusal(kind, message, link_url, link_label)`
7. `src/__init__.py` and empty `__init__.py` in each subpackage.
8. `.gitignore` — ignore `data/chroma/`, `__pycache__/`, `.env`, `.venv/`, `data/raw/`.
9. `.env.example` — `GENERATION_MODE=template|llm`, `LLM_API_KEY=`, `LLM_MODEL=`, `LOG_LEVEL=INFO`.
10. `pyproject.toml` with `[tool.ruff]` line-length 100 and basic rules; `Makefile` with `make ingest`, `make app`, `make test`, `make lint`.

### Cursor prompt

> Create the project scaffolding for a Python 3.11 RAG chatbot. Create the exact directory tree listed in `docs/architecture.md` §10. Add `requirements.txt`, `pyproject.toml` (ruff, line-length 100), `Makefile` (targets: `install`, `ingest`, `app`, `test`, `lint`), `.gitignore` (data/chroma, __pycache__, .venv, .env, data/raw), and `.env.example`. Then create `src/config.py` with the constants listed in my plan, `src/errors.py` with `CorpusError`/`IngestError`/`RetrievalError`/`GenerationError`/`PolicyRefusal`, `src/logging_config.py`, and `src/types.py` with the exact dataclass fields I specified. Add `__init__.py` files everywhere. Do not implement any RAG logic yet.

### Verification

```bash
cd "/Users/prashanttlabde/My files/PM/RAG chatbot"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ruff check .
python -c "from src import config, types, errors; print(config.TOP_K, types.Chunk.__dataclass_fields__.keys())"
```

### Acceptance criteria

- [x] `ruff check .` passes with zero findings
- [x] `python -c "import src.types"` succeeds and all five dataclasses exist
- [x] `make lint` runs
- [x] No RAG logic present yet

**Phase 0 delivered (2026-09-27):** full tree, `requirements.txt`, `pyproject.toml`, `Makefile`
(`install`/`ingest`/`app`/`test`/`lint`/`clean`), `.gitignore`, `.env.example`,
`src/config.py`, `src/errors.py`, `src/logging_config.py`, `src/types.py`, `__init__.py` in every
subpackage, `tests/test_scaffolding.py` (9 tests), and `.cursor/rules/rag-chatbot.mdc`.
`make lint` green, `make test` 9 passed. `make ingest` and `make app` intentionally fail until
Phases 3 and 8.

---

## Phase 1 — Corpus acquisition (Loading)

**Goal:** 5 cleaned, human-verifiable text files plus a source manifest. This is the single source of truth for all later phases.

**Architecture:** §5.1, §6 steps 1–2.

### Tasks

1. `data/sources.yaml` (or `.json` — pick one, be consistent) as the **allowlist and manifest**:

   | scheme_id | scheme_name | category | source_url |
   |---|---|---|---|
   | `hdfc-large-cap-direct-growth` | HDFC Large Cap Fund – Direct Growth | large_cap | `https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth` |
   | `hdfc-equity-flexi-cap-direct-growth` | HDFC Equity (Flexi Cap) Fund – Direct Growth | flexi_cap | `https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth` |
   | `hdfc-elss-tax-saver-direct-growth` | HDFC ELSS Tax Saver – Direct Growth | elss | `https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth` |
   | `hdfc-small-cap-direct-growth` | HDFC Small Cap Fund – Direct Growth | small_cap | `https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth` |
   | `hdfc-balanced-advantage-direct-growth` | HDFC Balanced Advantage Fund – Direct Growth | hybrid | `https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth` |

2. `src/ingest/load.py`:
   - `fetch_page(url) -> str` — `requests` with a browser-like `User-Agent`, 30s timeout, one retry, `raise_for_status()`. Use `trafilatura.extract(..., output_format="txt", include_tables=True)` when available; fall back to BeautifulSoup paragraph extraction.
   - `load_raw(sources) -> dict[str, str]` — map `scheme_id` → raw text.
   - `clean_text(raw) -> str` — drop nav/footer/cookie/script noise, collapse whitespace, normalize unicode, strip zero-width chars.
   - `validate_fact_coverage(scheme_id, text) -> dict` — assert the text contains anchors for: expense ratio, exit load, minimum SIP, benchmark, riskometer, statement/tax guidance, and (for ELSS) lock-in. Return a `{anchor: bool}` report; raise `CorpusError` if a scheme fails **two or more** anchors — that means the scrape missed the page and manual fallback is needed.
   - `write_corpus(sources, raw_map) -> list[SourceDoc]` — writes `data/corpus/<scheme_id>.md` with a YAML front-matter block (`scheme_id`, `scheme_name`, `category`, `source_url`, `fetched_at`) followed by the cleaned body, and returns `SourceDoc` records stamped with `fetched_at = date.today().isoformat()`.
   - `write_manifest(sources) -> None` — regenerates `data/sources.md` (human-readable table: scheme, category, URL, fetched_at) so PRD deliverable #2 is always in sync.
   - `load_corpus() -> list[SourceDoc]` — reads back `data/corpus/*.md`, parsing front-matter. This is the function every later phase calls; it must never hit the network.
3. Manual fallback path (documented in the module docstring, PRD risk §15): if fetching fails, save cleaned text into `data/corpus/<scheme_id>.md` by hand and set `fetched_at` to the date you saved it.
4. CLI: `python -m src.ingest.load` prints a table of `scheme_id`, chars, and the coverage report, then writes files.

### Cursor prompt

> Implement Phase 1 of `docs/implementation.md`. Create `data/sources.yaml` with the 5 HDFC scheme entries (scheme_id, scheme_name, category, source_url) exactly as listed. Create `src/ingest/load.py` exposing `fetch_page(url)`, `clean_text(raw)`, `validate_fact_coverage(scheme_id, text)`, `write_corpus(sources, raw_map)`, `write_manifest(sources)`, and `load_corpus()`. Write each corpus file to `data/corpus/<scheme_id>.md` with YAML front-matter (scheme_id, scheme_name, category, source_url, fetched_at) and the cleaned text body. `load_corpus()` must read from disk only — no network. Add a `__main__` block that fetches, cleans, validates, writes, and prints a coverage table. Use `trafilatura` with a BeautifulSoup fallback. Raise `CorpusError` from `src.errors` if a scheme is missing 2+ fact anchors.

### Verification

```bash
ls -la data/corpus/
python -m src.ingest.load
python -c "from src.ingest.load import load_corpus; d=load_corpus(); print(len(d)); [print(x.scheme_id, x.source_url, x.fetched_at) for x in d]"
head -20 data/sources.md
```

Manually open each `data/corpus/*.md` and confirm: no nav junk, no cookie banners, and that expense ratio / exit load / min SIP / benchmark text is present.

### Acceptance criteria

- [x] Exactly 5 corpus files, each with valid front-matter
- [x] `load_corpus()` returns 5 `SourceDoc` records offline
- [x] Coverage report passes for all 5 schemes
- [x] `data/sources.md` lists all 5 URLs with `fetched_at`
- [x] No content in the corpus outside the 5-URL allowlist

**Phase 1 delivered (2026-09-27):** `data/sources.yaml` (allowlist + manifest),
`src/ingest/load.py`, 5 corpus files (~2.8–3.0k chars, 77–86 lines each, 6 `##`
sections, 6/6 anchors and 7/7 for ELSS), `data/sources.md`, `tests/test_load.py`
(23 tests, one added during Phase 2 to pin a page variation).
`make lint` green. Regenerate with `python -m src.ingest.load`.

**Deviations recorded in [implementation-notes.md](./implementation-notes.md):**
(1) trafilatura scores 0 fact anchors on these pages, so the section-aware DOM walk
is primary and trafilatura is the fallback — both are still raced, chosen by anchor
coverage; (2) the corpus is section-scoped, dropping holdings / returns / compare /
manager-bio blocks that were ~90% of the page; (3) the plan's "statement" anchor does
not exist on these pages, so it is narrowed to `tax_guidance`; (4) the pages' FAQ
JSON-LD is excluded because it is mostly returns and AUM; (5) label/value joining is
allowlist-driven after a generic heuristic invented `Exit load ...: 16 Feb 2015`
pairs; (6) corpus bodies carry `##` headings so Phase 2's heading-based chunking fires;
(7) `SourceRef` added to `src/types.py`.

---

## Phase 2 — Cleaning & structure-aware chunking

**Goal:** Turn each `SourceDoc` into a list of `Chunk`s that map 1:1 to citation-valid sections.

**Architecture:** §5.2, §6 step 3.

### Tasks

1. `src/ingest/chunk.py`:
   - `SECTION_PATTERNS` — ordered regex list of known section headings to preserve: `expense ratio`, `exit load`, `sip`, `minimum (sip )?amount`, `lock[- ]?in`, `riskometer|riskometer level|volatility`, `benchmark`, `nav`, `how to (get|download)|statement|capital gains|tax`, `direct plan|growth option`, `minimum investment`, `diversification`, `minimum amount`.
   - `segment_sections(text) -> list[tuple[str | None, str]]` — scan line by line; a line is a heading if it matches a pattern, is < 80 chars, and is not sentence-terminated. Everything until the next heading is that section's body. Text before the first heading becomes section `None` (preamble). If **fewer than 3** headings are found, return a single `None` section so the caller falls back.
   - `split_fallback(body, target_tokens, overlap_ratio) -> list[str]` — token-ish split on sentence boundaries (split on `(?<=[.!?])\s+`), greedily pack to `CHUNK_TARGET_TOKENS`, overlap by `CHUNK_OVERLAP_RATIO` of sentences. Word count / 0.75 is a good token proxy for English; document that approximation in the docstring.
   - `chunk_document(doc: SourceDoc) -> list[Chunk]`:
     - If the document has ≥ 3 detected sections, chunk per section; further split any section that exceeds `2 * CHUNK_TARGET_TOKENS` using `split_fallback`.
     - Else chunk the whole body with `split_fallback`.
     - Drop chunks with < 30 non-space characters (boilerplate residue).
     - `chunk_id = f"{scheme_id}::{section_slug}::{i:03d}"` — deterministic, so re-running ingest produces stable ids.
     - Every chunk carries `scheme_id`, `source_url`, `section`, `fetched_at`, `text` copied from the parent doc.
2. `chunk_corpus(docs) -> list[Chunk]` convenience wrapper.
3. CLI: `python -m src.ingest.chunk` prints per-scheme section list, chunk count, and the first line of each chunk, plus a `chunk_stats.json` summary (`docs`, `chunks`, `min/avg/max tokens`, `sections_found`).

### Design decision to record

Write the observed section structure of the real corpus into `docs/implementation-notes.md` as you go: which headings were detected per scheme, whether the section path or the fallback path was taken, and the final chunk size/overlap constants. This is the evidence for architecture.md §6's "Chunking decision rule" and PRD §14.

### Cursor prompt

> Implement Phase 2 of `docs/implementation.md`. Create `src/ingest/chunk.py` with `segment_sections(text)`, `split_fallback(body, target_tokens, overlap_ratio)`, `chunk_document(doc)`, and `chunk_corpus(docs)`. Detect sections by matching an ordered list of heading regexes (expense ratio, exit load, SIP, minimum amount, lock-in, riskometer, benchmark, NAV, statement/capital gains/tax, direct plan, minimum investment, diversification). Only use the section path when at least 3 headings are detected; otherwise fall back to sentence-aware packing at `CHUNK_TARGET_TOKENS` with `CHUNK_OVERLAP_RATIO` overlap. Use deterministic chunk ids of the form `scheme_id::section_slug::001`. Copy scheme_id, source_url, section, and fetched_at from the parent SourceDoc into every chunk. Add a `__main__` block that loads the corpus, chunks it, prints per-scheme sections and counts, and writes `data/chunk_stats.json`.

### Verification

```bash
python -m src.ingest.chunk
python - <<'PY'
from src.ingest.load import load_corpus
from src.ingest.chunk import chunk_corpus
chunks = chunk_corpus(load_corpus())
print("total chunks:", len(chunks))
bad = [c.chunk_id for c in chunks if not c.source_url.startswith("https://groww.in/")]
print("non-allowlist urls:", bad)
missing = [c.chunk_id for c in chunks if not c.section]
print("chunks without a section label:", len(missing))
for c in chunks[:8]:
    print(c.chunk_id, "|", (c.section or "-"), "|", c.text[:70].replace("\n", " "))
PY
```

Then read 3 chunks end-to-end and confirm each is coherent on its own and carries a URL.

### Acceptance criteria

- [x] Every chunk has non-empty `chunk_id`, `scheme_id`, `source_url`, `text`
- [x] All `source_url` values are in `data/sources.yaml`
- [x] No chunk is a bare heading or a fragment smaller than 30 chars
- [x] `docs/implementation-notes.md` records the observed section structure and final chunk constants
- [x] Re-running produces identical `chunk_id` values

**Phase 2 delivered (2026-09-27):** `src/ingest/chunk.py` (`SECTION_PATTERNS`,
`segment_sections`, `split_fallback`, `chunk_document`, `chunk_corpus`, `summarize`,
`write_chunk_stats`, CLI), `data/chunk_stats.json`, `tests/test_chunk.py` (37 tests).
**30 chunks, 6 per scheme, 21/101/208 tokens (min/avg/max)** against a 500-token
target; the section path was taken for all 5 schemes and the size fallback never
ran. Re-running the CLI twice produced byte-identical stdout and `chunk_stats.json`.

**Deviations recorded in [implementation-notes.md](./implementation-notes.md):**
(8) `## ` headings are treated as authoritative and the `SECTION_PATTERNS` keyword
heuristic only runs on a body with no `##` — applied literally it over-fired inside
`## Understand terms` and produced 55 chunks with some as small as 5 tokens;
(9) `split_fallback` treats a newline as a hard sentence boundary, so terminator-less
fact lines are not glued to the next line.

---

## Phase 3 — Embedding & vector store

**Goal:** A persistent Chroma collection that Phase 4 can query offline.

**Architecture:** §6 steps 4–5, §12.

### Tasks

1. `src/ingest/embed.py`:
   - Lazy singleton `_get_model()` returning `SentenceTransformer(config.EMBEDDING_MODEL)`; load once per process, not per chunk.
   - `embed_texts(texts, batch_size=32) -> np.ndarray` — `normalize_embeddings=True`, batched, with a progress log every batch.
   - `embed_query(text) -> np.ndarray` — the **same** model and the same normalization. This symmetry is required for cosine similarity to mean anything; add a docstring note.
2. `src/ingest/store.py`:
   - `get_collection()` — `chromadb.PersistentClient(path=str(CHROMA_PATH))`, `get_or_create_collection(name=CHROMA_COLLECTION, metadata={"hnsw:space": "cosine"})`.
   - `store_chunks(chunks) -> int` — upsert with `ids=[c.chunk_id]`, `documents=[c.text]`, `embeddings=embed_texts(...)`, `metadatas=[{k: v for scheme_id, source_url, section, fetched_at}]`. `section=None` must be written as `""`, since Chroma rejects `None` metadata.
   - `reset_collection() -> None` — delete and recreate. Required for idempotent re-ingestion.
   - `collection_stats() -> dict` — count + a sample `fetchitems` for the CLI report.
3. `src/ingest/pipeline.py` — the orchestrator the Makefile calls:
   - `run_ingest(rebuild: bool = True) -> IngestReport`
   - Steps: `load_corpus()` → `chunk_corpus()` → `store_chunks()` → `collection_stats()`; log timings per stage; raise `IngestError` with the failing stage name on any failure.
   - Return a report dataclass `IngestReport(docs, chunks, collection_count, stage_timings)`.
4. CLI: `python -m src.ingest.pipeline --rebuild` and `--stats`.
5. `.gitignore` keeps `data/chroma/` out of version control; the README will say "run `make ingest` after clone".

### Cursor prompt

> Implement Phase 3 of `docs/implementation.md`. Create `src/ingest/embed.py` with a lazy-loaded `SentenceTransformer(config.EMBEDDING_MODEL)` singleton, `embed_texts(texts, batch_size=32)` using `normalize_embeddings=True`, and `embed_query(text)` using the same model and normalization. Create `src/ingest/store.py` with `get_collection()` (PersistentClient, cosine space, get_or_create), `store_chunks(chunks)` that upserts ids/documents/embeddings/metadata — writing `""` instead of `None` for a missing section because Chroma rejects None metadata — plus `reset_collection()` and `collection_stats()`. Create `src/ingest/pipeline.py` exposing `run_ingest(rebuild=True)` returning an `IngestReport`, wired to CLI flags `--rebuild` and `--stats`, logging per-stage timings and raising `IngestError` naming the failed stage. Wire `make ingest` to `python -m src.ingest.pipeline --rebuild`.

### Verification

```bash
make ingest
python -m src.ingest.pipeline --stats
python - <<'PY'
from src.ingest.store import get_collection
col = get_collection()
print("count:", col.count())
r = col.query(query_texts=["what is the exit load"], n_results=2, include=["metadatas","distances","documents"])
for m, d, doc in zip(r["metadatas"][0], r["distances"][0], r["documents"][0]):
    print(round(d,3), m["scheme_id"], "|", m["section"], "|", doc[:60])
PY
```

Run the above twice: the second `make ingest` must not duplicate documents (`count` stays the same after `--rebuild`).

### Acceptance criteria

- [x] `data/chroma/` persists and reloads across processes
- [x] Collection count equals chunk count from Phase 2
- [x] Re-running `--rebuild` is idempotent
- [x] A raw Chroma query for "exit load" returns chunks whose metadata matches the expected scheme
- [x] All metadata values are non-`None`

**Phase 3 delivered (2026-09-27):** `src/ingest/embed.py` (`_get_model` singleton,
`embed_texts`, `embed_query`), `src/ingest/store.py` (`get_collection`,
`collection_exists`, `store_chunks`, `reset_collection`, `collection_stats`),
`src/ingest/pipeline.py` (`run_ingest`, CLI `--rebuild`/`--stats`),
`tests/test_store.py` (18 tests) and `tests/test_pipeline.py` (20 tests).
**5 docs → 30 chunks → 30 stored**, verified idempotent across three runs
(rebuild, rebuild, upsert) and readable from a separate process. A raw query for
"what is the exit load" returns the `Exit load, stamp duty and tax` section of
`hdfc-large-cap-direct-growth` at distance 0.499 (similarity 0.501). At the time of
delivery the metadata keys were exactly `scheme_id`/`source_url`/`section`/`fetched_at`
with zero `None` values and zero empty sections; **`chunk_id` was added in Phase 4**
(see deviation 12), so the contract is now five keys. `make lint` green,
`make test` 101 passed at Phase 3 delivery, 168 passed after Phase 4.
`make ingest` was already wired to `python -m src.ingest.pipeline --rebuild`, and
`.gitignore` already excluded `data/chroma/`.

**No deviations from the plan.** One addition beyond the plan: a
`--dump-embeddings` flag writing `data/embeddings_dump.txt`, a human-readable view
of the store (all 30 × 384 floats read back from Chroma, plus a nearest-neighbour
table and a byte-identical-chunk report). It exists because the vectors are
otherwise only visible as a binary BLOB in `chroma.sqlite3`.

Findings recorded in [implementation-notes.md](./implementation-notes.md): the
MiniLM download is the project's only network need and INV-6 was verified with
`HF_HUB_OFFLINE=1` (offline query returned the ELSS hero block at similarity
0.532); the plan's four pipeline stages mean embedding time is reported under
`store`; unfiltered retrieval returns a cross-scheme sibling section at 0.483,
which is why Phase 4 needs the `scheme_id` filter; third-party `httpx` /
`huggingface_hub` INFO logging was downgraded so the per-stage timings
architecture.md §14 asks for stay visible.

**Carried into Phase 4:** 30 chunks hold only **19 distinct texts** — 16 are
byte-identical copies, because Groww renders identical boilerplate (`Understand
terms` x5, `Minimum investments` x4, `Exit load` x3, `Fund house` x2+x2) for
every scheme. With `TOP_K=4`, a boilerplate question fills all four slots with one
fact under four citations, and a `SIMILARITY_FLOOR` sweep run against this corpus
measures duplicate self-similarity. The floor tuning should dedupe by text hash
first, or the floor will be set too high.

---

## Phase 4 — Retriever

**Goal:** A single function the chat service calls: query in, ranked grounded chunks out.

**Architecture:** §7 steps 2–4, §12.

### Tasks

1. `src/retrieve/retriever.py`:
   - `SCHEME_ALIASES: dict[str, str]` — map human mentions to `scheme_id`: `"large cap"`, `"hdfc large cap"` → `hdfc-large-cap-direct-growth`; `"elss"`, `"tax saver"`, `"80c"`, `"80c"`, `"tax"` → `hdfc-elss-tax-saver-direct-growth`; `"flexi cap"`, `"hdfc equity fund"` → equity/flexi; `"small cap"` → small cap; `"balanced advantage"`, `"hybrid"` → balanced advantage. Keep this table in one place; it is also used by the demo script.
   - `detect_scheme_id(query) -> str | None` — longest-alias-first match on a lowercased, punctuation-stripped query. Return `None` when ambiguous or absent.
   - `similarity_floor: float` — tunable, start at `config.SIMILARITY_FLOOR`.
   - `retrieve(query, top_k=None, scheme_id=None) -> RetrievalResult`:
     - If `scheme_id` is `None`, call `detect_scheme_id(query)`.
     - Build a Chroma `where` filter `{"scheme_id": scheme_id}` only when a scheme was detected.
     - Query with `n_results = min(top_k or TOP_K, collection.count())`, `include=["documents", "metadatas", "distances"]`.
     - Convert cosine distance → similarity via `similarity = 1.0 - distance`.
     - Filter out hits below `similarity_floor`.
     - Return `RetrievalResult(query, hits: list[RetrievalHit], detected_scheme_id, considered_count, floor)`.
     - Raise `RetrievalError` if the collection is missing or empty, with the message "run `make ingest` first".
   - Keep a module-level `functools.lru_cache` on the collection handle and the embedding model so repeated calls in a Streamlit session don't re-load MiniLM.
2. `src/retrieve/__init__.py` exporting `retrieve`, `detect_scheme_id`, `RetrievalResult`.
3. Tuning script `scripts/tune_floor.py`:
   - A hand-labelled set of ~15 queries (8 answerable, 8 out-of-corpus like "What is the NAV of HDFC Mid Cap?" or "How do I file a tax grievance?").
   - For floors in `[0.20, 0.25, ..., 0.60]`, print precision (answerable queries that returned ≥1 hit) and false-answer rate (out-of-corpus queries that returned ≥1 hit).
   - Pick the floor that keeps false answers at 0 while retaining ≥ 7/8 answerable queries. Write the chosen value and the table into `docs/implementation-notes.md`, then set `config.SIMILARITY_FLOOR`.
4. `tests/test_retriever.py`:
   - "exit load of HDFC large cap" returns hits from `hdfc-large-cap-direct-growth`
   - "ELSS lock in period" returns hits from the ELSS scheme
   - an off-corpus query returns zero hits at the tuned floor

### Cursor prompt

> Implement Phase 4 of `docs/implementation.md`. Create `src/retrieve/retriever.py` with a `SCHEME_ALIASES` table mapping phrases like "large cap", "flexi cap", "elss", "80c", "tax saver", "small cap", "balanced advantage" to scheme ids; `detect_scheme_id(query)` doing longest-alias-first matching; and `retrieve(query, top_k=None, scheme_id=None) -> RetrievalResult` that embeds the query with `src.ingest.embed.embed_query`, applies a `where` metadata filter on `scheme_id` when one is detected, converts cosine distance to similarity, drops hits below `config.SIMILARITY_FLOOR`, and raises `RetrievalError` when the collection is empty. Cache the collection and model with `lru_cache`. Create `scripts/tune_floor.py` with ~15 labelled queries that sweeps floors 0.20–0.60 and prints precision and false-answer rate, and `tests/test_retriever.py` with the three cases I listed. Run the tuner, pick the floor that gives zero false answers, and record the table in `docs/implementation-notes.md`.

### Verification

```bash
python scripts/tune_floor.py
pytest tests/test_retriever.py -v
```

### Acceptance criteria

- [x] Scheme-scoped queries only return chunks from that scheme
- [x] The tuned floor gives 0 false answers on the labelled set
- [x] Off-corpus questions return an empty `hits` list rather than weak chunks
- [x] `RetrievalError` is raised (not a crash) when ingest has not been run

**Phase 4 delivered (2026-09-27):** `src/retrieve/retriever.py` (`SCHEME_ALIASES`,
`OUT_OF_CORPUS_SCHEMES`, `OUT_OF_CORPUS_FUND_HOUSES`, `normalize_query`,
`detect_scheme_id`, `detect_out_of_corpus_scheme`, `detect_out_of_corpus_fund_house`,
`content_terms`, `_is_lexically_grounded`, `_dedupe_by_text`, `retrieve` with an
`lru_cache`d per-path collection handle), `src/retrieve/__init__.py`,
`scripts/tune_floor.py`, `tests/test_retriever.py` (70 tests). `SIMILARITY_FLOOR` tuned
**0.35 → 0.40**. Full suite 178 passed, lint green.

All four acceptance criteria verified against the real store. *"What is the exit load
of HDFC Large Cap?"* returns only `hdfc-large-cap-direct-growth`; *"ELSS lock in
period"* returns only the ELSS scheme with `ELSS 3Y Lock in` in the text; all 8
hand-checked out-of-corpus queries return an empty `hits` list; a missing store raises
`RetrievalError` naming `make ingest` rather than a Chroma traceback.

**The plan's central assumption did not hold, and that is the main finding.** A single
similarity floor cannot separate answerable from out-of-corpus questions on this
corpus. Measured: answerable queries span 0.433–0.728, out-of-corpus span
0.261–**0.716**, so the worst out-of-corpus question outranks four of the eight
answerable ones and no threshold exists that separates them. Retrieval therefore adds
two gates that are facts about a closed 5-URL corpus rather than guesses about a score
distribution — out-of-corpus scheme detection (short-circuits before searching) and
answer-term grounding (the question's content words must appear in the retrieved
text). The floor is now a backstop: **7 of the 8 out-of-corpus queries are rejected
without reference to it.**

**Deviations recorded in [implementation-notes.md](./implementation-notes.md):**
(10) the two gates above, because the floor provably cannot do the job;
(10a) the out-of-corpus check split into a fund-house tier and a scheme-category
tier — writing acceptance checks outside the labelled set exposed that *"What is the
exit load of Mirae Asset Large Cap?"* was answered with HDFC Large Cap's exit load,
because an indexed alias suppressed the denylist entirely;
(11) over-fetch `top_k * RETRIEVE_OVERFETCH` before collapsing duplicates, so
duplicate collapse still leaves a full budget of distinct facts;
(12) `chunk_id` added to Chroma metadata, because without it every
`RetrievalHit.chunk.chunk_id` was silently `""`;
(13) the labelled set has a third label, `guard_blocked`, for the advice/returns/
grievance prompts Phase 5 refuses before retrieval — they are printed by the tuner but
deliberately **not scored**, since counting them would flatter the floor by measuring
another layer's job.

---

## Phase 5 — Guardrails / policy layer

**Goal:** A deterministic gate that runs **before** any retrieval, so advice, returns, and PII never reach the generator (INV-4) and never touch disk (INV-3).

**Architecture:** §7 step 1, §8.

### Tasks

1. `src/guards/policy.py`. Return a `GuardDecision(allowed: bool, kind: str | None, message: str | None, link_url: str | None, link_label: str | None)`. `kind` ∈ `{"pii", "advice", "returns", "grievance", "out_of_scope", None}`.
2. **PII detection** (`_detect_pii(text) -> list[str]`) — ordered regexes, each with a label used only for the log line, never the value:
   - Aadhaar: `\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b`
   - PAN: `\b[A-Z]{5}\d{4}[A-Z]\b` (case-insensitive)
   - Email, phone (`\+?\d[\d\s\-]{8,14}\d`), 4–6 digit OTP context (`\b(otp|one time password)\b.*\b\d{4,6}\b`), account number context (`\b(account|a/c|acc no|ifsc|folio)\b.*\b\d{6,}\b`)
   - **Never** include the matched substring in logs, errors, or returned messages. Log only `pii_detected=<label>`.
3. **Intent gates** — regex + keyword heuristics, each a named pattern list in a module constant:
   - `ADVICE_PATTERNS`: `should i (buy|sell|invest|switch)`, `which (fund|scheme) (should|is best)`, `best (fund|scheme|mutual fund)`, `is it (a )?good (time|investment)`, `recommend`, `suggest (a|which)`, `suitable for me`, `can i (buy|sell)`, `worth (buying|investing)`
   - `RETURNS_PATTERNS`: `returns?`, `cagr`, `xirr`, `performance`, `how much (did|has) .* (return|earn)`, `best performing`, `ranking`, `top performing`, `1 year return`, `since inception return`, `compare .* (return|performance)`
   - `GRIEVANCE_PATTERNS`: `complaint`, `grievance`, `regulator`, `sebi`, `rbi`, `refund.*(reversed|failed)`, `money.*(stuck|missing)`, `chargeback`, `legal`
   - Order of evaluation: **PII → advice → returns → grievance**. PII wins because it must never be echoed back.
4. **Copy** (exact strings, in `src/guards/copy.py` so they are easy to review and demo):
   - PII: "I can't help with that. Please don't share PAN, Aadhaar, account numbers, OTPs, or other personal details here. I don't store anything you type."
   - Advice: "I'm a facts-only assistant, so I can't recommend a scheme or tell you whether to buy or sell. I can share published scheme facts like expense ratio, exit load, and minimum SIP. For guidance, see AMFI's investor education resources: <link>"
   - Returns: "I don't compute or compare returns. The scheme's official factsheet on the source page has the published performance figures: <link>"  (link = the scheme page if one was detected, else the corpus allowlist index)
   - Grievance: "This looks like a complaint or regulator matter, which I can't handle. Please raise it through the AMC's official grievance channel or SEBI's SCORES portal: <link>"
   - Educational links as module constants: AMFI investor education, SEBI SCORES, HDFC MF contact page. Keep the allowlist rule in mind — **educational links are the one place a non-corpus URL may appear**, and only inside a refusal.
5. **Logging:** log `kind` and a redacted 60-char prefix of the query (prefix only, and only when no PII was detected). Never log the full text of a PII-flagged message.
6. `tests/test_policy.py` — table-driven, one case per rule:
   - `"My PAN is ABCDE1234F and Aadhaar 2345 6789 0123"` → `pii`
   - `"Should I buy HDFC Large Cap?"` → `advice`
   - `"Which is the best performing ELSS?"` → `advice` or `returns` (assert the kind is in the set; order is documented)
   - `"What is the 1 year return?"` → `returns`
   - `"How do I raise a grievance with SEBI?"` → `grievance`
   - `"What is the exit load?"` → allowed
   - `"expense ratio of hdfc equity fund"` → allowed
   - No refusal message or exception anywhere contains the fake PAN/Aadhaar strings.

### Cursor prompt

> Implement Phase 5 of `docs/implementation.md`. Create `src/guards/copy.py` holding the exact refusal copy and the educational link constants (AMFI investor education, SEBI SCORES, HDFC MF contact). Create `src/guards/policy.py` with a `GuardDecision` dataclass and `check_query(text) -> GuardDecision` that evaluates in this order: PII detection (Aadhaar, PAN, email, phone, OTP context, account-number context), then ADVICE_PATTERNS, then RETURNS_PATTERNS, then GRIEVANCE_PATTERNS, otherwise allowed. Never include the matched PII substring in any log, message, or exception — log only the label. Put all refusal strings in copy.py, not inline. Create `tests/test_policy.py` as a table-driven test covering one positive and one negative case per rule, and assert that no refusal text ever contains the test PAN or Aadhaar values. Educational links may only appear inside a refusal message.

### Verification

```bash
pytest tests/test_policy.py -v
python - <<'PY'
from src.guards.policy import check_query
for q in ["My PAN is ABCDE1234F", "Should I buy HDFC Large Cap?",
          "1 year return?", "raise a grievance with SEBI", "exit load?"]:
    d = check_query(q)
    print(d.allowed, d.kind, "|", (d.message or "")[:60])
PY
```

Grep the whole repo afterwards to confirm no PII value can escape: the fake PAN/Aadhaar strings must appear only inside `tests/`.

### Acceptance criteria

- [x] Every listed test case routes to the expected `kind`
- [x] Advice/returns/grievance queries are blocked **before** the retriever is called
- [x] No PII substring appears in any log line, response, or exception
- [x] No user query is written to any file

**Phase 5 delivered (2026-09-27):** `src/guards/copy.py` (all refusal copy, the three
educational links, and the persistent disclaimer), `src/guards/policy.py` (`GuardDecision`
with a self-validating contract, `PII_PATTERNS`, `ADVICE_PATTERNS`, `RETURNS_PATTERNS`,
`GRIEVANCE_PATTERNS`, `_detect_pii`, `_detect_intent`, `check_query`), `src/guards/__init__.py`,
`tests/test_policy.py` (107 tests). Full suite **285 passed**, lint green.

All four acceptance criteria verified, not just the routing table:

* **Blocked before retrieval (INV-4)** — with `CHROMA_PATH` pointed at a non-existent
  directory, all four refusal kinds still render, while `retrieve()` on the same absent
  store raises `RetrievalError`. The gate provably needs no vector store, so no refused
  turn can reach a search.
* **No PII substring anywhere** — six fake identifiers are asserted absent from the
  decision, message, link, label, and every captured log record, including on queries
  that also trip an intent gate.
* **No PII even logged** — a flagged query logs only `kind=pii pii_detected=<labels>`. A
  60-char prefix is logged for allowed and non-PII-refused turns only, and a test asserts
  no PII line carries one, because `"My PAN is 1234…"[:60]` is still PII.
* **Nothing written (INV-3)** — 54 guard calls over mixed queries left all 75 repo files
  byte- and mtime-identical.

**The routing was the easy half; all three real defects were in the regexes.** Each was
found by a test asserting a pattern *should* match and finding it did not, not by reading
the table. (14) The specified phone pattern `\+?\d[\d\s\-]{8,14}\d` also matches
`2026-09-27`, so *"What was the exit load as on 2026-09-27?"* was refused as PII — a
silent false refusal, on a corpus that publishes a `fetched_at` date per chunk; a
date-shaped match is now discarded. (15) The specified account/IFSC context form requires
a word boundary before the digits, but an IFSC is four letters then digits, so **it
matched every bare account number and no real IFSC at all**; replaced with a digit
lookaround. (16) `performan(ce|t)` missed *"how has HDFC Flexi Cap performed"*, a natural
phrasing of a performance question. One pattern was **tightened**: bare `rank` was dropped
because this corpus contains `Rank (total assets)`, which would have refused the
answerable "what is HDFC's rank by AUM?".

**No over-refusal, measured rather than assumed.** All 15 factual questions that Phases
6–9 depend on — the 8 `answerable` rows in `scripts/tune_floor.py` plus the 7 from the
plan's own Phase 6/7 examples — route to `allowed`. A guard that refuses a legitimate fact
is worse for this demo than one that lets a performance question through.

**Deviations recorded in [implementation-notes.md](./implementation-notes.md):**
(14) the date/phone exclusion, (15) the IFSC lookaround, (16) the `performed` widening,
plus the one deliberate layering exception — the returns refusal links the named scheme's
page via a lazy, failure-tolerant import of `detect_scheme_id` (a pure string function), so
the guard layer takes no hard dependency on the retrieval stack. Its no-scheme fallback
points at the app's Sources panel rather than a URL, because no such public page exists and
inventing one would be a fabricated citation.

**Carried into Phase 7:** a returns refusal for a *named* scheme carries an allowlist
`groww.in` link (the plan's instruction — it points at the factsheet the copy tells the
user to read), so the "advice reply contains no `groww.in` citation" test must be written
for the advice kind specifically. And because `sebi` is in `GRIEVANCE_PATTERNS`, *"What is
the SEBI registration number of HDFC Mutual Fund?"* becomes a `grievance` refusal in the
app rather than a no-answer; the tuner still calls `retrieve` directly, so its 8
out-of-corpus figures are unaffected.

---

## Phase 6 — Grounded generation & response formatting

**Goal:** Turn `RetrievalResult` into a cited, ≤3-sentence, facts-only `Answer` — with a deterministic template mode as the default so the demo never depends on an API key.

**Architecture:** §7 steps 5–6, §8, §12.

### Tasks

1. `src/generate/prompt.py` — the single source of truth for the grounding contract:
   - `SYSTEM_RULES` string: "You answer only from the provided context. Use only facts present in the context. Never invent numbers, dates, or scheme names. Maximum 3 sentences. No investment advice, no recommendations, no performance comparison. If the context does not contain the answer, reply exactly: NOT_IN_CONTEXT."
   - `build_user_prompt(question, chunks) -> str` — renders each chunk as `[i] (scheme: <name> · section: <section>)\n<text>` with `source_url` listed once at the end for the model to reference.
   - `GROUNDING_CHECK` regex: the sentinel `NOT_IN_CONTEXT`.
2. `src/generate/answer.py`:
   - `compose_context(hits) -> str`
   - `select_primary_source(hits) -> str` — the citation rule. If all hits share one `source_url`, use it. Otherwise use the highest-scoring hit's URL; if two top hits are within 0.05 of each other and have different URLs, treat it as ambiguous and return the first but flag `Answer(ambiguous_source=True)`. **Never** return more than one URL.
   - `truncate_sentences(text, max_sentences=3) -> str` — split on `(?<=[.!?])\s+`, keep the first 3, re-join. If truncation happens, log a warning.
   - `generate_llm(question, hits, client) -> str` — calls the configured chat model with `SYSTEM_RULES` + `build_user_prompt`; temperature 0; on any exception, log and fall back to `generate_template`.
   - `generate_template(question, hits) -> str` — deterministic extractive answer: take the best sentence(s) from the top hit that contain the highest term overlap with the question, trimmed to ≤3 sentences, prefixed with the scheme name. This is the default path and must produce a sensible answer for all 5–10 sample questions.
   - `answer_question(question, hits) -> Answer` — full path: primary source → generation (llm or template by `GENERATION_MODE`) → sentence cap → grounding sentinel check → `NOT_IN_CONTEXT` becomes an `Answer` with `answer_text=None` so the chat service can route to the no-answer path. Populate `scheme_id`, `section`, `fetched_at` from the primary hit.
3. `src/generate/no_answer.py`:
   - `no_answer_message(hits) -> str` — "I don't have that in the indexed scheme pages." Plus, when a scheme was detected, "The closest page I have is <scheme name>: <url>" and the list of the 5 schemes I *do* cover. No fabricated numbers.
4. `tests/test_answer.py`:
   - `select_primary_source` returns a single URL in all three cases (same URL / different URLs / ambiguous)
   - `truncate_sentences` never exceeds 3 sentences
   - `answer_question` with an off-topic `hits` list returns the sentinel → `answer_text is None`
   - Every generated answer string contains no URL except the single citation (cites ≠ extra links)

### Cursor prompt

> Implement Phase 6 of `docs/implementation.md`. Create `src/generate/prompt.py` with `SYSTEM_RULES` (context-only, no invented numbers, max 3 sentences, no advice or performance comparison, reply exactly NOT_IN_CONTEXT if the context lacks the answer), `build_user_prompt(question, chunks)`, and the `GROUNDING_CHECK` sentinel. Create `src/generate/answer.py` with `compose_context`, `select_primary_source` (one URL only: shared URL, else top hit, flag ambiguity when two hits within 0.05 differ), `truncate_sentences`, `generate_template` (extractive — pick sentences from the top hit with the highest question term overlap, cap at 3 sentences, prefix the scheme name), `generate_llm` (temperature 0, fall back to template on any exception), and `answer_question(question, hits) -> Answer` that routes `GENERATION_MODE`. Create `src/generate/no_answer.py` with `no_answer_message(hits)`. Create `tests/test_answer.py` covering single-source selection, ambiguous selection, the 3-sentence cap, the NOT_IN_CONTEXT sentinel, and the rule that an answer body never contains more than one URL.

### Verification

```bash
GENERATION_MODE=template pytest tests/test_answer.py -v
python - <<'PY'
from src.ingest.load import load_corpus
from src.ingest.chunk import chunk_corpus
from src.retrieve.retriever import retrieve
from src.generate.answer import answer_question
for q in ["What is the exit load of HDFC Large Cap?",
          "What is the minimum SIP amount?",
          "What is the lock in period for ELSS?",
          "What is the expense ratio of HDFC Small Cap?"]:
    r = retrieve(q)
    a = answer_question(q, r.hits)
    print("Q:", q); print("  A:", a.answer_text); print("  cite:", a.source_url, "| updated:", a.fetched_at)
PY
```

### Acceptance criteria

- [x] Every answer has exactly one citation URL
- [x] No answer exceeds 3 sentences
- [x] Off-corpus questions return the no-answer message, not a guess
- [x] `GENERATION_MODE=template` works with no API key and no network
- [x] No fabricated numbers appear in any template answer (spot-check against corpus files)

**Phase 6 delivered (2026-09-27):** `src/generate/prompt.py` (`SYSTEM_RULES`,
`build_user_prompt`, `GROUNDING_CHECK`), `answer.py` (`compose_context`,
`select_primary_source`, `is_ambiguous_source`, `split_units`, `truncate_sentences`,
`strip_urls`, `generate_template`, `generate_llm`, `answer_question`), `no_answer.py`,
and `tests/test_answer.py` (64 tests). Suite **349 passed**, `make lint` clean. The plan's
verification block answers all four questions with one allowlisted citation each.

Deviations, all recorded in `implementation-notes.md` §Phase 6:

1. **A unit is a newline or a real sentence terminator, not any terminator.** The plan's
   `(?<=[.!?])\s+` splits `Min. for SIP: ₹100` at the period in `Min.`, separating the
   amount from its label. `split_units` treats a period after an abbreviation as a label
   ending. This is the load-bearing change; INV-2 still holds on both prose and
   label/value blocks.
2. **`generate_template` answers from one hit, never two.** Following the plan's "from the
   top hit" literally is a correctness requirement: two schemes' SIP minimums in one answer
   is a contradiction, not an answer.
3. **`generate_llm` falls back when no client is wired up** instead of raising, per "on
   any exception". `_build_client()` is the single seam for enabling the LLM path; no SDK
   is a dependency, so it returns `None` and the default template path never touches it.
4. **`content_terms` gained `strip_entities=False`**, used only when entity stripping
   removes every content word. Default behaviour is unchanged, so Phase 4's grounding and
   its 178 tests are untouched.
5. **Unit ranking prefers the unit carrying a value**, and a page title is dropped when
   something else was also selected. Both are tie-breaks for questions whose words are
   split across a heading and its value.

Three recall gaps were found and deliberately **not** fixed here because they belong to
Phase 4's tuned floor and matcher: `nav` is over-stripped as an entity term, `\b{term}\b`
does not match `Lumpsum` with the term `lump` (so a lump-sum question can be answered with
the SIP minimum), and naming a scheme lifts similarity by 0.15–0.48, which puts 5 of 7
unscoped attribute questions below the floor. Generation refuses rather than guessing in
all three (INV-5). Details and options in the notes.

---

## Phase 7 — Chat service orchestration

**Goal:** One function the UI calls. Owns the runtime sequence from architecture.md §11.

**Architecture:** §7 as a whole, §11.

### Tasks

1. `src/chat_service.py`:
   - `ChatTurn(request_id, question, retrieved, answer, refusal, decision_kind, latency_ms)` — a **transient** record returned to the UI for the demo debug panel only. It is never written to disk. Add a module docstring stating this explicitly (INV-3).
   - `handle_message(question: str) -> ChatReply`:
     1. Normalize: strip, collapse whitespace, cap at 500 chars. Empty → `Refusal(kind="empty", message="Please type a question about a scheme fact.")`.
     2. `check_query(question)` → if not allowed, return the refusal immediately. **No retrieval.** (INV-4)
     3. `retrieve(question)` → if `hits` is empty, return the no-answer path with `Answer(answer_text=None, source_url=best-effort or None)`.
     4. `answer_question(question, hits)`.
     5. `format_reply(...)` → render the final string: `answer_text`, then `Source: <url>`, then `Last updated from sources: <fetched_at>`, then the persistent disclaimer.
   - `format_reply(answer) -> str` — the **only** place that assembles the user-visible text. Keep the citation and freshness lines as separate lines so the UI can style them.
   - `EXAMPLE_QUESTIONS: list[str]` — the 3 for the UI (one expense ratio, one exit load / SIP, one ELSS lock-in) plus a `SAMPLE_QUESTIONS` list of 8–10 for the samples file.
   - `EXPERIMENTAL: SHOW_RETRIEVAL` env flag — when true, the reply includes retrieved `scheme_id`/`section`/score lines for the instructor demo (architecture.md §14).
   - Guard the whole body in `try/except`: domain exceptions → a friendly `Refusal(kind="error", ...)`; log the exception type, never the query text if PII was flagged.
2. Latency logging per stage (`guard`, `retrieve`, `generate`, `total`) at `INFO`.
3. `tests/test_chat_service.py`:
   - Factual question → reply contains exactly one `https://groww.in/` URL and a "Last updated from sources:" line
   - Advice question → reply contains the AMFI link and **no** `groww.in` citation, and the retriever was never invoked (assert with a monkeypatched spy)
   - PII question → reply does not contain the test PAN
   - Empty input → friendly message
   - No file is created by handling 50 messages (snapshot the `data/` tree before and after)

### Cursor prompt

> Implement Phase 7 of `docs/implementation.md`. Create `src/chat_service.py` with a `ChatTurn` record, `EXAMPLE_QUESTIONS` (3 items), `SAMPLE_QUESTIONS` (8-10 items), `format_reply(answer)` as the single place that assembles the visible text (answer body, then `Source: <url>`, then `Last updated from sources: <fetched_at>`, then the disclaimer), and `handle_message(question) -> ChatReply` that normalizes input, runs `check_query` and returns immediately on refusal without calling the retriever, calls `retrieve`, routes empty hits to the no-answer path, then `answer_question`, then `format_reply`. Wrap in try/except for domain exceptions. Log per-stage latency. Honour a `SHOW_RETRIEVAL` env flag to append retrieved scheme/section/score lines. Nothing may be persisted. Create `tests/test_chat_service.py` asserting: a factual reply has exactly one groww.in URL plus a last-updated line; an advice reply has the AMFI link, no groww citation, and the retriever spy was never called; a PII reply never echoes the PAN; empty input is handled; and handling 50 messages creates no files under `data/`.

### Verification

```bash
pytest tests/test_chat_service.py -v
find data -type f -newermt '-1 minute' | wc -l   # should be 0 after a 50-message run
```

### Acceptance criteria

- [x] The three-step sequence guard → retrieve → generate is visible in the code and in the logs
- [x] Refusals never trigger a vector search
- [x] `data/` is unchanged after a 50-turn session
- [x] `format_reply` is the only function producing user-visible text

**Phase 7 delivered (2026-09-27):** `src/chat_service.py` (`ChatTurn`, `recent_turns()`,
`EXAMPLE_QUESTIONS` (3), `SAMPLE_QUESTIONS` (8), `format_reply`, `handle_message`),
`tests/test_chat_service.py` (29 tests). Full suite **378 passed**, lint green.

`handle_message` is guard → retrieve → generate → format in that literal order, with a
per-stage `logger.info` after each one and a `total` line at the end; a monkeypatched
`retrieve` spy that raises if called confirms advice/returns/PII/grievance queries never
reach it. A 50-turn mixed session (factual, advice, PII, returns, off-corpus, empty)
leaves `data/` byte- and mtime-identical, checked after warming the store once first (see
deviation below). Every domain exception is caught and rendered as a refusal; the except
block logs only `type(exc).__name__`, never `question` or the normalized text.

**Deviations recorded in [implementation-notes.md](./implementation-notes.md):**
(17) `handle_message` catches `RetrievalError` itself and returns a `make ingest` refusal
rather than letting it reach the UI, which changes what Phase 8 needs to catch; (18) the
`data/` snapshot test must warm the Chroma collection once before snapshotting, because
Chroma's persistent client touches (not resizes) its own index file mtimes on first open
per process — unrelated to anything this module writes, but order-dependent if untreated;
(19) an answer with text but no citation is treated as a contract break and routed to
no-answer rather than ever being shown (INV-1 fails closed).

---

## Phase 8 — UI

**Goal:** The demo surface from architecture.md §9 and PRD §6.2 / §9.

### Tasks

1. `src/app.py` (Streamlit), single page, `st.set_page_config(page_title="HDFC MF Facts Assistant", layout="centered")`:
   - **Welcome line** (FR-8): "Ask about HDFC mutual fund scheme facts — expense ratio, exit load, minimum SIP, lock-in, benchmark, riskometer."
   - **Persistent disclaimer** via `st.caption(...)` or a fixed `st.info` with the exact Appendix A copy from PRD.
   - **3 example questions** as `st.button`s from `EXAMPLE_QUESTIONS`; clicking sets `st.session_state["question"]` and submits.
   - **Chat area**: `st.chat_input`; user bubbles right/left aligned; assistant message rendered with `st.markdown` and `st.info(citation)`.
   - **Session state**: `st.session_state["messages"]` holds the current conversation, in memory only, plus `st.session_state["last_turns"]` for the debug panel. Add a comment-free docstring noting nothing is written to disk and the buffer dies with the session (architecture.md §17).
   - **Empty state**: if the Chroma collection is missing, show a clear "Run `make ingest` first" panel instead of a traceback (catch `RetrievalError`).
   - **Error state**: catch `GenerationError` / domain errors and show the friendly message.
   - **Sidebar**: corpus coverage list (5 schemes + `fetched_at`), a `Show retrieved chunks` checkbox (wired to the `SHOW_RETRIEVAL` debug view), and a `Reset conversation` button.
   - Accessibility/robustness: no `unsafe_allow_html` with unescaped content; no user text echoed into markdown that could inject links.
2. Make it launchable: `streamlit run src/app.py` via `make app`.
3. `README.md` quickstart section stub (filled in Phase 10):
   ```
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   make ingest
   make app
   ```

### Cursor prompt

> Implement Phase 8 of `docs/implementation.md`. Create `src/app.py` as a single-page Streamlit app that renders a one-line welcome, the PRD Appendix A disclaimer as a persistent caption, three example-question buttons wired to `EXAMPLE_QUESTIONS` in `src/chat_service.py`, a `st.chat_input` chat area, and a sidebar showing the 5-scheme corpus coverage with `fetched_at`, a "show retrieved chunks" checkbox, and a reset button. All messages go through `handle_message` in `src/chat_service.py`; the app must contain no business logic. Catch `RetrievalError` and show a "Run make ingest first" panel, and catch domain errors to show a friendly message. Keep chat state only in `st.session_state` and never write to disk. Do not use `unsafe_allow_html` on user-supplied text. Wire `make app` to `streamlit run src/app.py`.

### Verification

```bash
make app   # then click all 3 examples, ask 1 advice question, 1 PII question, 1 nonsense question
ruff check .
```

Manual checklist:

- [x] Disclaimer visible on first load
- [x] Example buttons populate and send
- [x] Answer shows one clickable citation + last-updated line
- [x] Advice question shows the refusal + AMFI link and no citation
- [x] PII question shows the do-not-share message and no PAN text
- [x] Nonsense question shows the "don't have that" message
- [x] Missing-ingest state shows the setup panel, not a stack trace

**Phase 8 delivered (2026-09-27):** `src/app.py` (single-page Streamlit UI: welcome
caption, PRD Appendix A disclaimer, 3 example buttons, chat area, sidebar with
corpus coverage + show-retrieved-chunks checkbox + answered/refused counters +
reset button), `tests/test_app.py` (15 tests, driven headlessly via
`streamlit.testing.v1.AppTest` against the real app and store), `README.md`
quickstart stub. Full suite **403 passed**, lint green. Every item in the manual
checklist above was verified both by an automated `AppTest` and, for the launch
path itself, by starting the real server and curling it.

**Three real bugs were found and fixed, one of them launch-blocking:**

1. **`streamlit run src/app.py` (i.e. `make app`) failed outright** with
   `ModuleNotFoundError: No module named 'src'`. Streamlit puts only the script's
   own directory on `sys.path`, not the project root, so `from src... import`
   fails -- unlike `python -m` or pytest, which both handle this automatically.
   `AppTest`-based tests never caught this because they run inside the pytest
   process, which already has the project root on `sys.path`. Fixed with a
   `sys.path.insert` at the top of `app.py`, before any `src` import; verified by
   starting a real server and curling it (clean, versus a traceback before the fix).
2. **The sidebar's answered/refused counters permanently lagged one turn behind**
   the chat area. They were computed in the same `with st.sidebar:` block as the
   corpus list, which runs *before* the current turn is appended to
   `st.session_state["messages"]` later in the script. Fixed by writing the
   sidebar in two passes -- corpus list and checkbox first (needed early, for the
   history-rendering loop), counts and the reset button in a second
   `with st.sidebar:` block after the new turn is appended. Streamlit appends to
   the same sidebar container across multiple `with` blocks in one run, so this is
   still one sidebar. Regression test:
   `TestSidebarCounters::test_counts_reflect_the_turn_that_was_just_submitted`.
3. **`guards/copy.py`'s `DISCLAIMER` was not actually the PRD's Appendix A text**,
   despite its own comment claiming it was (a Phase 5 discrepancy that had gone
   unnoticed until this phase needed to render "the exact Appendix A copy from
   PRD"). Fixed by replacing it with the literal PRD.md text; since
   `format_reply` (Phase 7) already appends this same constant to every factual
   answer, the fix also corrected that path, and the README (Phase 10) will now
   copy the same corrected constant rather than propagating the mismatch further.

**A fourth issue was caught before it could do damage**, not shipped: an early
version of `tests/test_app.py`'s missing-ingest test simulated "no store" by
pointing `config.CHROMA_COLLECTION` at a name that doesn't exist. `store.
get_collection()` uses `get_or_create_collection`, so retrieving against that name
*created* a real, permanent, empty collection in the actual `data/chroma` store on
disk. Caught immediately (`client.list_collections()` showed the stray name),
cleaned up, and the test rewritten to mock `collection_exists` and `retrieve`
directly instead of touching the real store.

Deviations recorded in [implementation-notes.md](./implementation-notes.md).

---

## Phase 9 — Samples, tests, observability

**Goal:** The submission evidence: sample Q&A, a test suite, and a demo debug view.

### Tasks

1. `samples/qa_samples.md` — 8–10 rows, each with: `#`, Question, Answer (≤3 sentences, verbatim from the app), Citation URL, Scheme, Section, `fetched_at`, and a Notes column for anything surprising (e.g. "answered from the 'Exit load' section, floor 0.41"). Include at least 2 refusal rows (advice, returns) labelled as such. Generate it by running the app's own pipeline so the answers are real, not typed by hand.
2. `tests/` — the full suite:
   - `tests/conftest.py` — session-scoped fixture that asserts the Chroma collection exists and skips (with a clear reason) if not; a `sample_question` fixture.
   - `tests/test_policy.py` (Phase 5), `tests/test_retriever.py` (Phase 4), `tests/test_answer.py` (Phase 6), `tests/test_chat_service.py` (Phase 7).
   - `tests/test_e2e.py` — the PRD success metrics as assertions:
     - **citation coverage**: over the 8 answerable sample questions, 100% of replies contain exactly one allowlist URL
     - **refusal consistency**: all 4 advice-style prompts are refused
     - **answer length**: every factual reply is ≤ 3 sentences
     - **freshness note**: every factual reply contains "Last updated from sources:"
   - `tests/test_corpus_integrity.py` — every `source_url` in the collection appears in `data/sources.yaml`; every chunk's `fetched_at` is a valid ISO date; no chunk text is shorter than 30 chars.
3. Observability (architecture.md §14): keep the `SHOW_RETRIEVAL` panel from Phase 8, plus a per-session counter of answered vs refused turns rendered in the sidebar.
4. `make test` runs `pytest -q`; `make lint` runs `ruff check .`; both must be green.

### Cursor prompt

> Implement Phase 9 of `docs/implementation.md`. Write `samples/qa_samples.md` with 8-10 rows (id, question, verbatim answer, citation URL, scheme, section, fetched_at, notes) generated by actually running the chat service, including 2 refusal rows for advice and returns. Add `tests/conftest.py` with a session fixture that verifies the Chroma collection exists and skips with a clear reason otherwise. Add `tests/test_e2e.py` asserting the four PRD success metrics: 100% of answerable replies carry exactly one allowlist URL, all advice prompts are refused, every factual reply is ≤3 sentences, and every factual reply contains "Last updated from sources:". Add `tests/test_corpus_integrity.py` asserting every stored source_url is in `data/sources.yaml`, every fetched_at is a valid ISO date, and no chunk is under 30 characters. Add an answered-vs-refused turn counter to the Streamlit sidebar. Make `make test` and `make lint` both pass.

### Verification

```bash
make lint && make test
```

### Acceptance criteria

- [ ] All PRD success metrics are covered by an assertion in `tests/test_e2e.py`
- [ ] `samples/qa_samples.md` answers were generated by the pipeline, not hand-written
- [ ] Full suite green; corpus integrity test green
- [ ] Sidebar shows answered vs refused counts for the session

---

## Phase 10 — Demo pack & documentation

**Goal:** Everything PRD §11 asks to submit, plus the write-up that makes the RAG stages explainable in class.

### Tasks

1. `README.md`:
   - One-paragraph what/why
   - **Setup:** venv → `pip install -r requirements.txt` → `make ingest` → `make app`
   - **Scope:** AMC + the 5 schemes with a table
   - **How it works:** the 6-stage narrative (Load → Chunk → Embed → Store → Retrieve → Generate) in 6–8 lines, each stage naming its module
   - **Guardrails:** what is refused and why, with the refusal copy
   - **Known limits:** copied from architecture.md §17, plus the tuned `SIMILARITY_FLOOR` and the "not investment advice, read the SID/KIM/factsheet" line
   - **Disclaimer:** PRD Appendix A copy, verbatim
   - **Project layout:** the tree from architecture.md §10
2. `docs/implementation-notes.md` (created in Phases 2 and 4, finish here): chunking decision + parameters, similarity-floor tuning table, known failure cases, and any deviation from architecture.md with the reason.
3. Demo script `docs/demo-script.md`: a 3-minute run of show — the 3 example questions, 1 refusal, 1 out-of-scope, the "show retrieved chunks" toggle, and one sentence per RAG stage. Include the fallback line for a failed live fetch (run `make ingest` from cache).
4. Final consistency pass: architecture.md §15 open decisions now have answers — record each resolved decision and its rationale in `docs/implementation-notes.md`.
5. Optional: `docs/problemstat.txt` if the class requires it (architecture.md §10 references it).

### Cursor prompt

> Implement Phase 10 of `docs/implementation.md`. Write `README.md` with setup (venv, pip install, make ingest, make app), the 5-scheme scope table, a 6–8 line explanation of the Load → Chunk → Embed → Store → Retrieve → Generate stages each naming its module, the guardrails and their refusal copy, the known limits including the tuned similarity floor, the PRD Appendix A disclaimer verbatim, and the project layout tree. Write `docs/demo-script.md` as a 3-minute run of show: three example questions, one advice refusal, one out-of-scope question, the show-retrieved-chunks toggle, and one sentence per RAG stage, with a fallback line if ingestion must run from cache. Append to `docs/implementation-notes.md` the resolved open decisions from architecture.md §15 with their rationale.

### Verification

```bash
make lint && make test
ls -R data samples docs
```

Then do a **cold-start rehearsal**: clone the repo to a temp dir, follow the README setup verbatim, and confirm the app starts and answers a question. Fix the README if any step is missing.

### Acceptance criteria

- [ ] A clean clone + README steps alone gets a working app
- [ ] Every architecture.md §15 open decision is resolved and recorded
- [ ] All five PRD §11 deliverables exist and are consistent with the running code
- [ ] The disclaimer text is identical in the README and the UI

---

## Appendix A — Phase dependency map

```
Phase 0 ──▶ Phase 1 ──▶ Phase 2 ──▶ Phase 3 ──▶ Phase 4 ──┐
 (scaffold)  (corpus)    (chunks)   (store)   (retrieve)  │
                                                             ├──▶ Phase 6 ──▶ Phase 7 ──▶ Phase 8 ──▶ Phase 9 ──▶ Phase 10
                          (guardrails) Phase 5 ─────────────┘      (generate)  (service)   (UI)      (tests)      (docs)
```

Phases 4 and 5 are independent and can be built in parallel. Everything after 6 is strictly sequential.

## Appendix B — Cursor rules file

Drop this as `.cursor/rules/rag-chatbot.mdc` in the repo so every Cursor session inherits the constraints:

```markdown
---
description: Constraints for the HDFC MF facts-only RAG chatbot
globs: ["**/*.py"]
alwaysApply: true
---

- Python 3.11, src/ layout, full type hints, no untyped defs.
- No business logic in src/app.py — it only calls src/chat_service.handle_message.
- Every factual answer must carry exactly one source_url from data/sources.yaml.
- Factual answers are capped at 3 sentences.
- Never log, store, or echo user PII (PAN, Aadhaar, account numbers, OTP, email, phone).
- Advice, returns, and grievance questions must be refused by src/guards/policy.py before retrieval.
- All generation is grounded in retrieved chunk text; never invent numbers, dates, or scheme names.
- No network calls at query time; no new dependencies without asking.
- Config constants live in src/config.py; no magic numbers in logic modules.
- Use pathlib, not string path concatenation. Use logging, not print, in library code.
- Docstrings only, no inline comments. Module docstrings cite the architecture.md section implemented.
```

## Appendix C — Definition of done (all phases)

- [ ] `make ingest` builds the collection from scratch on a clean machine
- [ ] `make app` starts and answers correctly offline
- [ ] `make lint` and `make test` are green
- [ ] Every factual answer: 1 citation, ≤3 sentences, freshness line
- [ ] Every advice/returns/grievance/PII prompt: refused with the right message
- [ ] Nothing user-typed is written to disk
- [ ] README, samples, and disclaimer match the running behaviour
- [ ] A 3-minute class demo can be delivered straight from `docs/demo-script.md`
