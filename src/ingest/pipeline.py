"""Ingestion orchestrator: corpus -> chunks -> embeddings -> store (architecture.md §6).

This is what `make ingest` calls. It runs the four offline stages in order,
times each one, and reports a single `IngestReport`. Any stage failure is
re-raised as an `IngestError` naming the stage that broke, so a broken ingest
says which of Load / Chunk / Embed / Store failed instead of surfacing a
sentence-transformers traceback from the middle of the pipeline.

The first run downloads the MiniLM weights, so `make ingest` needs network. Every
stage after that is offline, which is what INV-6 requires of the demo.
"""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import TypeVar

from src import config
from src.errors import ChatbotError, IngestError
from src.ingest.chunk import chunk_corpus
from src.ingest.load import load_corpus
from src.ingest.store import (
    collection_exists,
    collection_stats,
    get_collection,
    reset_collection,
    store_chunks,
)
from src.logging_config import get_logger
from src.types import Chunk, IngestReport, SourceDoc

logger = get_logger("ingest.pipeline")

STAGE_LOAD = "load"
STAGE_CHUNK = "chunk"
STAGE_STORE = "store"
STAGE_STATS = "stats"

_T = TypeVar("_T")


def _run_stage(
    name: str, timings: dict[str, float], func: Callable[..., _T], *args: object
) -> _T:
    """Run one ingestion stage, recording its wall-clock time.

    Domain exceptions keep their own message, since they are already
    user-presentable. Anything else is wrapped in an `IngestError` that names the
    stage, because an unhandled model or Chroma error is not.
    """
    logger.info("stage %s: start", name)
    start = perf_counter()
    try:
        result = func(*args)
    except ChatbotError:
        raise
    except Exception as exc:
        raise IngestError(f"stage '{name}' failed: {type(exc).__name__}: {exc}") from exc
    timings[name] = round(perf_counter() - start, 3)
    logger.info("stage %s: done in %ss", name, timings[name])
    return result


def run_ingest(rebuild: bool = True) -> IngestReport:
    """Load the corpus, chunk it, embed it, and store it in Chroma.

    With `rebuild=True` the collection is dropped first, which is the honest
    rebuild and the one `make ingest` uses. With `rebuild=False` the chunks are
    upserted into the existing collection; because `chunk_id` is deterministic
    that is still idempotent for an unchanged corpus, but it will not remove
    chunks whose sections disappeared from a re-scrape.
    """
    timings: dict[str, float] = {}
    logger.info(
        "ingest starting (rebuild=%s, corpus=%s, model=%s)",
        rebuild,
        config.CORPUS_DIR,
        config.EMBEDDING_MODEL,
    )

    docs: list[SourceDoc] = _run_stage(STAGE_LOAD, timings, load_corpus)
    chunks: list[Chunk] = _run_stage(STAGE_CHUNK, timings, chunk_corpus, docs)

    if rebuild:
        _run_stage(STAGE_STORE, timings, reset_collection)
        written = _run_stage(STAGE_STORE, timings, store_chunks, chunks)
    else:
        if not collection_exists():
            raise IngestError(
                f"no collection at {config.CHROMA_PATH}. Run `make ingest` first, "
                "or re-run with --rebuild."
            )
        written = _run_stage(STAGE_STORE, timings, store_chunks, chunks)

    stats = _run_stage(STAGE_STATS, timings, collection_stats)

    report = IngestReport(
        docs=len(docs),
        chunks=len(chunks),
        collection_count=stats["count"],
        stage_timings=timings,
    )
    if written != len(chunks):
        raise IngestError(
            f"stage 'store' wrote {written} chunks but the chunker produced {len(chunks)}"
        )
    if report.collection_count != len(chunks):
        raise IngestError(
            f"collection holds {report.collection_count} chunks but {len(chunks)} were "
            "produced; the store is out of sync with the corpus"
        )

    logger.info(
        "ingest complete: %d docs -> %d chunks -> %d stored (%s)",
        report.docs,
        report.chunks,
        report.collection_count,
        ", ".join(f"{stage}={seconds}s" for stage, seconds in timings.items()),
    )
    return report


