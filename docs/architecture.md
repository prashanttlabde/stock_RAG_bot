# Architecture: Mutual Fund Facts-Only RAG Chatbot

**Product:** FAQ assistant for HDFC mutual fund scheme facts  
**Based on:** [PRD.md](./PRD.md)  
**Audience:** Class / milestone demo  
**Status:** Draft  
**Last updated:** 2026-09-27  

---

## 1. Purpose

Describe the system design for a small RAG chatbot that:

1. Ingests public scheme pages into a vector store  
2. Retrieves relevant chunks for user questions  
3. Generates **facts-only** answers with **one citation link**  
4. Refuses advice, PII, and performance-comparison requests  

The architecture must make both **data ingestion** and **data retrieval** visible and demoable.

---

## 2. High-level system view

```
┌─────────────────────────────────────────────────────────────────┐
│                         Presentation                             │
│  Tiny UI: welcome · 3 example Qs · chat · disclaimer             │
└────────────────────────────┬────────────────────────────────────┘
                             │ user query
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Query orchestration                         │
│  PII check → Intent gate (facts / refuse) → RAG or refusal       │
└───────────────┬─────────────────────────────┬───────────────────┘
                │ factual                     │ advice / returns /
                ▼                             │ PII
┌───────────────────────────┐                 ▼
│     Retrieval + Generate  │         Fixed refusal + educational link
│  embed → Chroma top-k →   │
│  grounded answer + cite   │
└───────────────┬───────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Knowledge layer (offline)                    │
│  Corpus files ← Load ← Chunk ← Embed ← ChromaDB                  │
└─────────────────────────────────────────────────────────────────┘
```

Two pipelines, run separately:

| Pipeline | When | Stages |
|----------|------|--------|
| **Ingestion** | Offline / setup | Load → Chunk → Embed → Store |
| **Query** | Per user message | Guard → Retrieve → Generate → Format |

---

## 3. Component diagram

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  UI (Streamlit│────▶│  Chat service    │────▶│  Guardrails     │
│  / Gradio /   │◀────│  (app entry)     │◀────│  PII · advice · │
│  notebook)    │     └────────┬─────────┘     │  returns policy │
└──────────────┘              │                └─────────────────┘
                              │ factual path
                              ▼
                     ┌──────────────────┐
                     │  Retriever       │
                     │  query embed +   │
                     │  Chroma similarity│
                     └────────┬─────────┘
                              │ top-k chunks + metadata
                              ▼
                     ┌──────────────────┐
                     │  Generator       │
                     │  grounded LLM or │
                     │  template answer │
                     └────────┬─────────┘
                              │ answer ≤3 sentences
                              ▼
                     ┌──────────────────┐
                     │  Response formatter│
                     │  + single citation │
                     │  + last-updated    │
                     └──────────────────┘

Offline:
┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌──────────┐
│ Loader   │─▶│ Chunker  │─▶│ Embedder     │─▶│ ChromaDB │
│ 5 URLs / │  │ structure│  │ MiniLM-L6-v2 │  │ persist  │
│ local MD │  │ -aware   │  │              │  │          │
└──────────┘  └──────────┘  └──────────────┘  └──────────┘
```

---

## 4. Technology choices (from PRD)

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | Light, local, good for FAQ-scale demo |
| Vector DB | ChromaDB (persistent local) | Simple setup; metadata filters by scheme/URL |
| Chunking | Structure-aware (see §6) | Scheme pages are sectioned (fees, SIP, risk, etc.) |
| Generation | Grounded LLM **or** extractive/template (open decision) | Must stay faithful to retrieved text |
| UI | Single-page app (e.g. Streamlit/Gradio) or notebook | Demo-friendly, minimal surface |
| Corpus | Cached text from 5 public Groww URLs | Stable demo; “Last updated” stamp |

---

## 5. Data model

### 5.1 Source documents

| Field | Description |
|-------|-------------|
| `scheme_id` | Stable id (e.g. `hdfc-large-cap-direct-growth`) |
| `scheme_name` | Display name |
| `category` | large_cap / flexi_cap / elss / small_cap / hybrid |
| `source_url` | Canonical public URL (citation target) |
| `fetched_at` | ISO date used in “Last updated from sources” |
| `raw_text` / file path | Cleaned page text for indexing |

### 5.2 Chunk metadata (stored in Chroma)

| Field | Description |
|-------|-------------|
| `chunk_id` | Unique id |
| `scheme_id` | Parent scheme |
| `source_url` | URL returned as citation |
| `section` | Optional heading (e.g. “Exit load”, “SIP”) |
| `fetched_at` | Freshness for UI note |
| `text` | Chunk body |

### 5.3 Chat turn (in-memory only)

No persistence of user messages. Do not log PII. Session is ephemeral for the demo.

---

## 6. Ingestion pipeline (detail)

```
Public URLs (5)
      │
      ▼
