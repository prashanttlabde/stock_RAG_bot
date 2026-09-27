# Implementation notes

Running log of what the corpus and pipeline actually turned out to be, and every
place the build deviates from [architecture.md](./architecture.md) or the
[implementation plan](./implementation.md), with the reason.

**Last updated:** 2026-09-27

---

## Phase 1 — Corpus acquisition

### What the source pages actually are

All 5 Groww scheme pages are Next.js server-rendered React pages, ~450–800 KB of
HTML each. The scheme facts a user asks about are present in the HTML, but they
are spread across an accordion UI, and most of the page is not facts.

| Content block on the page | Kept? | Why |
| --- | --- | --- |
| Hero metrics (NAV, Min. for SIP, AUM, Expense ratio, Rating, risk band, ELSS lock-in) | yes | the primary fact block |
| `Minimum investments` | yes | minimum SIP and lumpsum |
| `Understand terms` (Expense ratio / Tax / Exit load / Stamp duty) | yes | plain-language definitions |
| `Exit load, stamp duty and tax` | yes | current exit load, stamp duty, tax implication |
| `About <scheme>` | yes | risk rating, benchmark, objective, launch date |
| `Fund house` | yes | AMC contact, custodian, registrar |
| `Holdings ( 50 )` | **no** | 200–270 lines of stock rows; swamps every real fact |
| `Return calculator`, `Returns and rankings` | **no** | performance figures; conflicts with the facts-only stance (FR-7) |
| `Compare similar funds` | **no** | cross-scheme comparison, same reason |
| `Fund management` bios | **no** | manager tenure, not a scheme fact |
| `Exit Load` (effective-date history table) | **no** | a date→value table; the current exit load is already stated in `Exit load, stamp duty and tax`, and parsing the date table invites pairing an exit-load figure with an unrelated date |

Net effect: a whole-page scrape is 24,000–77,000 characters per scheme, of which
roughly 90% is holdings and return tables. The section-scoped corpus is
**~2,800–3,000 characters, 77–86 lines, six `##` sections, with every required
fact anchor present** (6/6 for all schemes, 7/7 for ELSS).

### Deviation 1 — trafilatura is a fallback, not the primary extractor

The plan specifies `trafilatura.extract(..., include_tables=True)` as the primary
extraction with BeautifulSoup as the fallback. Measured on the live pages,
trafilatura scores **0 fact anchors**: it returns only the historic-returns and
holdings tables and drops every scheme fact, because those facts live in
accordion components that its main-content heuristic discards.

Rather than swap the two, `extract_text` still races both strategies and picks
the winner by **fact-anchor coverage** (`_pick_best_candidate`). The section-aware
walk wins today; if Groww ever restructures so the walk finds nothing,
trafilatura takes over automatically. The log line
`using extraction 'sections' for category 'large_cap'` records the decision per
scheme, which is the evidence to show if asked why trafilatura is not primary.

### Deviation 2 — the corpus is section-scoped, not a full-page snapshot

architecture.md §6 describes loading "cleaned page text". Taken literally that
produced a corpus dominated by holdings rows, which would have made retrieval
worse, not better. The corpus keeps the fact-bearing sections and drops the
rest. This is a deliberate narrowing of scope in service of the product's
facts-only promise, and it is what makes the one-citation guarantee auditable: a
human can read all 78–86 lines of a corpus file and confirm every claim traces to
the cited page.

### Deviation 3 — the "statement" fact anchor does not exist on these pages

The plan lists a `statement/tax guidance` anchor. No Groww scheme page contains
any statement or "how to download your statement" content, so that anchor could
never pass. The anchor is `tax_guidance`
(`capital gains|tax|taxed`), which every scheme satisfies via the
`Understand terms` → `Tax` glossary entry and the `Tax implication` line. If
statement guidance is wanted in scope, the corpus needs an additional AMC source
URL, which would change the 5-URL allowlist in the PRD.

### Deviation 4 — the page's FAQ JSON-LD is deliberately excluded

Every page ships a `FAQPage` JSON-LD block with 8 structured Q&A pairs
(expense ratio, AUM, NAV, PE/PB, SIP vs lumpsum, how to invest, how to redeem,
and returns). It is tempting because it is already clean text. It is excluded
because 3 of the 8 answers are returns or AUM figures and 2 are Groww-specific
"how to invest on Groww" instructions, none of which a facts-only,
no-performance-claims, single-AMC assistant should be quoting. The visible page
text already covers every in-scope fact.

### Deviation 5 — label/value joining is allowlist-driven, not heuristic

The details block renders each fact as two sibling nodes, so the raw text
alternates `Expense ratio` / `1.03%`. A generic "short line followed by short
line" rule joined these badly and, worse, **invented relationships the page never
states** — it produced `Exit load of 1% if redeemed within 1 year: 16 Feb 2015`
by pairing an exit-load figure with an unrelated date from the effective-date
table. Since INV-5 forbids invented facts, joining is now restricted to a
`FACT_LABELS` allowlist (expense ratio, fund benchmark, min. for 1st/2nd/SIP,
AUM, NAV, rating, exit load, tax, stamp duty), the value must not itself be a
label or a heading, and a line that already carries a joined value may not pair
again. `clean_text` is therefore idempotent, which is asserted by
`test_clean_text_is_idempotent` against the real corpus.

Two labels are exempt from the "no colon" rule because the page ships them with
their own colon, and only while they still hold exactly one: `NAV: 25 Sep '26`
may pair with its value, but `NAV: 25 Sep '26: ₹1,189.08` already has one and may
not. The resulting `NAV: 25 Sep '26: ₹1,447.38` double-colon form is intentional
— it preserves the as-of date, which is the kind of freshness detail this product
is supposed to surface.

### Deviation 6 — the corpus body carries `##` section headings

Each kept section is written as `## <heading>` followed by its fact lines. This
was not in the plan, but it is what makes architecture.md §6's "prefer split on
headings" chunking rule actually fire in Phase 2: the page's real section
structure survives into the corpus instead of being flattened into prose. Six
headings per scheme, each one citation-valid.

### Deviation 7 — `SourceRef` added to `src/types.py`

`load_sources` needs a record for an allowlist entry that has no `fetched_at` and
no text yet, which `SourceDoc` claims to have. Rather than pass `SourceDoc` with
empty strings, `SourceRef(scheme_id, scheme_name, category, source_url)` was
added. `write_corpus` returns fully populated `SourceDoc` records as planned.

### Return figures are absent from the corpus by design

The pages publish 1D/1M/6M/1Y/3Y/5Y returns, and the extraction deliberately
drops them along with the return tables. Consequence for the demo: the guardrail
in Phase 5 is what enforces FR-7 on returns questions, and it is the only
enforcement point. That is a stronger demo than relying on the model to decline,
and it means a "what are the 1-year returns" question must be shown being
refused rather than answered.

### Manual fallback status

Not needed. All 5 pages fetched cleanly on 2026-09-27 with full anchor coverage.
The fallback path is documented in the `src/ingest/load.py` module docstring and
in the CLI's error message, and a hand-written `data/corpus/<scheme_id>.md` is a
first-class corpus document: `load_corpus` reads from disk only and never
fetches.

---

## Phase 2 — Structure-aware chunking

### Observed section structure

All 5 schemes expose the **same 6 sections**, and the section path was taken for
every one of them (`chunking_strategy: "section"` in `data/chunk_stats.json`).
There was no scheme that needed the size-based fallback.

| # | Section | Chunks | What it answers |
| --- | --- | --- | --- |
| 1 | `<scheme name>` (hero block) | 1 | NAV, min SIP, AUM, expense ratio, rating, risk band, ELSS lock-in |
| 2 | `Minimum investments` | 1 | min 1st investment, min 2nd investment, min SIP |
| 3 | `Understand terms` | 1 | plain-language definitions of expense ratio, tax, exit load, stamp duty |
| 4 | `Exit load, stamp duty and tax` | 1 | current exit load, stamp duty rate, tax implication on redemption |
| 5 | `About <scheme name>` | 1 | risk rating sentence, benchmark, objective, launch date |
| 6 | `Fund house` | 1 | AMC, custodian, registrar, contact details |

**30 chunks total, 6 per scheme.** Section 1 is the hero block, so the first
section's name is the scheme's own page title. Sections 1 and 5 are near-duplicate
in *name* but not in content — section 5 carries the benchmark and the risk
sentence, which is the answer to two of the most common FAQ questions, so it is
worth its own chunk.

Measured chunk sizes: **21 / 101 / 208 tokens** (min / avg / max), against a
500-token target. Nothing needed splitting, so `split_fallback` never ran on the
real corpus. The largest chunk is `Fund house`, which is contact details.

### Final constants

| Constant | Value | Where |
| --- | --- | --- |
| `CHUNK_TARGET_TOKENS` | 500 | `src/config.py` — plan default, unchanged |
| `CHUNK_OVERLAP_RATIO` | 0.15 | `src/config.py` — plan default, unchanged |
| `MIN_CHUNK_CHARS` | 30 | `src/config.py` — plan default, unchanged |
| `MIN_SECTIONS_FOR_STRUCTURED_CHUNKING` | 3 | `src/config.py` — plan default, unchanged |
| `MAX_HEADING_CHARS` | 80 | `src/config.py` — plan default, unchanged |
| token proxy | `words / 0.75` | `estimate_tokens`, used for packing and reporting only |

The defaults were kept deliberately. At 21–208 tokens per chunk the corpus is far
below the 500-token target, so tuning it would be fitting noise: the binding
constraint is the section structure, not the size limit. `CHUNK_TARGET_TOKENS`
only starts to matter if a hand-written corpus file (the manual fallback path)
brings in a body with no headings.

