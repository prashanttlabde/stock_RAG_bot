"""Ingestion stage 4: embeddings (architecture.md §6 step 4).

Turns chunk text into 384-dimensional MiniLM vectors. The model is loaded once
per process and cached, because loading MiniLM costs about a second and the
Streamlit session in Phase 8 will ask for embeddings on every turn.

Both `embed_texts` and `embed_query` go through this module and both use
`normalize_embeddings=True`. That symmetry is not cosmetic: the store is created
with cosine space, and cosine similarity is only meaningful when stored and query
vectors are unit length and produced by the same model. An asymmetric pipeline
silently returns plausible-looking but wrong scores, which is exactly the kind of
bug that makes a retrieval demo look broken for reasons nobody can find.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

from src import config
from src.errors import IngestError
from src.logging_config import get_logger

logger = get_logger("ingest.embed")


def _model_dimension(model: SentenceTransformer) -> int:
    """Read the model's output width, supporting both method spellings.

    sentence-transformers 6 renamed `get_sentence_embedding_dimension` to
    `get_embedding_dimension`; `requirements.txt` allows 3.x, so both are tried.
    """
    getter = getattr(model, "get_embedding_dimension", None)
    if getter is None:
        getter = model.get_sentence_embedding_dimension
    return int(getter())


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    """Return the shared MiniLM model, loading it on first use.

    The first call downloads the weights to the local Hugging Face cache, which
    needs network access. Every later call in the same process is free, and no
    network is used at query time (INV-6).
    """
    logger.info("loading embedding model %s", config.EMBEDDING_MODEL)
    try:
        model = SentenceTransformer(config.EMBEDDING_MODEL)
    except Exception as exc:
        raise IngestError(
            f"could not load the embedding model {config.EMBEDDING_MODEL!r}: "
            f"{type(exc).__name__}. The first run downloads the weights and needs "
            "network access; later runs use the local Hugging Face cache."
        ) from exc

    dimension = _model_dimension(model)
    if dimension != config.EMBEDDING_DIM:
        raise IngestError(
            f"{config.EMBEDDING_MODEL} produces {dimension}-dimensional vectors but "
            f"config.EMBEDDING_DIM is {config.EMBEDDING_DIM}. The collection would be "
            "unqueryable; update the constant or pin a different model."
        )
    logger.info("embedding model ready (%s dimensions)", dimension)
    return model


def _as_matrix(vectors: np.ndarray, expected_rows: int) -> np.ndarray:
    """Validate the encoder's output shape and return a float32 matrix."""
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape != (expected_rows, config.EMBEDDING_DIM):
        raise IngestError(
            f"expected embeddings of shape ({expected_rows}, {config.EMBEDDING_DIM}), "
            f"got {matrix.shape}. The model and config disagree."
        )
    return matrix


def embed_texts(texts: Sequence[str], batch_size: int | None = None) -> np.ndarray:
    """Embed a batch of texts into a `(len(texts), EMBEDDING_DIM)` float32 matrix.

    Encoded in batches with a log line per batch, so a slow first run shows
    progress instead of appearing to hang.
    """
    if not texts:
        raise IngestError("embed_texts() was called with no texts")

    size = batch_size or config.EMBED_BATCH_SIZE
    model = _get_model()
    chunks: list[np.ndarray] = []
    total = len(texts)

    for start in range(0, total, size):
        window = list(texts[start : start + size])
        encoded = model.encode(window, normalize_embeddings=True, show_progress_bar=False)
        chunks.append(_as_matrix(encoded, len(window)))
        logger.info("embedded %d/%d texts", min(start + size, total), total)

    return np.vstack(chunks)


def embed_query(text: str) -> np.ndarray:
    """Embed one query string into a `(1, EMBEDDING_DIM)` float32 matrix.

    Same model and same normalization as `embed_texts`, by construction: both call
    `_get_model()` and pass `normalize_embeddings=True`. The 2-D shape is what
    Chroma's `query_embeddings` expects.
    """
    if not text.strip():
        raise IngestError("embed_query() was called with an empty query")
    return embed_texts([text], batch_size=1)