[1] Load / snapshot
      • Fetch or manually save cleaned text into /data/corpus/
      • Write sources.md (or CSV) with the 5 URLs
      • Stamp fetched_at
      │
      ▼
[2] Clean
      • Strip nav/chrome/boilerplate
      • Keep scheme facts: expense ratio, exit load, min SIP,
        lock-in, riskometer, benchmark, statement/tax guidance
      │
      ▼
[3] Chunk (structure-aware)
      • Prefer split on headings / labeled sections
      • Fallback: ~400–600 tokens, ~10–20% overlap
      • One scheme section ≈ one or few chunks
      • Attach source_url + scheme_id to every chunk
      │
      ▼
[4] Embed
      • Model: sentence-transformers/all-MiniLM-L6-v2
      • Batch embed chunk texts
      │
      ▼
[5] Store
      • Chroma collection e.g. mf_faq_hdfc
      • Persist to disk (e.g. /data/chroma/)
```

**Chunking decision rule (for demo write-up):**  
Inspect page structure first. If clear section headings exist for fees/SIP/risk/etc., split by section. Otherwise use fixed-size windows with overlap and keep `source_url` on each chunk so citations remain valid.

**Re-ingestion:** Re-run the pipeline when corpus files change; bump `fetched_at`.

---

## 7. Query pipeline (detail)

```
User message
      │
      ▼
[1] Guardrails (pre-retrieval)
      ├─ PII patterns (PAN, Aadhaar, account, OTP, email, phone)
      │    → refuse; do not store; remind not to share personal data
      ├─ Advice / portfolio intent (“should I buy/sell?”, “best fund?”)
      │    → polite refuse + educational link (e.g. AMFI investor education)
      └─ Returns / performance compare
           → do not compute; point to factsheet / scheme source page
      │
      ▼ (factual path)
[2] Embed query (same MiniLM model)
      │
      ▼
[3] Retrieve top-k from Chroma (k ≈ 3–5)
      • Optional metadata filter if scheme is named in the query
      │
      ▼
[4] Groundedness check
      • If scores too low / empty → “I don’t have that in the indexed pages”
        + link to closest scheme page if known
      │
      ▼
[5] Generate
      • System rules: facts only; ≤3 sentences; use only retrieved text;
        no advice; no invented numbers
      • Pick **one** primary `source_url` from the top chunk(s) for citation
      │
      ▼
[6] Format response
      • Answer body (≤3 sentences)
      • Citation: single clear link
      • “Last updated from sources: <fetched_at>”