### Deviation 8 — `## ` headings are authoritative; the keyword heuristic is a fallback

The plan specifies keyword detection as *the* heading rule. Applied literally on
this corpus it over-fires badly, and the first implementation produced **55 chunks
instead of 30**, with these defects:

- The glossary terms inside `## Understand terms` were promoted to headings of
  their own, so the expense-ratio definition lost the fact that it is part of the
  scheme's published terms glossary.
- `Fund benchmark` was split out of `## About …` into a two-line chunk of 5
  tokens, because on two pages the benchmark is a bare line and on the other
  three it is a `Fund benchmark: NIFTY …` fact line. Same page, two shapes.
- Every scheme reported 10–12 sections instead of 6.

So `segment_sections` now picks a mode before splitting: if the body has any
`## ` heading, those *are* the sections and no bare line inside them is promoted;
only a body with no `## ` at all falls back to the `SECTION_PATTERNS` keyword
heuristic. This is the "prefer splitting on headings" half of architecture.md §6's
decision rule applied properly — Phase 1 already preserved the page's real
section structure, so the heuristic should never override it.

Two supporting rules keep the keyword path honest for hand-written corpus files:
a `Label: value` line is never a heading (so a flat `Expense ratio: 1.03%` fact
block stays flat), and a document that *opens* with a keyword heading gets that
heading as a section rather than a spurious preamble.

### Deviation 9 — a newline is a hard sentence boundary in `split_fallback`

The plan splits on `(?<=[.!?])\s+`. Corpus bodies are line-oriented and many fact
lines have no terminator (`Min. for SIP: ₹100`), so a punctuation-only split
glues such a line to the next one and cuts mid-fact. `_sentence_units` therefore
treats a newline as a boundary in addition to sentence-ending punctuation, and
rejoins with newlines. This does not change the real corpus, where the fallback
never runs, but it makes the manual-fallback path produce sane chunks.

### A caveat on the 30-character floor

`MIN_CHUNK_CHARS` drops genuinely short sections. `## Exit load` containing only
`Exit load: 1%` is 20 non-space characters and is discarded, per the plan's
"boilerplate residue" rule. On the real corpus nothing is dropped — 30 sections
in, 30 chunks out — because every section is a full fact block. It is a real
constraint on the hand-written path, and `test_a_chunk_under_the_character_floor_is_dropped`
pins the behaviour so it cannot change silently.

### Observed page variation: the balanced advantage exit load

Four schemes render the exit load as a `Label: value` pair. The fifth does not:

```
Exit load
Exit Load for units in excess of 15% of the investment,1% will be charged for redemption within 1 year.
```

The value is 99 characters, past the 60-character cap that stops a label from
swallowing a paragraph, so it is left unjoined and the label stays on its own
line. This is deliberate, not an oversight. Joining it would produce
`Exit load: Exit Load for units in excess of 15% …`, which is redundant and
harder for a generator to read than the page's own layout, while raising the cap
re-opens exactly the failure that motivated the allowlist in the first place
(Phase 1 deviation 5: `Exit load of 1% if redeemed within 1 year: 16 Feb 2015`).
The fact is complete, the retrieval keyword is present on the line above it, and
Pinned by `test_a_long_value_is_left_unjoined_rather_than_swallowed`.

### Chunk ids

`{scheme_id}::{section_slug}::{i:03d}`, with `i` a 1-based per-document counter,
so ids are unique within a scheme and stable across runs. Verified by re-running
the CLI twice: identical stdout and an identical `data/chunk_stats.json`. This
matters for Phase 3, where the same id upserts into Chroma instead of creating a
near-duplicate vector.

Each chunk text opens with its own section name, which both gives the embedding
its topic and lets Phase 6's template generator name the section it answered
from. Phase 1's extractor repeats the page's accordion title as the section's
first body line, so `_body_text` drops that leading duplicate.


---

## Phase 3 — Embedding & vector store

The plan's spec was followed as written; no deviations. What follows is the
measured behaviour, because two of these numbers matter for the Phase 4 tuning and
the Phase 10 demo.

### Observed: the only network need in the whole project

`make ingest` downloads `sentence-transformers/all-MiniLM-L6-v2` on its first
run (~90 MB into the Hugging Face cache, about a minute). After that the model is
on disk and **nothing in this project touches the network at query time**.

Verified, not assumed. With the hub forced offline:

```
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -c "... embed_query + col.query ..."
  -> count: 30, query returned the ELSS hero block at similarity 0.532
```

This is INV-6 satisfied, and it is what lets Phase 8's Streamlit app be
demonstrated on a machine that has already run `make ingest` once.

### Observed stage timings (first run, model not yet cached)

| Stage | Time | Note |
| --- | --- | --- |
| `load` | 0.004s | 5 files off disk |
| `chunk` | 0.000s | 30 chunks |
| `store` | 5.6s | **dominated by loading MiniLM**, not by embedding 30 chunks |
| `stats` | 0.005s | reads 3 rows back |

Embedding the 30 chunks themselves is ~0.25s. Note that the plan's pipeline has
four stages (`load → chunk → store → stats`) while architecture.md §10's narrative
has six (`Load → Chunk → Embed → Store → Retrieve → Generate`), so **embedding time
is reported under `store`**. The per-batch `embedded 30/30 texts` line from
`store.py` is the finer-grained evidence to point at in the demo.

### The embedding dimension is asserted, not assumed

`_get_model()` checks the model's output width against `config.EMBEDDING_DIM` and
raises `IngestError` on a mismatch, and `_as_matrix()` re-validates the encoder's
output shape on every call. A silent dimension mismatch between the model and a
pre-existing collection produces vectors Chroma accepts but cannot search
meaningfully, which is close to undebuggable from the UI. Failing at ingest with a
named constant is much cheaper to diagnose.

`sentence-transformers` 6.x renamed `get_sentence_embedding_dimension` to
`get_embedding_dimension`. `requirements.txt` allows `>=3.0.0`, so
`_model_dimension()` tries the new name and falls back to the old one.

### Log noise needed suppressing

A single `make ingest` emitted ~20 `INFO` lines from `httpx` and `huggingface_hub`
(hub reachability checks, one per config file) on top of the ~10 lines that
matter. architecture.md §14 asks for the pipeline to be observable in the log,
which is impossible while a single dependency out-shouts it, so
`logging_config.NOISY_LOGGERS` pins the chatty third-party loggers to `WARNING`.
They are downgraded, not silenced, so they are still there for anyone debugging
that dependency.

### Unfiltered retrieval is not scheme-scoped

A raw query for *"what is the minimum sip for elss"* returns:

| # | similarity | scheme | section |
| --- | --- | --- | --- |
| 1 | 0.532 | `hdfc-elss-tax-saver-direct-growth` | hero block (carries `ELSS 3Y Lock in`) |
| 2 | 0.483 | `hdfc-equity-flexi-cap-direct-growth` | `Minimum investments` |

Both clear the current `SIMILARITY_FLOOR` of 0.35. That is expected, and it is
exactly why Phase 4 filters on `scheme_id` when `detect_scheme_id()` recognises
the mention instead of leaning on the floor alone. It is also the first data
point for the floor sweep: a cross-scheme sibling section sitting at 0.48 sets the
bar the floor has to clear.

### Idempotency, verified three ways

`make ingest` → 30. `make ingest` again → 30. Non-rebuild upsert → 30. The count
never moves, because `chunk_id` is deterministic and the store upserts.
`run_ingest` also refuses to report success unless `collection_count == chunks`,
so a store that silently lost rows fails the ingest instead of shipping a broken
demo.

### Test isolation

`store._client()` memoises the Chroma client **keyed by path**, so pointing
`config.CHROMA_PATH` at a `tmp_path` gives a test an isolated store instead of the
already-open one. `tests/test_store.py` and `tests/test_pipeline.py` both build
into temporary directories and never touch the real `data/chroma/`. Both modules
skip with a clear reason when the embedding model cannot be loaded, so `make test`
still passes on a machine with no model cache and no network.

### Finding: 30 chunks hold only 19 distinct texts

`pipeline --dump-embeddings` (added in Phase 3 to make the store inspectable
without opening SQLite) reported a cosine similarity of **1.000** between several
chunk pairs. That is not an encoder fault: the vectors were read back from Chroma
and are L2-normalised, so 1.000 means the input texts are byte-identical. Hashing
the chunk bodies confirms it.

| Copies | Section | Why |
| --- | --- | --- |
| x5 | `Understand terms` | Groww's glossary is the same on every scheme page |
| x4 | `Minimum investments` | ₹100 SIP / ₹100 lump-sum on 4 of the 5 schemes |
| x3 | `Exit load, stamp duty and tax` | "1% within 1 year" on flexi, large and small cap |
| x2 + x2 | `Fund house` | the HDFC Mutual Fund blurb |

**16 of 30 chunks are byte-identical copies of another chunk.** The extraction is
faithful — the source pages really do render identical text per scheme — so this
is not a Phase 1 or Phase 2 defect, and "30 chunks" is still the correct count of
*indexed sections*. It is a property of the corpus.

Three consequences, all deferred to Phase 4 rather than fixed here:

1. **`TOP_K=4` is spent on copies.** A question about exit load or understand-terms
   will fill all four slots with the same sentence attributed to three or four
   different schemes. The user would see one fact repeated with four citations.
2. **Citations stay accurate but stop being informative.** Citing the ELSS page for
   a large-cap question is factually correct (the ELSS page does say it) while
   implying scheme-specific information that is actually generic. This is a live
   risk for INV-2 and needs either text-hash dedup after retrieval or an explicit
   "this applies to all HDFC schemes" framing in the template generator.
