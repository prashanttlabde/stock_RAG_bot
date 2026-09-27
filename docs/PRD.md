# PRD: Mutual Fund Facts-Only RAG Chatbot

**Product:** FAQ assistant for mutual fund scheme facts  
**Audience:** Class / milestone demo  
**Status:** Draft  
**Last updated:** 2026-09-27  

---

## 1. Overview

Build a small RAG chatbot that answers **factual questions only** about a fixed set of HDFC mutual fund schemes, using public pages as the sole knowledge source. Every answer must cite one source link. The product must refuse advice, avoid PII, and not make performance claims.

This is a working prototype to demonstrate a full RAG pipeline: **Load → Chunk → Embed → Store → Retrieve → Generate**.

---

## 2. Problem

Retail users and support/content teams repeatedly ask the same scheme facts (expense ratio, exit load, SIP minimum, ELSS lock-in, riskometer, benchmark, how to get statements). Answers should come from official/public pages with a visible citation—not from blogs or opinionated guidance.

---

## 3. Goals

| Goal | Success look |
|------|----------------|
| Facts-only Q&A | Answers stay within corpus facts; no buy/sell advice |
| Traceable answers | Every answer includes exactly one clear source URL |
| Full RAG demo | Pipeline covers ingestion + retrieval end-to-end |
| Demo-ready UX | Tiny UI: welcome, 3 example questions, facts-only note |
| Safe scope | No PII collection; no returns comparison |

---

## 4. Non-goals

- Investment advice, portfolio recommendations, or “should I buy/sell?”
- Computing or comparing returns / performance rankings
- Multi-AMC coverage or live market data feeds
- User accounts, chat history persistence, or PII storage
- Production-grade auth, rate limiting, or compliance certification

---

## 5. Users & use cases

**Primary (demo):** Instructor / peers verifying RAG quality and citations.  
**Implied end users:** Retail users comparing schemes; support teams answering repetitive MF FAQs.

**In-scope questions (examples):**
- Expense ratio of [scheme]?
- Exit load?
- Minimum SIP?
- ELSS lock-in period?
- Riskometer / benchmark?
- How to download capital-gains / statements?

**Out-of-scope questions:** Opinionated or personal advice → polite refuse + educational link.

---

## 6. Scope

### 6.1 Corpus (AMC + schemes)

**AMC:** HDFC Mutual Fund (via public Groww scheme pages for demo corpus)

| Category | Scheme | Source URL |
|----------|--------|------------|
| Large Cap | HDFC Large Cap Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| Flexi Cap | HDFC Equity Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| ELSS | HDFC ELSS Tax Saver – Direct Growth | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |
| Small Cap | HDFC Small Cap Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth |
| Hybrid | HDFC Balanced Advantage Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth |

**Source policy:** Public pages only (AMC/SEBI/AMFI-style fact content via the listed public URLs). No app back-end screenshots; no third-party blogs as citations.

### 6.2 Product surface

- Welcome line
- 3 example questions
- Chat input + answer with citation
- Persistent UI note: **“Facts-only. No investment advice.”**
- Disclaimer snippet in UI (facts-only, no advice)

---

## 7. Functional requirements

| ID | Requirement |
|----|-------------|
| FR-1 | Answer factual MF scheme queries from the indexed corpus only |
| FR-2 | Include **one** clear citation link in every answer |
| FR-3 | Keep answers ≤ 3 sentences |
| FR-4 | Append “Last updated from sources: &lt;date/note&gt;” |
| FR-5 | Refuse opinionated / portfolio questions with a polite facts-only message + relevant educational link |
| FR-6 | Reject or ignore PII (PAN, Aadhaar, account numbers, OTPs, emails, phones)—do not store them |
| FR-7 | If asked about returns/performance: do not compute/compare; point to official factsheet / source page |
| FR-8 | Show welcome + 3 example questions + facts-only note on load |

---

## 8. RAG architecture requirements

Demonstrate both **data ingestion** and **data retrieval**.

```
Loading → Chunking → Embedding → Vector store
                ↓
         Query → Retrieve → Generate (facts-only + citation)
```

| Stage | Choice |
|-------|--------|
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2` |
| Vector DB | ChromaDB |
| Chunking | Decide from actual page content (structure-aware; prefer headings/sections over blind fixed splits) |

**Retrieval expectations:** Top-k relevant chunks; generation grounded in retrieved text; citation maps to the source URL of the used chunk(s).

---

## 9. UX requirements

1. Tiny, demo-friendly UI (single page is fine).
2. Clear refusal copy for advice questions.
3. Citation always visible (link text or URL).
4. Disclaimer always visible.

---

## 10. Constraints & compliance (demo)

- **Public sources only** for answers and citations.
- **No PII** accept/store.
- **No performance claims** beyond linking to official/public fact materials.
- **Clarity:** short answers + source freshness note.

---

## 11. Deliverables (submission)

1. Working prototype (app/notebook link) **or** ≤3-min demo video if hosting isn’t possible  
2. Source list (CSV or MD) of the 5 URLs used  
3. README: setup steps, scope (AMC + schemes), known limits  
4. Sample Q&A file (5–10 queries with answers + links)  
5. Disclaimer snippet used in the UI  

---

## 12. Success metrics (class demo)

| Metric | Target |
|--------|--------|
| Citation coverage | 100% of factual answers include a source link |
| Advice refusal | Advice-style prompts refused consistently |
| Answer length | ≤ 3 sentences for factual replies |
| Pipeline completeness | Ingestion + retrieval stages runnable and explainable in demo |
| Sample Q&A | 5–10 documented query/answer/link triples |

---

## 13. Milestones (suggested)

1. **Corpus** — scrape/load 5 pages; publish source list  
2. **Ingestion** — chunk + embed (`all-MiniLM-L6-v2`) + ChromaDB  
3. **Retrieval + generation** — facts-only prompt, citation, refusal path  
4. **UI** — welcome, examples, disclaimer  
5. **Demo pack** — README, sample Q&A, video or hosted link  

---

## 14. Open decisions

- Exact chunk size / overlap after inspecting page HTML/text structure  
- LLM vs template answers for generation (demo may use either if grounded + cited)  
- Hosting vs recorded demo video  

---

## 15. Risks

| Risk | Mitigation |
|------|------------|
| Groww/page layout changes | Snapshot or cache text used for indexing; note date in “Last updated” |
| Hallucinated facts | Strict grounding prompt; refuse if retrieval confidence low |
| Users ask for advice | Hard-coded refusal + educational link |
| Scraping blocks | Manual copy of public text into corpus files for demo |

---

## Appendix A — Disclaimer (UI)

> Facts-only assistant. Answers are based on public scheme pages and are not investment advice. Do not share PAN, Aadhaar, account numbers, OTPs, or other personal data.