```

---

## 8. Guardrails & policy mapping

| PRD rule | Architecture hook |
|----------|-------------------|
| FR-2 one citation | Formatter always attaches exactly one `source_url` |
| FR-3 ≤3 sentences | Prompt constraint + optional post-truncate |
| FR-4 last updated | From chunk/document `fetched_at` |
| FR-5 refuse advice | Intent classifier or keyword/heuristic gate **before** RAG |
| FR-6 no PII | Regex/heuristic strip + refuse; no DB writes of chat |
| FR-7 no performance claims | Dedicated branch; link out only |
| Public sources only | Corpus allowlist = 5 URLs; citations never leave allowlist |

---

## 9. UI architecture

Single view:

| Element | Behavior |
|---------|----------|
| Welcome line | Product purpose in one sentence |
| 3 example questions | Click fills/sends sample factual queries |
| Disclaimer | Always visible: facts-only, no advice, no PII |
| Chat area | User message → assistant reply with citation + last-updated |
| Empty/error states | Clear message when retrieval fails |

No login, no history store, no file upload of personal docs.

---

## 10. Suggested repo layout

```
RAG chatbot/
├── docs/
│   ├── PRD.md
│   ├── architecture.md
│   └── problemstat.txt
├── data/
│   ├── corpus/          # cleaned scheme texts
│   ├── sources.md       # 5 URLs + fetched_at
│   └── chroma/          # persistent vector store
├── src/
│   ├── ingest/
│   │   ├── load.py
│   │   ├── chunk.py
│   │   ├── embed.py
│   │   └── store.py
│   ├── retrieve/
│   │   └── retriever.py
│   ├── generate/
│   │   └── answer.py
│   ├── guards/
│   │   └── policy.py
│   └── app.py           # UI entry
├── samples/
│   └── qa_samples.md    # 5–10 Q&A + links
├── README.md
└── requirements.txt
```

---

## 11. Runtime sequence (happy path)

```
UI                Chat service         Guards        Retriever       Generator      Chroma
 │                     │                 │               │               │             │
 │── question ────────▶│                 │               │               │             │
 │                     │── check ───────▶│               │               │             │
 │                     │◀─ ok (factual) ─│               │               │             │
 │                     │── embed+search ────────────────▶│               │             │
 │                     │                 │               │── query ───────────────────▶│
 │                     │                 │               │◀─ chunks ───────────────────│
 │                     │── context + rules ─────────────────────────────▶│             │
 │                     │◀─ answer + source_url ──────────────────────────│             │
 │◀─ formatted reply ──│                 │               │               │             │
```

---

## 12. Configuration (demo defaults)

| Parameter | Suggested default | Notes |
|-----------|-------------------|-------|
| `top_k` | 3–5 | Small corpus; keep context tight |
| Chunk size | Section-based, or 400–600 tokens | Finalize after inspecting corpus |
| Overlap | 10–20% | Only for fixed-size fallback |
| Similarity floor | Tunable | Below floor → no-answer path |
| Embedding dim | 384 | MiniLM-L6-v2 |
| Persistence | Local Chroma path | Commit or rebuild via ingest script |

---

## 13. Non-functional (demo scale)

| Concern | Approach |
|---------|----------|
| Latency | Local embeddings + small Chroma; acceptable for classroom |
| Cost | Prefer local embed; LLM optional/local/API with tight prompt |
| Reliability | Cached corpus so demo works offline after ingest |
| Privacy | No chat persistence; PII gate |
| Explainability | Show citation + optionally retrieved section title in demo mode |

---

## 14. Observability for the demo

Optional but useful when presenting:

- Log which `scheme_id` / `section` was retrieved (not raw user PII)  
- Toggle “show retrieved chunks” for the instructor  
- Count refusal vs answered turns in the live session only  

---

## 15. Open decisions (architecture impact)

| Decision | Options | Impact |
|----------|---------|--------|
| Generation | LLM vs template/extractive | Prompting vs deterministic answers |
| Hosting | Local app vs notebook vs video | Deployment complexity |
| Scheme detection | NER/keywords vs pure vector search | Metadata filter accuracy |
| Exact chunk params | After corpus inspection | Ingest script constants |

---

## 16. Alignment with deliverables

| Deliverable | Architecture artifact |
|-------------|----------------------|
| Working prototype | `src/app.py` + ingest + retrieve |
| Source list | `data/sources.md` |
| README | Setup: ingest then run UI; known limits |
| Sample Q&A | `samples/qa_samples.md` |
| Disclaimer | Hard-coded in UI component |

---

## 17. Known limits (to document in README)

- Corpus limited to 5 HDFC Direct Growth scheme pages  
- Facts only as of `fetched_at`; not live market data  
- Not advice; not a substitute for SID/KIM/factsheet reading  
- May refuse or under-answer if the page text lacks the fact  
- No multi-turn memory beyond the current session UI buffer  

---

## Appendix — End-to-end RAG stages (demo narrative)

Use this order when explaining the architecture in class:

1. **Loading** — public pages → cleaned corpus files  
2. **Chunking** — structure-aware sections + metadata  
3. **Embedding** — `all-MiniLM-L6-v2`  
4. **Store vector data** — ChromaDB  
5. **Retrieval** — similarity search on user query  
6. **Generation** — grounded facts-only answer + one citation  

That sequence matches the PRD requirement to follow all RAG stages for **data ingestion** and **data retrieval**.