3. **It distorts the `SIMILARITY_FLOOR` sweep.** A floor tuned on a corpus where
   the top hits are near-duplicates measures boilerplate self-similarity, not
   retrieval quality. The sweep should dedupe first, or the floor will be set too
   high and real cross-scheme questions will fail it.

Recording it now so the floor tuning in Phase 4 is not done against a misleading
signal.

---

## Phase 4 — Retriever

`src/retrieve/retriever.py`, `src/retrieve/__init__.py`, `scripts/tune_floor.py`,
`tests/test_retriever.py` (60 tests). `SIMILARITY_FLOOR` tuned 0.35 → **0.40**.

### The plan's central assumption turned out to be false

The plan expects a single similarity threshold to separate answerable questions from
out-of-corpus ones. Measured on the real store, it cannot:

| | range across the 15 labelled queries |
| --- | --- |
| answerable (8) | 0.433 – 0.728 |
| out-of-corpus (8) | 0.261 – **0.716** |

The single worst out-of-corpus question, *"What is the SEBI registration number of
HDFC Mutual Fund?"*, scores **0.716** — above four of the eight answerable ones. The
best answerable question, *"What is the lock in period for ELSS?"*, scores 0.433.
There is no threshold that separates those. Any floor low enough to keep the ELSS
question admits the SEBI one, and any floor high enough to reject SEBI throws away
half the answerable set.

The reason is that MiniLM cosine similarity on 30 chunks of same-domain finance text
has a compressed, overlapping range. A query shares surface vocabulary with the hero
chunks — "HDFC", "fund", "NAV" — whether or not the answer is in them, and
similarity cannot tell "asks about something in this chunk" from "uses the same words
as this chunk".

### Deviation 10: two orthogonal gates, because the floor cannot carry the load

Retrieval now applies two checks that are facts about a closed 5-URL corpus rather
than guesses about a score distribution. Both fire *in addition to* the floor, which
is now a backstop rather than the primary filter.

**(a) Out-of-corpus scheme detection.** `OUT_OF_CORPUS_SCHEMES` names HDFC scheme
families that are real funds but absent from the allowlist (mid cap, floating rate,
income, dividend yield, pharma, …). The corpus is a closed set, so naming one is a
*definitive* miss, not a weak match. It short-circuits before any vector search
(`considered_count == 0`). Phrases are multi-word where a bare word would collide with
corpus vocabulary: `nifty` is deliberately excluded because the Balanced Advantage
chunk literally contains "NIFTY 50 Hybrid Composite Debt 50:50 Index" as its
benchmark, so a bare `nifty` alias would break the benchmark question. It only
short-circuits when no *indexed* scheme is also named, so "Compare HDFC Mid Cap and
HDFC Large Cap" still answers for Large Cap.

**(b) Answer-term grounding.** A question's content words — after stopwords and
entity words are removed — must appear in the retrieved text
(`MIN_ANSWER_TERM_MATCH = 1`). "What is the exit load of HDFC Large Cap?" reduces to
`["exit", "load"]`; the corpus contains both, so it grounds. *"What is the SEBI
registration number…"* reduces to `["sebi", "registration", "number"]` and the corpus
contains none of them, so nothing can ground it. This is the check that catches the
0.716 case the floor cannot.

Entity words must be removed or the gate is useless: every chunk contains "HDFC",
"Fund" and the scheme name, so without removal any question matches on the entity
alone. `manager` counts as an entity, because the role is named in the corpus while
the manager's biography was deliberately dropped in Phase 1 — a question about
someone's education must not be grounded by the word "manager".

### The word-boundary bug this gate first shipped with

The first implementation used `term in text`. That is wrong in a way that only shows
up on real data: `"ratio" in "Registrar & Transfer Agent, operation"` is **True**,
because "ope**ratio**n" contains "ratio". The symptom was *"expense ratio of hdfc
equity fund"* ranking the HDFC **Fund house** chunk (0.687) above the hero chunk that
literally states `Expense ratio: 0.77%` (0.582).

Fixed by compiling one `\b(?:term1|term2|…)\b` pattern per question, cached with
`lru_cache`. After the fix the hero chunk ranks first and Fund house is correctly
rejected. Worth remembering that substring matching on short content words is not
safe, and that the demo query which exposed it is one the instructor is likely to try.

### The floor sweep

```
 floor  answered  false ans  false rate  guard hits  verdict
 0.20       8/8          1        12%           2  rejected
 0.25       8/8          1        12%           2  rejected
 0.30       8/8          0         0%           2  MEETS TARGET
 0.35       8/8          0         0%           2  MEETS TARGET
 0.40       8/8          0         0%           2  MEETS TARGET
 0.45       7/8          0         0%           1  MEETS TARGET
 0.50       7/8          0         0%           1  MEETS TARGET
 0.55       6/8          0         0%           1  too few answered
 0.60       6/8          0         0%           1  too few answered
```

**Chosen: 0.40**, the midpoint of the qualifying band 0.30–0.50, not its edge. The
tuner picks the midpoint on purpose: a floor at 0.30 sits one step above the last
failing value, and one step below it a new phrasing of an out-of-corpus question
scores through. The midpoint keeps the largest margin to both observed failure edges,
which is the only defence against a query that is not in the labelled set.

Tightest surviving margin: the ELSS lock-in question at 0.433 against the 0.40 floor,
a gap of 0.033. That single query is what caps the floor. If a re-tune ever loses it,
the honest fix is a better ELSS chunk, not a lower floor.

Note what the floor is now doing: of the 8 out-of-corpus queries, **7 are rejected by
the two gates without reference to the floor at all**. Only the score-based path
contributes the 1 false answer seen at 0.20 and 0.25.

### Deviation 11: the labelled set has three labels, not two

The plan asks for 8 answerable and 7 out-of-corpus queries (the set grew to 8
out-of-corpus in deviation 10a). Its two example
out-of-corpus questions — *"What is the NAV of HDFC Mid Cap?"* and *"How do I file a
tax grievance?"* — are of different kinds, and conflating them would have produced a
meaningless number. The grievance question is stopped by Phase 5's `GRIEVANCE_PATTERNS`
before the retriever ever runs, so counting it as a retriever success measures a layer
that is not this one's job.

The set is therefore labelled `answerable` (8), `out_of_corpus` (7), and
`guard_blocked` (4). The floor is tuned on the first two only. The third is **printed
but not scored**, and its hit count is shown in the `guard hits` column so the
blind spot stays visible rather than being quietly dropped. Scoring them would flatter
the floor; hiding them would flatter it too, by narrowing the test without saying so.

Two of the four do score hits if they ever reached the retriever, and both are
`returns` questions that ground on the word "year" — the exit-load chunk literally
contains "If you redeem within one year, returns are taxed at 20%". Phase 5's
`RETURNS_PATTERNS` is what must stop them. If a returns question ever reaches
retrieval in the demo, that is a Phase 5 bug.

### Deviation 12: over-fetch, so duplicate collapse still leaves a full budget

The plan queries `n_results = min(top_k, count)`. With `TOP_K=4` and `Understand
terms` present once per scheme, that returns four byte-identical chunks and the
budget is spent on one fact. Retrieval instead asks Chroma for
`min(count, top_k * RETRIEVE_OVERFETCH)` rows with `RETRIEVE_OVERFETCH = 5`, applies
the floor and the grounding gate, then collapses duplicates and takes the first
`top_k` *distinct* texts. The multiplier matches the worst-case duplicate run, so
`top_k` distinct rows are always reachable from `top_k * 5` candidates.

This also keeps Phase 6's `select_primary_source` unambiguous: with duplicates
collapsed, a boilerplate answer has one obvious citation rather than four equally
valid identical ones, so `AMBIGUOUS_SOURCE_DELTA` is far less likely to fire.

Observed: an unscoped *"what is the exit load"* considers 20 rows and returns 3 hits
— the flexi-cap, balanced-advantage and ELSS exit-load texts, which genuinely differ
— with the large-cap and small-cap copies correctly collapsed into the flexi-cap one.

### Deviation 13: `chunk_id` added to Chroma metadata

`Chunk.to_metadata()` now stores `chunk_id` alongside the other four fields, so a
retrieved row is self-describing and `retrieve` can rebuild a whole `Chunk` from
metadata alone. Without it every `RetrievalHit.chunk.chunk_id` was silently `""` — a
latent bug that Phase 6's `used_chunks` and the Phase 8 debug panel would have
inherited. Cost: one re-ingest, and the 3 Phase 0/2 tests that pinned the 4-key
metadata contract were updated.

### Ambiguous scheme mentions are left unscoped, deliberately

`detect_scheme_id` returns `None` both when no scheme is named and when **two or more**
are. So "Compare HDFC Large Cap and HDFC Small Cap" searches all five schemes rather
than guessing one. That is the truthful behaviour: the user asked about two funds, so
hiding one of them would be worse than showing both, and Phase 6 still cites exactly
one URL as INV-1 requires.

"Do I pay tax on HDFC Large Cap?" hits both the `tax` → ELSS alias and the
`large cap` alias, so it is ambiguous and unscoped. The loose `tax` alias the plan
specifies is saved from doing damage by this rule, and by the fact that "taxation"
does not match it — aliases are matched on word boundaries, not substrings.

### Known limitation carried forward

Ranking is still not perfect. For *"expense ratio of hdfc equity fund"* the hero
chunk (which states the number) now ranks first at 0.582, but the query's short length
and the shared entity words mean the ordering within a scheme is driven as much by
chunk length as by topical match. Phase 6 mitigates this by selecting sentences by
question-term overlap across the top hits rather than reading only the first one, so a
lower-ranked hit can still supply the correct sentence. A reranker is the real fix and
is out of scope.

### Deviation 10a: the out-of-corpus check needed two tiers, not one