def _print_stats() -> None:
    stats = collection_stats()
    if not stats["built"]:
        print(f"\nNo collection at {stats['path']}.")
        print("Run `make ingest` to build it from data/corpus.")
        return

    print(f"\ncollection : {stats['name']}")
    print(f"path       : {stats['path']}")
    print(f"chunks     : {stats['count']}")
    print("\nsample rows read back from the store:")
    for row in stats["sample"]:
        print(f"  {row['chunk_id']}")
        print(f"    section={row['section']!r} fetched_at={row['fetched_at']}")
        print(f"    {row['text_prefix']}")


def _vector_lines(vector: list[float], per_line: int = 12) -> list[str]:
    """Wrap a vector into fixed-width rows so 384 floats stay eyeballable."""
    rows = []
    for start in range(0, len(vector), per_line):
        row = vector[start : start + per_line]
        rows.append("      " + " ".join(f"{value:+.5f}" for value in row))
    return rows


def dump_embeddings(path: Path | None = None) -> Path:
    """Write every stored embedding to a plain text file, read back from Chroma.

    The vectors are read out of the collection rather than recomputed from the
    corpus, so this file is evidence of what was actually persisted. Alongside the
    raw floats it prints each chunk's nearest neighbour by cosine similarity,
    which is the part worth checking by eye: sibling sections of one scheme should
    pair with each other, and unrelated sections should not.
    """
    import numpy as np

    target = path or config.EMBEDDINGS_DUMP_FILE
    collection = get_collection()
    stored = collection.get(include=["embeddings", "metadatas", "documents"])
    ids = stored["ids"]
    if not ids:
        raise IngestError(
            f"no vectors in {config.CHROMA_COLLECTION} at {config.CHROMA_PATH}. "
            "Run `make ingest` first."
        )

    matrix = np.array(stored["embeddings"], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    # Vectors are stored L2-normalised, so the dot product is the cosine similarity.
    similarity = matrix @ matrix.T
    documents = stored["documents"] or []

    # Groww renders the same boilerplate for every scheme, so several chunks are
    # byte-identical. Worth stating in the file: a similarity of 1.000 below is
    # then correct rather than a symptom of a broken encoder.
    body_hashes: dict[str, list[str]] = {}
    for chunk_id, document in zip(ids, documents, strict=True):
        body_hashes.setdefault(hashlib.md5((document or "").strip().encode()).hexdigest(), []).append(
            chunk_id
        )
    repeated = {key: value for key, value in body_hashes.items() if len(value) > 1}

    distinct_note = [
        f"NOTE: {len(ids)} chunks hold only {len(body_hashes)} distinct texts.",
    ]
    if repeated:
        distinct_note += [
            f"      {sum(len(value) for value in repeated.values())} chunks are byte-identical copies of",
            "      another chunk, because Groww renders the same boilerplate for every",
            "      scheme. A cosine similarity of 1.000 in section 3 is therefore",
            "      correct, not a bug. See the duplicate groups in section 4.",
        ]

    lines: list[str] = [
        "=" * 78,
        "EMBEDDING DUMP - human-readable view of the Chroma vector store",
        "=" * 78,
        f"collection   : {config.CHROMA_COLLECTION}",
        f"path         : {config.CHROMA_PATH}",
        f"model        : {config.EMBEDDING_MODEL}",
        f"dimensions   : {config.EMBEDDING_DIM}",
        f"space        : {config.CHROMA_SPACE}",
        f"chunks       : {len(ids)}",
        f"generated    : {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        "Every vector below was READ BACK from the store, not recomputed from the",
        "corpus. Floats are 384 x float32 in a 1536-byte blob in chroma.sqlite3;",
        f"all {len(ids)} vectors are L2-normalised to 1.0, so cosine similarity is just",
        "their dot product and cosine distance is 1 minus that. Chroma reports",
        "distance in its own query results; the scores below are SIMILARITY (higher",
        "is a closer match), so they are the complement of what `collection.query`",
        "returns.",
        "",
        *distinct_note,
        "",
        "SECTION 1 - THE VECTORS",
        "-" * 78,
    ]

    documents = stored["documents"] or []
    for position, (chunk_id, metadata, document) in enumerate(
        zip(ids, stored["metadatas"], documents, strict=True), start=1
    ):
        lines += [
            "",
            f"[{position:02d}/{len(ids)}] {chunk_id}",
            f"  scheme_id  : {metadata['scheme_id']}",
            f"  section    : {metadata['section']}",
            f"  source_url : {metadata['source_url']}",
            f"  fetched_at : {metadata['fetched_at']}",
            f"  dimension  : {matrix.shape[1]}   L2 norm: {norms[position - 1]:.6f}",
            f"  characters : {len(document)}",
            "  vector     :",
        ]
        lines += _vector_lines([float(v) for v in matrix[position - 1]])

    lines += [
        "",
        "=" * 78,
        "SECTION 2 - NEAREST NEIGHBOUR OF EACH CHUNK (cosine similarity)",
        "-" * 78,
        "This is the part to sanity-check. Sections of the same scheme that answer",
        "the same kind of question should land near each other.",
        "",
    ]
    for position, chunk_id in enumerate(ids):
        others = np.where(np.arange(len(ids)) == position, -2.0, similarity[position])
        best = int(np.argmax(others))
        lines.append(f"  {similarity[position][best]:.3f}  {chunk_id}")
        lines.append(f"        -> {ids[best]}")

    lines += [
        "",
        "=" * 78,
        "SECTION 3 - CLOSEST PAIRS ACROSS THE WHOLE COLLECTION (cosine similarity)",
        "-" * 78,
    ]
    pairs = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            pairs.append((float(similarity[i][j]), i, j))
    pairs.sort(reverse=True)
    for score, i, j in pairs[:10]:
        lines.append(f"  {score:.3f}  {ids[i]}")
        lines.append(f"        {ids[j]}")

    lines += [
        "",
        "=" * 78,
        "SECTION 4 - BYTE-IDENTICAL CHUNKS (why 1.000 appears above)",
        "-" * 78,
    ]
    if not repeated:
        lines += ["", "  none - every chunk has its own text."]
    for value in repeated.values():
        lines += ["", f"  x{len(value)} identical copies:"]
        lines += [f"    {chunk_id}" for chunk_id in value]

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("wrote %d vectors (%d dims) to %s", len(ids), matrix.shape[1], target)
    return target


def main(argv: list[str] | None = None) -> int:
    """CLI: `python -m src.ingest.pipeline [--rebuild | --stats | --dump-embeddings]`."""
    parser = argparse.ArgumentParser(description="Build the Chroma store from the corpus.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--rebuild", action="store_true", help="drop the collection and rebuild it")
    group.add_argument("--stats", action="store_true", help="report on the existing collection")
    group.add_argument(
        "--dump-embeddings",
        action="store_true",
        help="write every stored embedding to a readable text file",
    )
    args = parser.parse_args(argv)

    if args.stats:
        _print_stats()
        return 0

    if args.dump_embeddings:
        try:
            written = dump_embeddings()
        except IngestError as exc:
            print(f"\nERROR: {exc}", flush=True)
            return 1
        print(f"\nWrote {config.CHROMA_COLLECTION} embeddings to {written}")
        return 0

    try:
        report = run_ingest(rebuild=args.rebuild)
    except IngestError as exc:
        print(f"\nERROR: {exc}", flush=True)
        return 1

    print(f"\ndocs            : {report.docs}")
    print(f"chunks          : {report.chunks}")
    print(f"stored          : {report.collection_count}")
    print("stage timings   : " + ", ".join(f"{k}={v}s" for k, v in report.stage_timings.items()))
    print(f"\nVector store ready at {config.CHROMA_PATH}. Start the app with `make app`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
