"""Ingestion stage 5: the persistent vector store (architecture.md §6 step 5).

Owns the Chroma collection that Phase 4 queries. Two properties matter:

* **Persistent.** The collection lives under `data/chroma/`, so a fresh process
  (and a Streamlit restart) reuses it instead of re-embedding. This is what makes
  INV-6 hold: everything after `make ingest` runs offline.
* **Idempotent.** Chunks are upserted by their deterministic `chunk_id`, so
  re-running ingest overwrites the same rows. `reset_collection()` drops the
  collection outright, which is how a model or dimension change is recovered.

Chroma rejects `None` metadata values, so a chunk with no section label is stored
with `section=""`. Phase 4 treats an empty section as "unlabelled" rather than as
a section named the empty string.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import chromadb
from chromadb.api.models.Collection import Collection

from src import config
from src.errors import IngestError
from src.ingest.embed import embed_texts
from src.logging_config import get_logger
from src.types import Chunk

logger = get_logger("ingest.store")

UPSERT_BATCH: Final = 64
SAMPLE_SIZE: Final = 3

_CLIENTS: dict[str, chromadb.ClientAPI] = {}


def _client(path: Path | None = None) -> chromadb.ClientAPI:
    """Return the persistent client for a store path, creating it once.

    Keyed by path rather than memoised globally, so pointing `CHROMA_PATH` at a
    temporary directory in a test yields an isolated store rather than the
    already-open one.
    """
    key = str(path or config.CHROMA_PATH)
    if key not in _CLIENTS:
        Path(key).mkdir(parents=True, exist_ok=True)
        _CLIENTS[key] = chromadb.PersistentClient(path=key)
    return _CLIENTS[key]


def get_collection(path: Path | None = None) -> Collection:
    """Return the shared collection, creating it in cosine space if absent.

    Cosine space is chosen so that `1 - distance` in Phase 4 is a real similarity
    in `[-1, 1]`, which is the range the tuned `SIMILARITY_FLOOR` is expressed in.

    `path` defaults to `config.CHROMA_PATH` and exists so a caller can memoise on
    the same path that produced the handle, the way `_client` already does.
    """
    client = _client(path)
    try:
        return client.get_or_create_collection(
            name=config.CHROMA_COLLECTION, metadata={"hnsw:space": config.CHROMA_SPACE}
        )
    except Exception as exc:
        raise IngestError(
            f"could not open the Chroma collection {config.CHROMA_COLLECTION!r} at "
            f"{path or config.CHROMA_PATH}: {type(exc).__name__}. Delete that directory and "
            "re-run `make ingest`."
        ) from exc


def collection_exists() -> bool:
    """True when the collection is already built. Never creates one."""
    try:
        _client().get_collection(config.CHROMA_COLLECTION)
    except Exception as exc:
        logger.debug("collection %s not found: %s", config.CHROMA_COLLECTION, type(exc).__name__)
        return False
    return True


def reset_collection() -> None:
    """Delete and recreate the collection. Required for idempotent re-ingestion."""
    client = _client()
    try:
        client.delete_collection(config.CHROMA_COLLECTION)
        logger.info("dropped collection %s", config.CHROMA_COLLECTION)
    except Exception as exc:
        logger.debug("nothing to drop (%s)", type(exc).__name__)
    get_collection()


def store_chunks(chunks: list[Chunk]) -> int:
    """Embed and upsert every chunk, returning the number of rows written.

    Upsert rather than add, because `chunk_id` is deterministic: re-running
    ingestion on an unchanged corpus rewrites the same ids and leaves the count
    alone instead of doubling it.
    """
    if not chunks:
        raise IngestError("store_chunks() was called with no chunks")

    collection = get_collection()
    vectors = embed_texts([chunk.text for chunk in chunks])

    for start in range(0, len(chunks), UPSERT_BATCH):
        window = chunks[start : start + UPSERT_BATCH]
        collection.upsert(
            ids=[chunk.chunk_id for chunk in window],
            documents=[chunk.text for chunk in window],
            embeddings=vectors[start : start + len(window)].tolist(),
            metadatas=[chunk.to_metadata() for chunk in window],
        )
        logger.info("stored %d/%d chunks", min(start + UPSERT_BATCH, len(chunks)), len(chunks))

    logger.info("collection now holds %d chunks", collection.count())
    return len(chunks)


def collection_stats() -> dict[str, Any]:
    """Summarise the built collection for the CLI report.

    Reads back real rows rather than trusting the write, so the numbers shown are
    the numbers Phase 4 will query. Returns `count=0` when ingest has not run.
    """
    if not collection_exists():
        return {
            "name": config.CHROMA_COLLECTION,
            "path": str(config.CHROMA_PATH),
            "count": 0,
            "built": False,
            "sample": [],
        }

    collection = get_collection()
    stored = collection.get(limit=SAMPLE_SIZE, include=["metadatas", "documents"])
    metadatas = stored.get("metadatas") or []
    documents = stored.get("documents") or []

    sample = [
        {
            "chunk_id": chunk_id,
            "scheme_id": metadatas[index].get("scheme_id", ""),
            "section": metadatas[index].get("section", ""),
            "source_url": metadatas[index].get("source_url", ""),
            "fetched_at": metadatas[index].get("fetched_at", ""),
            "text_prefix": (documents[index] or "").splitlines()[0][:60],
        }
        for index, chunk_id in enumerate(stored.get("ids") or [])
    ]

    return {
        "name": config.CHROMA_COLLECTION,
        "path": str(config.CHROMA_PATH),
        "count": collection.count(),
        "built": True,
        "sample": sample,
    }