Writing the acceptance checks by hand turned up a false answer the 15 labelled
queries never tested for: **"What is the exit load of Mirae Asset Large Cap?" returned
HDFC Large Cap's exit load at 0.544, with `detected_scheme_id` set and no indication
anything was wrong.**

The cause was the ambiguity rule working against me. "large cap" is an indexed alias,
so `detect_scheme_id` returned `hdfc-large-cap-direct-growth`; and because the
out-of-corpus check only ran when scheme detection came back `None` — a rule added so
that "Compare HDFC Mid Cap and HDFC Large Cap" would still answer for Large Cap — the
denylist was never consulted. The plan's requirement that a question about a fund
outside the allowlist return no hits was simply not met.

The fix splits the denylist by *how a miss is proved*, not by topic:

| tier | example | rule |
| --- | --- | --- |
| `OUT_OF_CORPUS_FUND_HOUSES` | `mirae asset`, `parag parikh`, `kotak`, `sbi` | **always** decisive, checked before scheme detection |
| `OUT_OF_CORPUS_SCHEMES` | `mid cap`, `floating rate`, `etf`, `pharma` | decisive only when no indexed scheme is also named |

The reasoning is that a category word and a brand name are different kinds of
evidence. "Mid cap" in "Compare HDFC Mid Cap and HDFC Large Cap" is part of a
comparison this corpus can partly answer, so it must not veto the question. "Mirae
Asset" is not a category — no reading of "Mirae Asset Large Cap" denotes HDFC's fund,
so it is a miss no matter what else the sentence contains. Checking the brand tier
first costs the comparison behaviour nothing, because a comparison between two *other*
funds' categories is still answered.

Every brand entry was verified against all 30 corpus chunks with a word-boundary match
before being added, because the short ones are ordinary English words and a denylist
entry that occurs in the corpus would reject questions about indexed funds:
`axis` ("axis of growth"), `union` ("Union Budget") and `canara` ("Canara Bank", a
common HDFC custodian) were all checked and are absent from this corpus. That check is
now a test — `test_no_denied_phrase_appears_in_the_corpus` — so the lists cannot be
extended past the point where they start colliding with real content, which is the
failure mode a hardcoded denylist silently develops.

The lesson generalises past this phase: the labelled set in `scripts/tune_floor.py`
passes at 0 false answers, and the two most likely demo questions are still not in it.
I wrote acceptance checks *outside* the labelled set to get these numbers, which is the
only reason the gap surfaced at all.

---

## Phase 6 — Grounded generation

`src/generate/prompt.py`, `answer.py`, `no_answer.py`, `tests/test_answer.py` (64 tests).
Suite: **349 passed**, `make lint` clean. All five acceptance criteria are met, and the
plan's own verification block produces the four expected answers.

### The corpus has no such thing as a "sentence"

The plan defines the 3-sentence cap by splitting on `(?<=[.!?])\s+`. That is wrong for
this corpus, and not in a small way. The five source pages are mostly label/value lines,
and the labels are abbreviated:

```
Min. for SIP: 100
```

A terminator split cuts that at the period in `Min.` and yields `Min.` and
`for SIP: 100`. The amount is separated from its label, so a question about SIP minimums
produced *"Minimum investments for SIP: 100 for SIP: 500"* — two unrelated facts welded
together, with the number the user asked for no longer attached to its name.

`split_units` therefore splits on a newline **or** a terminator that actually ends a
sentence, where a period after an abbreviation (`min`, `no`, `dr`, `rs`, `a/c`, single
letters) is a label ending. A newline stays a unit boundary, which is what keeps INV-2
honest on label/value blocks: a 10-line hero block is 10 units, so the cap binds.

### The one-hit rule is a correctness rule, not a simplification

`generate_template` takes units from a **single** hit. The plan says "from the top hit",
which reads like tidiness; it is not. The five schemes are five different funds, so
borrowing a line from a second hit mixes two schemes' numbers into one answer. It did
exactly that:

> Q: What is the minimum SIP amount?
> A: HDFC Balanced Advantage Fund: Min. for SIP: 100 **Min. for SIP: 500**

Two schemes, one citation, and nothing to say which figure belonged to which. The rule is
now: take the first hit that can answer at all, never a second. An answer always comes
from one page, and the answer names the page.

### "The expense ratio" is not an answer

Ranking units purely by term overlap picks the wrong line whenever a question's words are
split across a heading and its value. Asking for the expense ratio, `Expense ratio` and
`Expense ratio: 0.78%` both match `expense`+`ratio`; the second is the answer. Ties now
break toward the unit **carrying a value** (a digit), because a question about an amount,
a ratio or a period is answered by the line with the number on it. A selected unit also
suppresses any unit whose text it contains, which is what stops the heading being printed
next to its own value.

Two related rules fell out of testing the same questions:

* **A bare label is completed only in the glossary.** The `Understand terms` chunks are a
  term followed by its definition, so `Expense ratio` there is half an answer. Applied
  anywhere else it answers *"what is the risk level?"* with `Very High Risk NAV: 25 Sep
  '26: 159.82` — a category with an unrelated figure stapled to it. It is now scoped to
  that one section, which is a structural fact about the corpus rather than a guess.
* **A page title is dropped when something else was selected.** The answer is already
  prefixed with the scheme name, so repeating the page title gave *"HDFC Large Cap Fund –
  Direct Growth: HDFC Large Cap Fund Direct Growth NAV: ..."*. The title survives when it
  is the only thing that matched, so a question whose answer genuinely is the title still
  gets one.

### Entity stripping can delete the whole question

`_ENTITY_TERMS` is built from scheme names and aliases, so it also swallows generic words
that happen to appear in them — `nav`, `fund`, `scheme`, `tax`, `cap`, `large`. That is
the right trade for grounding (*"the exit load of HDFC Large Cap"* must not require the
text to say "HDFC"), but for *"Is it a large cap fund?"* it removes **every** content
word, leaving nothing to match on. The old fallback quoted the first three lines of the
best chunk, which answered with the page heading printed twice.

`content_terms` gained `strip_entities=False` for exactly this case, used only when the
strict set comes back empty. The default is unchanged, so Phase 4's grounding and its 178
tests are untouched. This is a narrow, additive seam — the alternative was loosening the
matcher, which is the one thing Phase 4 tuned on purpose.

### `generate_llm` no longer raises when nothing is wired up

The plan asks for a template fallback "on any exception". The first version raised
`GenerationError` when `client is None` — *outside* the `try` — so `GENERATION_MODE=llm`
with no SDK turned a missing integration into a broken chat turn instead of a working
template answer. No client is now a failure like any other. `_build_client()` is the one
named seam for wiring a real client later; it returns `None` today, so the default
template path never touches it.

### Verified

* The plan's verification block, verbatim: all four questions answered, one allowlisted
  citation each, `fetched_at` populated.
* 20 factual questions: 19 answered, **0 invariant violations** — every citation in the
  allowlist, no body over 3 units, no URL in a body, and every number present in the
  corpus files.
* 8 out-of-corpus/advice questions: all refused by `check_query` before reaching the
  generator (INV-4). Note this is the *policy* that refuses them; `answer_question` on
  its own will answer a returns question with whatever is in the context, which is why
  INV-4 is a layering guarantee and not a generator guarantee.
* No API key, no network: a test installs a client factory that raises on call and
  asserts the template path still answers.

### Open items, deliberately not fixed here

Three recall problems are Phase 4's, not Phase 6's. Generation refuses rather than
guesses in all three (INV-5), so they are visible as a no-answer rather than as a wrong
answer — but the user sees "I don't have that" for questions the corpus can answer.
1. **`_ENTITY_TERMS` over-strips `nav`.** *"What is the NAV?"* leaves no content word
   after stripping, so the unstripped fallback is used and the fund-name line outranks the
   NAV line on `hdfc`/`large`/`cap`. The answer is correct and cited, just wordy.
2. **`matched_terms` is deliberately strict** (`\b{term}\b`, so `ratio` cannot match
   `operation`), which means `lump` does not match `Lumpsum` and `risky` does not match
   `Very High Risk`. *"Minimum lump sum of HDFC Small Cap?"* therefore returns the **SIP**
   minimum. A stem or synonym table is the right fix, in the module that owns the matcher.
3. **The floor is sensitive to how the question is phrased.** Across 7 attribute questions
   asked both ways, naming the scheme lifts the top similarity by 0.15–0.48, and the
   unscoped form falls below the tuned `SIMILARITY_FLOOR = 0.40` **5 times out of 7**:

   | unscoped | scoped |
   |---|---|
   | What is the NAV? — 0.143 | …of HDFC Large Cap? — 0.625 |
   | What is the benchmark? — 0.200 | …of HDFC Large Cap? — 0.619 |
   | What is the AUM? — 0.236 | …of HDFC Flexi Cap? — 0.675 |
   | What is the rating? — 0.252 | …of HDFC Large Cap? — 0.678 |
   | What is the risk? — 0.240 | …of HDFC Small Cap? — 0.703 |

   The corpus *does* hold every one of these answers; a rare token like "HDFC" simply
   anchors the embedding far better than "NAV" alone does. Only 1 of the 20 questions in
   the verification sweep falls below the floor (0.397 against 0.400). Lowering the floor
   is not the fix — Phase 4 traded that away for 8/8 out-of-corpus precision. The honest
   options are to ask which scheme the user means (which is what the scheme-scoped chat
   in Phase 7/8 is for) or to resolve the scheme and re-embed before searching. Worth
   deciding deliberately rather than discovering in the demo.

### Regression found and fixed during the Phase 1-6 review (2026-09-27)

