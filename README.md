# HDFC MF Facts Assistant

A facts-only RAG chatbot over 5 HDFC mutual fund scheme pages. Full write-up (scope,
architecture, guardrails, known limits) lands in Phase 10 — this is the Phase 8
quickstart stub so the app is runnable in the meantime.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make ingest
make app
```

`make ingest` builds the local vector store from `data/corpus/` (run once after
clone, or after editing the corpus). `make app` starts the Streamlit UI.