`src/retrieve/retriever.py` had picked up an undocumented, untested `_NOT_ENTITY`
carve-out (`{"nav", "fund", "funds", "scheme", "schemes", "tax"}`) that un-stripped
these words from `_ENTITY_TERMS`, so they counted as grounding terms in
`content_terms()`. The stated intent -- keep "What is the tax on this fund?" from
reducing to nothing -- directly contradicted this file's own recorded decision two
sections up (leave `nav` over-stripped; an empty term list already falls back to the
floor via `_is_lexically_grounded`'s no-op case) and was covered by zero tests.

The effect: every chunk in the corpus mentions "fund", and every ELSS chunk mentions
"tax", so with `MIN_ANSWER_TERM_MATCH = 1` either word alone grounded *any* question.
`pytest -q` had 3 real (not flaky) failures in
`TestRetrieve::test_every_labelled_out_of_corpus_query_is_empty`, and the effect was
user-visible, not just a test technicality:

| query | before (wrong) | after |
|---|---|---|
| "What is the SEBI registration number of HDFC Mutual Fund?" | answered "HDFC Small Cap Fund - Direct Growth: HDFC Mutual Fund", cited | no hits, no answer |
| "Tell me about the fund manager's education background." | answered "HDFC Small Cap Fund - Direct Growth: HDFC Mutual Fund", cited | no hits, no answer |
| "What is the eligibility criteria for the tax saver fund?" | answered the ELSS scheme's generic description sentence, cited | no hits, no answer |

**Fix:** removed the carve-out entirely (`_ENTITY_TERMS` keeps `nav`/`fund`/`funds`/
`scheme`/`schemes`/`tax` stripped, matching the tested Phase 4 behaviour). Re-verified
after the fix: full suite green (0 failures), `ruff check .` clean, all 3 queries above
now correctly return no hits, and the Phase 4/6 verification-block questions ("exit
load of HDFC Large Cap", "minimum SIP amount", "ELSS lock in period", "expense ratio of
HDFC Small Cap") are unaffected.

## Phase 5 — Guardrails / policy layer

`src/guards/copy.py`, `src/guards/policy.py`, `src/guards/__init__.py`,
`tests/test_policy.py` (107 tests). Suite total **285 passed**, lint green.

### The pattern table is where the bugs were, not the routing

Routing was the easy half — the plan's own table routed correctly on the first run. All
three real defects were in individual regexes, and all three were found by writing a test
that assumed the pattern *should* match something, then finding it did not.

**Deviation 14: the plan's phone pattern matches every date.** The specified
`\+?\d[\d\s\-]{8,14}\d` also matches `2026-09-27`, because a date is 8 digits and 2
separators inside that character class. Uncorrected, *"What was the exit load as on
2026-09-27?"* is refused as PII. This is not hypothetical: the corpus carries a
`fetched_at` date on every chunk, so a date is exactly the kind of thing a user of a
facts assistant asks about, and the refusal would be silently wrong rather than loudly
broken. A date-shaped match is now discarded (`_is_phone_candidate`), and there are three
tests pinning it. The general lesson: a character-class digit run needs an exclusion
rule before it is trustworthy on financial text, where dates and percentages are native.

**Deviation 15: the plan's account/IFSC pattern never matches a real IFSC.** The
specified context form `\b(ifsc)\b.*\b\d{6,}\b` requires a word boundary before the
digits, but an IFSC is four letters then digits (`HDFC` + `0000123`), so the boundary
is between `C` and `0` — both word characters — and there isn't one. **The pattern
matched every bare account number and no IFSC whatsoever.** Replaced with a digit
lookaround, `(?<!\d)\d{6,}(?!\d)`, which matches both a letter-prefixed IFSC and an
all-digit account number. Worth noting that this is the failure mode a spec-literal
implementation hides best: the regex looks reasonable, matches the obvious test case, and
is wrong for the actual format it was written for.

**Deviation 16: `performan(ce|t)` missed "performed".** The plan lists `performance`,
which does not match *"how has HDFC Flexi Cap performed"* — a natural phrasing of a
performance question, and one the demo is likely to use. Widened to
`perform(ance|ances|ed|ing|ant)`.

One pattern was also **tightened** rather than widened: `ranking` became
`rank(ing|ed)` instead of `rank(ing|ed)?`. The bare word `rank` appears in this corpus
(`Fund house` → `Rank (total assets)`), so a bare-`rank` pattern would refuse the
perfectly answerable question "what is HDFC's rank by AUM?".

### One PII value can legitimately produce two labels

The plan's example — a PAN and a 12-digit Aadhaar written with spaces — logs
`pii_detected=aadhaar,pan,phone`, because a spaced 12-digit Aadhaar also satisfies the
looser phone pattern. `_detect_pii` reports *every* class that matched rather than
stopping at the first, and over-reporting a class is the correct direction for a privacy
log: silently dropping one would be the dangerous direction. This is documented at the
table and pinned by a test so the behaviour is a decision rather than an accident.

### The evaluation order has exactly one load-bearing rule

PII → advice → returns → grievance. Only the first position is a safety property: a
message containing both a PAN and "should I buy" must not be answered with the advice
copy, because that copy invites the user to keep typing, and the PAN they just pasted
would be the thing they retype. There is a test for that case.

The other three positions are convention. The plan hedges its own example — *"Which is
the best performing ELSS?"* is described as `advice` or `returns` with the order left
documented — but the hedge is unnecessary: the question matches no advice pattern
(`best (fund|scheme|...)` needs a scheme noun, and this has none), so it routes to
`returns` deterministically. The tests assert the exact kind rather than membership in a
set, because a test that accepts either answer cannot detect a regression in the other.

### Layering: the one place the guard reaches downstream

The returns copy calls for "the scheme page if one was detected, else the corpus
allowlist index". Detecting the scheme means calling `detect_scheme_id`, which lives in
`src/retrieve/`, i.e. *downstream* of the guard in architecture.md §7. The import is
therefore lazy, confined to the returns branch, and wrapped so that any failure renders
the no-scheme copy instead of breaking a refusal. The rationale is that `detect_scheme_id`
is a pure string function — no model, no store, no I/O — so this is a code dependency
rather than a runtime one, and importing it eagerly would have pulled chromadb into the
guard layer for no benefit.

The fallback is a phrase pointing at the app's Sources panel, **not** a URL. There is no
public page listing "the 5 schemes this bot covers", and inventing one would be a
fabricated citation, which INV-1 and Phase 6 both exist to prevent.

Two consequences worth flagging for later phases:

* A returns refusal for a *named* scheme carries an allowlist `groww.in` link. That is
  the plan's instruction and it points at the factsheet the copy tells the user to read,
  but Phase 7's "an advice reply contains no `groww.in` citation" test must be written
  for the advice kind specifically, or it will fail on a returns refusal that is
  behaving correctly.
* `sebi` is in the plan's `GRIEVANCE_PATTERNS`, so *"What is the SEBI registration
  number of HDFC Mutual Fund?"* — one of the 8 out-of-corpus queries in
  `scripts/tune_floor.py` — becomes a `grievance` refusal in the app rather than a
  no-answer. That is defensible (it is a regulator matter) but it is a different user
  experience than the tuner measures, and the tuner still calls `retrieve` directly, so
  the two numbers stay consistent.

### Over-refusal was the risk worth measuring

A guard that refuses a legitimate fact is worse for this demo than one that lets a
performance question through, because the factual questions are the product. All **15**
factual questions that Phases 6–9 depend on (the 8 `answerable` rows in
`scripts/tune_floor.py` plus the 7 from the plan's Phase 6/7 examples) were run through
`check_query`: **all 15 are allowed.** The date, `rank`, and `performance` fixes above all
came out of that sweep rather than out of reading the patterns.

### INV-3 verified two ways

* **No value escapes.** Six fake identifiers (PAN, Aadhaar, email, phone, OTP, account)
  are asserted absent from the decision, the message, the link, the label, and every
  captured log record — for queries that also trip an intent gate, so the advice copy is
  proven not to be the leak path.
* **No value is even logged.** A PII-flagged query logs only `kind=pii
  pii_detected=<labels>`. A 60-char prefix is logged for allowed and non-PII-refused
  queries only, and a test asserts the prefix is absent from PII lines — because
  `"My PAN is 1234..."[:60]` is still PII, which is the non-obvious part of "log a
  prefix".
* **Nothing is written.** 54 guard calls over mixed seeded and realistic queries left
  all 75 repo files byte- and mtime-identical.

The plan's own closing check — the fake PAN/Aadhaar must appear only in `tests/` — also
caught me: an explanatory comment in `policy.py` quoted the plan's example verbatim, so
the grep hit shipped source. The comment now describes the values instead of reproducing
them. The only remaining hits outside `tests/` are in `docs/implementation.md`, which is
the plan's own text.

---

## Phase 7 — Chat service orchestration

`src/chat_service.py` (`ChatTurn`, `recent_turns()`, `EXAMPLE_QUESTIONS`,
`SAMPLE_QUESTIONS`, `format_reply`, `handle_message`), `tests/test_chat_service.py`
(29 tests). Full suite **378 passed**, lint green.

### The sequence is provable, not just intended

`handle_message` is guard → retrieve → generate → format in that literal order in the
source, with one `logger.info` after each stage plus a `total` line. INV-4 (a refusal
never reaches the retriever) is asserted directly: a monkeypatched `retrieve` that
raises `AssertionError` if called is installed before sending an advice, a returns, and
a PII query, and all three still produce the expected refusal text with the spy
untouched.

### Deviation 17: `RetrievalError` is caught here, not left for the UI

The plan's Phase 7 task says "wrap in try/except for domain exceptions", and Phase 8's
task (not yet built) separately says the UI should "catch `RetrievalError`" to show a
"run `make ingest`" panel. Taken literally that is a contradiction: if `handle_message`
swallows every domain exception into a friendly refusal, `RetrievalError` never reaches
`app.py` to catch.

Resolved in favour of never leaking a domain exception type out of this module, which is
what Phase 7's own goal ("one function the UI calls") is for. `RetrievalError` is caught
here specifically -- before the general `ChatbotError` catch -- and rendered as its own
refusal kind (`retrieval_error`) with a message that already says to run `make ingest`.
Phase 8's UI does not need to catch `RetrievalError` itself; it can render
`reply.refusal.kind == "retrieval_error"` as the setup panel if it wants a distinct
visual treatment, or just show `reply.text`, which is already correct either way.

### Deviation 18: the "nothing written" test must warm the store first

Chroma's persistent client touches (but does not resize or change the content of) its
own HNSW index files -- `header.bin`, `data_level0.bin`, `length.bin`, `link_lists.bin`
-- and `chroma.sqlite3` the first time a process opens the collection. Sizes are
byte-identical before and after; only mtimes move. This is a real effect (confirmed with
a fresh interpreter: 5 files touched on the very first `retrieve()` call, none touched
on the next five), and it is entirely the library's own behaviour, not something this
project's code writes.

The first version of `TestNothingIsPersisted` snapshotted `data/` before *any* call in
the test session, so it passed only when some earlier test class in the same file had
already warmed the collection -- an order dependency that `pytest
tests/test_chat_service.py::TestNothingIsPersisted` alone (no earlier class to warm it)
caught immediately. Both persistence tests now call `handle_message` once, unchecked, to
warm the store before taking the "before" snapshot, so the test's outcome does not depend
on what ran earlier in the file. What it verifies is exactly the plan's claim -- handling
messages writes nothing -- not the unrelated claim that opening a store for the first
time writes nothing.

### Deviation 19: an uncited answer_text fails closed to no-answer

`answer_question`'s contract is "text implies a citation", and every current code path in
`src/generate/answer.py` upholds it. `handle_message` does not assume that holds forever:
if a future change to generation ever produced `answer_text` without `source_url`, showing
it would violate INV-1 (a citation is a separate, load-bearing field the UI renders next
to the text, not a decoration). `handle_message` checks for that case explicitly and
routes it to the no-answer message with a warning log line, rather than trusting the
contract silently. This path is unreachable with the current generator and only guards
against a future regression.

### `format_reply`'s scope

Per the plan, `format_reply(answer: Answer) -> str` builds the visible text for a
*factual* answer only -- body, `Source:`, `Last updated from sources:`, the disclaimer,
each on its own line. A guard refusal's text is `decision.message` as `copy.py` already
wrote it (already self-contained, with any educational link inline), and a no-answer
turn's text is `no_answer_message(...)` as Phase 6 already wrote it. `format_reply` is
still "the only function producing user-visible text" in the sense the acceptance
criterion means: `handle_message` never builds a reply string by ad hoc concatenation
anywhere in its own body: it always calls exactly one of these three already-reviewed
message functions.

### Verified

* Guard → retrieve → generate → format is the literal statement order, with per-stage
  timing logged; a spy that raises if called proves advice/returns/PII refusals never
  invoke `retrieve`.
* A 50-turn mixed session (factual, advice, PII, returns, off-corpus, empty), after
  warming the store once, leaves every file under `data/` and under the project root
  (excluding `.venv`/`.git`/cache directories) byte- and mtime-identical.
* Every factual reply carries exactly one `https://groww.in/` URL and a
  `Last updated from sources:` line; an advice reply carries the AMFI URL and no
  `groww.in` URL; a PII reply contains neither the fake PAN nor the fake Aadhaar.
* `recent_turns()` is a bounded, in-memory-only ring buffer (`maxlen=50`); nothing it
  holds is ever written to disk.

---

## Post-Phase-7 fix: generic time modifiers as a false grounding signal (2026-09-27)

Found by manually testing the running chatbot (not by the test suite) with *"what is
current NAV of HDFC Small Cap Fund Direct Growth?"*. It answered:

> HDFC Small Cap Fund – Direct Growth: Dhruv Muchhal is the Current Fund Manager of
> HDFC Small Cap Fund Direct Growth fund.

-- a confidently-cited, completely wrong answer to a different question, worse than a
refusal because nothing about the reply signals it might be wrong.

### Root cause

`content_terms()` strips the scheme name as entity words, and previously stripped "nav"
too (a known, accepted gap: see Phase 4/6 notes, "`_ENTITY_TERMS` over-strips nav" --
"the answer is correct and cited, just wordy"). For this question, that left exactly one
surviving word: **"current"**. Every retrieved chunk contains "currently" somewhere
(`_stem` does not fold it to "current" -- see the word-boundary bug note in Phase 4),
but by coincidence one sentence contains the literal word "Current": *"Dhruv Muchhal is
the **Current** Fund Manager of..."*. `generate_template` ranks a chunk's units by how
many question terms they match, and that sentence was the *only* unit matching the sole
surviving term -- so it won outright, regardless of being about a different fact
entirely.

This is a more severe failure mode than the accepted "nav" gap: that gap always produced
an empty term list, which is a documented no-op that falls back to unstripped matching.
Here a *non-empty* single-word term list survived, and it was pure noise -- a temporal
modifier the corpus could never contain a literal match for -- so it should never have
been eligible to decide the ranking in the first place.

### Fix

Two changes in `src/retrieve/retriever.py`:

1. **`_STOPWORDS` gained temporal fillers**: `current`, `currently`, `latest`, `recent`,
   `recently`, `now`, `today`, `existing`, `present`. None of these names a fact the
   corpus could contain, so none should ever count as a content word -- for retrieval
   grounding or generation ranking alike, since both call the same `content_terms`.
2. **`nav` was removed from `_ENTITY_TERMS`.** Checked before doing this: "nav" appears
   in exactly 10 of 30 chunks -- the one hero block and one About paragraph per
   scheme -- never in a fund-house, glossary, or exit-load-only chunk. That is
   structurally nothing like "fund" (every chunk) or "tax" (every ELSS chunk), which is
   why those two stay stripped. "nav" is a field name like "expense ratio" or "exit
   load", not a scheme-identity word, and the chunks it does appear in are exactly the
   ones where it is the right thing to rank on.

Verified: the reported question now answers *"The fund currently has an Asset Under
Management(AUM) of ₹9,86,237 Cr and the Latest NAV as of 25 Sep 2026 is ₹159.82"* --
correct and cited, if a little more than strictly asked. Re-phrased without "current",
*"what is the NAV of HDFC Small Cap?"* now answers the clean hero-block figure, *"NAV:
25 Sep '26: ₹159.82"* — an improvement over its own pre-fix answer, which stapled on an
unrelated AUM figure. Full suite green (383 tests) after adding
`TestContentTerms::test_nav_is_a_content_word_not_an_entity`,
`test_generic_time_modifiers_are_stopwords`, and
`TestFactualQuestion::test_current_nav_question_answers_nav_not_fund_manager`; the three
regressions from the earlier `_NOT_ENTITY` fix (SEBI registration, fund manager
background, ELSS eligibility) were re-checked and remain correctly refused.

**Open, not fixed here:** the same class of bug can recur for any generic word that
happens to coincide with an unrelated sentence in the same chunk (the risk is inherent
to single-term ranking on short text, not specific to "current"). A stem/synonym table
was already flagged as future work in Phase 6's notes for a related problem (`lump` vs
`Lumpsum`); a scored ranking (e.g. weight by term rarity across the corpus, or require
the winning unit's section to match the question's field) is the more durable fix and is
future work, not part of this pass.

---

## Post-Phase-6 addendum: wiring up the Groq LLM path (2026-09-27)

Phase 6 built `_build_client()` as a documented, deliberately-`None` seam ("no LLM SDK
is a dependency of this project"). The user chose Groq
(https://console.groq.com/keys, model `llama-3.1-8b-instant`) as the provider, so this
addendum implements that seam rather than leaving it a stub.

`src/generate/llm_client.py` is new: `GroqClient.complete(system, user, temperature)`
calls Groq's OpenAI-compatible chat completions endpoint via `requests` (already a
project dependency, used by `src/ingest/load.py`) and returns the assistant text, or
raises on any failure -- a bad key, a timeout, a non-2xx status, a malformed response
body. It deliberately does not catch its own exceptions: `generate_llm` in
`src/generate/answer.py` is the layer that decides "any exception means fall back to
the template", so this class must not make that decision itself. `build_groq_client()`
returns `None` when neither `config.GROQ_API_KEY` nor the generic `config.LLM_API_KEY`
is set, which is exactly the state a fresh clone is in.

`_build_client()` now delegates to `build_groq_client()` instead of returning `None`
unconditionally. `config.py` gained `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_API_URL`,
`GROQ_TIMEOUT_SECONDS`. `.env` and `.env.example` both document the new fields.

**This is the one place in the project that calls the network at query time, and that
is by design, not a violation of INV-6.** `GENERATION_MODE=template` -- the default --
never calls `_build_client()` at all (`answer_question` only reaches it in the `mode ==
"llm"` branch), so the offline guarantee holds for anyone who has not explicitly opted
in. `GENERATION_MODE=llm` was always meant to be the one documented exception, with a
template fallback on any failure; this addendum makes that mode functional instead of a
no-op that silently always falls back.

**Verified with mocks:** `tests/test_llm_client.py` (7 tests) exercises
`GroqClient.complete` against a fake `requests.post` -- correct request shape
(headers, model, messages, temperature), correct content extraction, an `HTTPError`
on a 4xx/5xx status, and a `ValueError` on an unexpected response shape -- so no test
in the suite makes a real network call. `GENERATION_MODE=llm` with no key configured
still answers correctly via the template fallback (`generate: no llm client wired up;
using template`), and a `GroqClient` that fails mid-call (mocked as a 500) still
yields the identical template answer through `generate_llm`'s own fallback.

### Verified live, with a real key

Two things only a real call could catch surfaced immediately:

1. **`llama-3.1-8b-instant` no longer exists on Groq.** A live call returned
   `404 model_not_found`. `GET /openai/v1/models` against the same key listed the
   account's current catalog, which does not include any plain `llama-3.1-*` model
   at all (it includes `openai/gpt-oss-120b`, `openai/gpt-oss-20b`,
   `qwen/qwen3.8-27b`, and a few audio/guard models) -- Groq's catalog has clearly
   moved on since the model name was chosen. `openai/gpt-oss-20b` (small, fast,
   instruction-tuned -- the closest match to what was asked for) is now
   `config.GROQ_MODEL`'s default and is confirmed working with a real call.
2. **Setting `GENERATION_MODE=llm` in `.env` while testing broke the test suite's
   offline guarantee.** With a real key and mode in `.env`, `pytest` immediately
   started making live Groq calls for every test that calls `answer_question`
   without itself forcing a mode (`config.GENERATION_MODE` is read from the
   environment at import time, and most tests never override it) --
   `test_no_number_is_invented` failed non-deterministically on live model output.
   This is a real defect, not a one-off: any contributor's local `.env` left in
   `llm` mode would silently make every future `pytest`/`make test` run
   network-dependent, slow, non-deterministic, and would burn real API quota.

   Fixed with `tests/conftest.py`: a session-wide `autouse` fixture that pins
   `config.GENERATION_MODE` to `"template"` for every test, regardless of what a
   local `.env` says. A test that specifically wants `llm` mode (`TestGenerateLlm` in
   `tests/test_answer.py`) sets it explicitly inside its own body, which runs after
   the fixture and still wins. Verified both ways: with `.env` forced to `llm` mode,
   the full suite (391 tests) still passes and stays offline; reverted back to
   `template` afterward as the resting default.

With the model fixed and `GENERATION_MODE=llm` set, live end-to-end queries through
`handle_message` were confirmed grounded and correctly capped: *"what is current NAV
of HDFC Small Cap Fund Direct Growth?"* → *"The current NAV of HDFC Small Cap Fund
Direct Growth is ₹159.82."* (one unit, correctly cited -- an improvement over the
template path's answer to the same question, which includes an unrelated AUM figure);
advice/PII/no-answer routing all still worked identically, since the guard runs before
generation regardless of mode. Full suite: **391 passed**, lint green, with `.env`
left in its safe resting state (`GENERATION_MODE=template`) so cloning this repo and
running `make test` stays fully offline by default.

---

## Phase 8 — UI

`src/app.py`, `tests/test_app.py` (15 tests), `README.md` quickstart stub. Full
suite **403 passed**, lint green.

### The launch-blocking bug only a real launch could catch

`streamlit.testing.v1.AppTest` runs a script inside the calling pytest process, which
already has the project root on `sys.path` (pytest arranges that itself). A real
`streamlit run src/app.py` does not: Streamlit puts only the script's own directory
(`src/`) on `sys.path`, so `from src.chat_service import ...` raised
`ModuleNotFoundError: No module named 'src'` immediately on startup -- `make app`, the
literal command the README tells a new user to run, would have failed outright. All 15
`AppTest`-based tests passed the whole time, because they never exercise this path.

Caught by actually running `streamlit run src/app.py --server.headless true` and
curling it, which is now part of how this phase was verified rather than trusting
`AppTest` alone. Fixed with a `sys.path.insert(0, str(project_root))` at the top of
`app.py`, before any `src` import -- the one place in the project that needs it,
since every other entry point (`python -m src.ingest.pipeline`, pytest) already
gets this for free. Re-verified with a second real launch: clean startup, no
traceback, `curl` returns 200.

### The sidebar counters lagged by exactly one turn

The first version computed `st.session_state["messages"]` answered/refused counts
inside the same `with st.sidebar:` block as the corpus list and checkbox, which runs
*before* the current turn is appended further down the script. So after 4 turns (1
answer, 3 refusals), the sidebar showed 1 and 2, not 1 and 3 -- always one turn stale.
`AppTest` caught this immediately once a test actually asserted the count value rather
than just checking "no exception". Fixed by splitting the sidebar into two `with
st.sidebar:` blocks -- corpus list and checkbox first (the history-rendering loop
needs the checkbox value), counts and the reset button in a second block placed after
the new turn is appended. Streamlit appends to the same sidebar container across
multiple `with` blocks in one script run, so the sidebar still renders as one piece.

### `DISCLAIMER` was not actually PRD Appendix A

Phase 5's `guards/copy.py` comment claimed `DISCLAIMER` was "the persistent
disclaimer the UI shows on every load (PRD Appendix A)" -- but it was a paraphrase
("This assistant shares published facts from 5 HDFC Mutual Fund scheme pages
only...") that had never been diffed against `PRD.md`'s actual Appendix A text
("Facts-only assistant. Answers are based on public scheme pages and are not
investment advice. Do not share PAN, Aadhaar, account numbers, OTPs, or other
personal data."). This phase's own task -- render "the exact Appendix A copy from
PRD" -- is what surfaced the mismatch. Fixed at the source (`copy.py`) rather than
in `app.py`, since `format_reply` (Phase 7) already appends the same constant to
every factual answer: one fix corrects both the UI and every chat reply, and Phase
10's README will inherit the corrected text automatically rather than needing its
own separate fix.

### Two defensive choices, both because a plan step turned out unnecessary

* **No `try/except` around `handle_message` in `app.py`.** The plan's Phase 8 task
  says to catch `RetrievalError` and other domain errors. Phase 7 already made
  `handle_message` never raise them -- it catches every `ChatbotError` itself and
  returns a `ChatReply` with a `refusal.kind` of `"retrieval_error"` or `"error"`
  instead. `app.py` branches on `reply.refusal.kind == "retrieval_error"` to show
  the "run `make ingest`" panel with its own visual treatment, which is simpler and
  provably exhaustive (there is no exception path left uncovered) rather than
  wrapping a call that cannot raise.
* **User text is escaped before `st.markdown`, not passed through unsafe_allow_html
  (which the plan already says not to use).** A small `_escape_markdown` prefixes
  every markdown-special character (`[`, `]`, `(`, `)`, `*`, `_`, backtick, `#`) with
  a backslash before a user's own typed message is rendered, so `[click
  here](http://evil.example)` displays as literal bracketed text instead of a
  clickable link. Assistant replies are never escaped this way, because they are
  always one of this project's own curated strings (extractive/LLM-grounded answer,
  refusal, or no-answer message), never raw user input.

### A near-miss: simulating "no store" almost corrupted the real store

The first version of the missing-ingest test pointed `config.CHROMA_COLLECTION` at
`"nonexistent_collection_for_test"` to make `collection_exists()` return `False`.
`store.get_collection()` calls `client.get_or_create_collection(...)`, so the very
act of *retrieving* against that name during the test's "ask a question anyway" step
silently created a real, permanent, empty collection under that name in the actual
`data/chroma` store on disk -- `client.list_collections()` afterward showed both
`mf_faq_hdfc` (30 chunks, untouched) and the stray empty one. Caught immediately
after the test run by inspecting the store directly, cleaned up with
`client.delete_collection(...)`, and verified the real collection's count was still
exactly 30. The test was rewritten to mock `src.ingest.store.collection_exists` and
`src.chat_service.retrieve` directly (function-level, matching the "retriever never
called" spy pattern already used in `tests/test_chat_service.py`) instead of trying
to simulate the missing-store condition through real config and a real query --
which touches nothing on disk and is also a more precise test of the two code paths
that actually matter (`app.py`'s proactive check, and `handle_message`'s own
`RetrievalError` handling).

**Worth knowing, not fixed here:** `store.get_collection()`'s `get_or_create_collection`
semantics mean any code path that ends up calling it with a name that doesn't yet
exist will silently create it, rather than failing. This is correct and intended for
`make ingest` itself, and is harmless for `retrieve()` in normal operation (the
collection name is a fixed constant, `RetrievalError` still fires correctly on an
empty count either way) -- but it is a sharp edge for any future test or script that
changes `config.CHROMA_COLLECTION` at runtime without also isolating `CHROMA_PATH` to
a temp directory, as `tests/test_store.py` and `tests/test_pipeline.py` already do.

### Verified

* Every item in the plan's manual checklist, both via `AppTest` and, for the launch
  path specifically, a real `streamlit run` server hit with `curl`.
* Markdown injection: `[click here](http://evil.example) **bold** _em_` typed as a
  chat message renders as literal escaped text in the user's own bubble, not a link
  or emphasis.
* The "show retrieved chunks" checkbox: off by default (no expander in a reply),
  produces an expander listing `scheme_id`/`section`/`score` per hit when checked.
* Reset conversation: clears `st.session_state["messages"]` immediately (0 chat
  messages rendered afterward).
* The real `data/chroma` store is unchanged after the full test run: still exactly
  one collection (`mf_faq_hdfc`), still 30 chunks.

---

## Post-Phase-8 addendum: conversation memory for retrieval (2026-09-27)

`handle_message(question, history=None)` gained an optional `history` parameter: up
to the last `config.MEMORY_WINDOW` (10) of the caller's own prior `ChatReply`s. When
the current question names no scheme at all -- not an indexed one, not a competing
brand, not a known-and-absent HDFC scheme -- it resolves the most recently discussed
scheme from that history and uses it to scope retrieval, so "what about the minimum
SIP amount?" after asking about HDFC Small Cap answers about HDFC Small Cap instead
of running an unscoped search or failing. `src/app.py` passes its own
`st.session_state["messages"]` as `history`; omitting the parameter (every existing
caller) behaves exactly as before.

### Memory is session-scoped by construction, not by discipline

This module already had a global, cross-session ring buffer (`_recent_turns`, behind
`recent_turns()`) for the debug panel. It was never a candidate for this feature:
using it for retrieval would let one browser tab's in-progress fund leak into
another tab's follow-up question on a shared server. `history` is instead an
argument the caller owns and passes in explicitly, so there is no way to wire this
feature up wrong and get cross-session leakage -- the only history `handle_message`
ever sees is whatever a specific caller hands it for that specific call.

### Two things had to be fixed before memory actually worked, not just resolved

Resolving the right scheme_id was the easy half. `retrieve(question, scheme_id=...)`
only adds a metadata filter; it does not change what the question's embedding
means, and a bare follow-up like "what about the minimum SIP amount?" or "and the
risk level?" embeds too weakly to clear `SIMILARITY_FLOOR` *regardless* of which
chunks it is filtered against, because cosine similarity reflects the query's own
semantic content, not which subset was searched. Phase 4's own notes had already
measured and named this precisely -- naming the scheme lifts a question's top
similarity by 0.15-0.48, enough that 5 of 7 unscoped attribute questions fall below
the floor on their own -- and suggested "resolve the scheme and re-embed before
searching" as the fix, rather than lowering the floor. This addendum is that fix:
`_add_remembered_scheme_to_query` prepends the resolved scheme's full display name
to the question before it reaches `retrieve`, only when memory (not the question
itself) supplied the scheme.

A first version prepended the shortest `SCHEME_ALIASES` entry instead of the full
display name -- "80c" for the ELSS scheme, since it ties on length with "tax" but is
found first while building the reverse lookup. Confirmed empirically that this does
not reliably anchor the embedding at all (0 hits for "80c what about its exit
load?", and even "elss" alone also failed); the full display name
("HDFC ELSS Tax Saver – Direct Growth") worked for all 5 schemes, since it is close
to how each scheme names itself in its own corpus text. Fixed by building the anchor
from `load_sources()`'s `scheme_name` instead of `SCHEME_ALIASES`.

### A pre-existing generation bug this newly exercised, and fixed

With retrieval finally finding the right chunk, "what about its exit load?" (after
discussing ELSS) answered *"HDFC ELSS Tax Saver – Direct Growth: Exit load, stamp
duty and tax"* -- the section title, not the fact. `generate_template` ranks a
chunk's units by term matches, then by whether a unit "carries a value"
(`_HAS_VALUE`, a digit) as a tiebreak, then by length. Both "Exit load, stamp duty
and tax" (the title) and "Exit load: Nil" (the fact) match the same two terms, and
neither contains a digit -- ELSS genuinely has no exit load, so its value is the word
"Nil" -- so the tie fell to "longer unit wins", and the title is longer than "Exit
load: Nil". `_carries_value` now also recognises a `"Label: <non-empty text>"` shape
regardless of whether the value has a digit in it, which is what every other hero-
block fact in this corpus already looks like; the digit-only cases (SIP amounts, NAV,
expense ratio, all elsewhere in the same corpus) were and remain unaffected, since
they already had a digit and already passed the old check.

### Verified

* `tests/test_chat_service.py` gained `TestInferSchemeFromHistory`,
  `TestNamesAnyScheme`, and `TestConversationMemory` (15 tests): a scheme-less
  follow-up resolves the prior scheme; naming a different scheme overrides memory;
  naming a competing brand or an out-of-corpus HDFC scheme is never overridden by
  memory; a scheme more than `MEMORY_WINDOW` turns back is not found; guardrails
  still refuse before retrieval regardless of history (a `retrieve` spy that raises
  if called, exactly as Phase 7's own INV-4 tests do); `history=None` and omitting
  the parameter produce identical answers; a 3-turn memory-driven conversation
  leaves `data/` byte- and mtime-identical.
* A live 10-turn conversation exercising every path -- scheme continuity across 3
  turns, an explicit scheme switch, a guard refusal and a PII refusal interleaved
  (history must survive them without breaking), an out-of-corpus follow-up, and a
  second independent scheme thread -- produced correct, cited answers at every step
  with no exception.
* Full suite: **418 passed**, lint green.

### Found, not fixed here: a second, unrelated pre-existing generation bug

*"What is the benchmark of HDFC Balanced Advantage?"* -- asked directly, with no
memory involved at all -- answers *"HDFC Balanced Advantage Fund – Direct Growth:
Fund benchmark"*, again just the label. `data/corpus/hdfc-balanced-advantage-direct-
growth.md` has this fact as two separate lines (`Fund benchmark` / `NIFTY 50 Hybrid
Composite Debt 50:50 Index`), unlike every other scheme's single-line `Fund
benchmark: <index>`, so `split_units`' newline-based splitting makes them two
separate units and the value-bearing one matches zero question terms (`benchmark`
is in the label unit, not the value unit) and is never selected at all -- this is a
different failure shape than the exit-load bug above (that one selected the wrong
unit; this one has no candidate unit that both matches a term and carries a value).
Confirmed via a direct call with no history, so it is unrelated to this addendum.
Flagged to the user rather than fixed here, since it is out of this task's scope and
specific to one fact on one scheme.

---

## Post-Phase-8 addendum: deploy-friendly for a 512MB host (2026-09-27)

The Streamlit app showed a blank page on Render's free tier. Four changes, all
aimed at the same two constraints a free-tier host has that a laptop does not:
very little RAM, and no visible feedback while something slow happens.

### CPU-only torch

`sentence-transformers` never runs anything but CPU inference here (no GPU on
Render, and this corpus is 30 chunks), but plain `torch` from PyPI installs the
CUDA-enabled build, which is several times larger and can exhaust a 512MB build.
`requirements.txt` now pins `torch==2.14.0` with
`--extra-index-url https://download.pytorch.org/whl/cpu` above it. Verified against
the real index rather than assumed: `pip install torch==999.999.999 --index-url
.../cpu --dry-run` listed every version the CPU index actually has (up to 2.14.0),
and `pip install torch==2.14.0 --extra-index-url .../cpu --dry-run` resolves to
`torch-2.14.0+cpu-*.whl`, not the default PyPI wheel -- confirmed for the exact
Python 3.13 / linux_x86_64 target a Render instance would use.

### The title now renders before this project's own code is imported

`import src.chat_service` (and the modules it pulls in) transitively imports
chromadb and sentence-transformers/torch -- profiled at ~5 seconds of pure import
time locally, before the embedding model has even loaded. Since Python cannot
reach a single line of `app.py` past its own imports until they finish, and all of
this project's imports used to sit above `st.set_page_config`, the page had
nothing to show for that whole stretch. `st.set_page_config`, the title, and the
welcome caption now run first, immediately after `import streamlit as st` and
before any `from src...` import -- Streamlit streams each `st.*` call to the
browser as it executes rather than batching them, so this is visible right away.

### Model and store loading is now explicit, cached, and spinner-visible

`embed.py` and `store.py` already keep process-lifetime singletons for the model
and the Chroma client, so nothing was reloading redundantly. What was missing was
visibility: that first load happened silently inside whichever query triggered it
first, which from a user's side looks identical to a hang. A new `_warm_up()` in
`app.py`, wrapped in `@st.cache_resource(show_spinner=...)`, calls `embed_query`
and opens the collection once, right after the page shell renders. `st.cache_resource`
caches the return value process-wide, matching the singletons' existing lifetime,
so this adds a spinner without changing how often anything actually loads.
`_warm_up()` deliberately does not catch its own exceptions -- `st.cache_resource`
does not cache a raised exception, so a transient failure (a slow model download,
a cold disk) is retried on the next page load instead of being stuck returning the
same failure until the process restarts; the try/except lives one level up, around
the *call* to `_warm_up()`, purely to show a friendly message for that one failed
run.

**A real caching gotcha this created for tests, not for production:** `st.cache_resource`
caches by the function's own code, not per script run, so `tests/test_app.py`'s
`AppTest`-based tests all share one process and therefore one cache. A test that
mocks `collection_exists` to simulate a missing store got back the *previous*
test's real, successful warm-up (chunk count 30) instead, silently ignoring its own
monkeypatch. Fixed with an autouse fixture that calls `st.cache_resource.clear()`
before every test in that file -- correct in production, where there is only ever
one process and clearing is never needed.

### The vector store is now committed, not rebuilt at deploy time

`data/chroma/` (1.3MB) is no longer gitignored. A 512MB free-tier build has too
little memory and too little build-time budget to comfortably re-run the full
embedding pipeline on every deploy, and doing so also needs reliable network
access to Hugging Face at build time. Shipping the already-built index removes
both requirements from the deploy path entirely: `pip install -r requirements.txt`
is now the whole Render build command, no `make ingest`/`--rebuild` step. Re-run
`make ingest` locally and commit the result whenever `data/corpus/` changes --
documented directly in `.gitignore` at the point it stops ignoring this path, so
the reason is visible exactly where someone would otherwise assume it is still
ignored.

### Verified

* Full suite: **418 passed**, lint green, `tests/test_app.py`'s cache-isolation fix
  confirmed by re-running with and without it (fails predictably without the
  autouse fixture, in exactly the way described above).
* `pip install -r requirements.txt --dry-run` resolves cleanly end to end, torch
  included, confirming the file's syntax and index configuration are valid.
* A real `streamlit run src/app.py` launch, not just `AppTest`, still starts clean
  with the new load order (same verification habit Phase 8 established after the
  `sys.path` bug that only a real launch had caught).

**Not verified: an actual Render deployment.** All of the above is verified
locally and via the test suite; Render's own memory ceiling, network egress, and
build-time behavior can only be confirmed by deploying there.
