"""Phase 3 store and embedding tests.

Everything here runs against a temporary Chroma path, so the real
`data/chroma/` built by `make ingest` is never touched. The embedding model is
downloaded on first use, so the whole module skips cleanly when it is
unavailable rather than failing `make test` on a machine with no cache.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src import config
from src.errors import IngestError
from src.ingest import store as store_module
from src.ingest.chunk import chunk_corpus
from src.ingest.embed import _as_matrix, _get_model, embed_query, embed_texts
from src.ingest.load import load_corpus
from src.ingest.store import (
    collection_exists,
    collection_stats,
    get_collection,
    reset_collection,
    store_chunks,
)
from src.types import Chunk


def _model_or_skip() -> None:
    try:
        _get_model()
    except IngestError as exc:
        pytest.skip(f"embedding model unavailable: {exc}")


@pytest.fixture(scope="module")
def isolated_store(tmp_path_factory) -> Path:
    """Point the store at a throwaway directory with its own collection name."""
    _model_or_skip()
    path = tmp_path_factory.mktemp("chroma")
    patch = pytest.MonkeyPatch()
    patch.setattr(config, "CHROMA_PATH", path)
    patch.setattr(config, "CHROMA_COLLECTION", "test_mf_faq")
    store_module._CLIENTS.clear()
    yield path
    store_module._CLIENTS.clear()
    patch.undo()


@pytest.fixture(scope="module")
def corpus_chunks() -> list[Chunk]:
    return chunk_corpus(load_corpus())


@pytest.fixture
def empty_store(isolated_store: Path) -> None:
    """Reset before each test that asserts on an exact count."""
    reset_collection()
    assert get_collection().count() == 0


def _unlabelled_chunk() -> Chunk:
    return Chunk(
        chunk_id="hdfc-test::preamble::001",
        scheme_id="hdfc-test",
        source_url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        section=None,
        fetched_at="2026-09-27",
        text="Some unlabelled body text long enough to survive the minimum length filter.",
    )


class TestEmbeddings:
    def test_embed_texts_returns_a_unit_length_matrix(self) -> None:
        _model_or_skip()
        matrix = embed_texts(["exit load of 1 percent", "expense ratio is 1.03 percent"])
        assert matrix.shape == (2, config.EMBEDDING_DIM)
        assert matrix.dtype == np.float32
        assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-5)

    def test_embed_query_matches_embed_texts_for_the_same_string(self) -> None:
        _model_or_skip()
        text = "what is the exit load"
        assert np.allclose(embed_query(text), embed_texts([text]), atol=1e-6)

    def test_batching_does_not_change_the_vectors(self) -> None:
        _model_or_skip()
        texts = [f"scheme fact number {n}" for n in range(5)]
        assert np.allclose(embed_texts(texts, batch_size=5), embed_texts(texts, batch_size=2), atol=1e-5)

    def test_embed_texts_rejects_an_empty_batch(self) -> None:
        with pytest.raises(IngestError, match="no texts"):
            embed_texts([])

    def test_embed_query_rejects_an_empty_string(self) -> None:
        with pytest.raises(IngestError, match="empty query"):
            embed_query("   ")

    def test_as_matrix_rejects_a_wrong_shape(self) -> None:
        with pytest.raises(IngestError, match="expected embeddings"):
            _as_matrix(np.zeros((2, 17)), 2)
        with pytest.raises(IngestError, match="expected embeddings"):
            _as_matrix(np.zeros(384), 1)


class TestStore:
    def test_stores_every_chunk_and_reports_the_count(
        self, empty_store: None, corpus_chunks: list[Chunk]
    ) -> None:
        assert store_chunks(corpus_chunks) == 30
        assert get_collection().count() == 30

    def test_collection_uses_cosine_space(self, isolated_store: Path) -> None:
        reset_collection()
        assert get_collection().metadata.get("hnsw:space") == config.CHROMA_SPACE

    def test_all_metadata_values_are_non_none(
        self, empty_store: None, corpus_chunks: list[Chunk]
    ) -> None:
        store_chunks(corpus_chunks)
        stored = get_collection().get(include=["metadatas"])
        assert stored["metadatas"]
        for metadata in stored["metadatas"]:
            # `chunk_id` was added in Phase 4 so a retrieved row is self-describing:
            # the retriever rebuilds a whole Chunk from metadata alone.
            assert set(metadata) == {
                "chunk_id",
                "scheme_id",
                "source_url",
                "section",
                "fetched_at",
            }
            assert all(value is not None for value in metadata.values())
            assert metadata["source_url"].startswith("https://groww.in/")

    def test_stored_chunk_id_matches_the_row_id(
        self, empty_store: None, corpus_chunks: list[Chunk]
    ) -> None:
        store_chunks(corpus_chunks)
        stored = get_collection().get(include=["metadatas"])
        for row_id, metadata in zip(stored["ids"], stored["metadatas"], strict=True):
            assert metadata["chunk_id"] == row_id

    def test_a_missing_section_is_stored_as_an_empty_string(self, empty_store: None) -> None:
        store_chunks([_unlabelled_chunk()])
        stored = get_collection().get(include=["metadatas", "documents"])
        assert stored["metadatas"][0]["section"] == ""
        assert stored["documents"][0].startswith("Some unlabelled body text")

    def test_every_stored_row_is_readable_and_unique(
        self, empty_store: None, corpus_chunks: list[Chunk]
    ) -> None:
        store_chunks(corpus_chunks)
        stored = get_collection().get(include=["metadatas", "documents"])
        assert len(set(stored["ids"])) == len(stored["ids"]) == 30
        assert all(document.strip() for document in stored["documents"])

    def test_re_storing_the_same_chunks_does_not_duplicate(
        self, empty_store: None, corpus_chunks: list[Chunk]
    ) -> None:
        store_chunks(corpus_chunks)
        store_chunks(corpus_chunks)
        assert get_collection().count() == 30

    def test_the_store_survives_a_fresh_client(
        self, empty_store: None, corpus_chunks: list[Chunk]
    ) -> None:
        store_chunks(corpus_chunks)
        store_module._CLIENTS.clear()
        assert get_collection().count() == 30
        assert collection_exists()

    def test_store_chunks_rejects_an_empty_list(self, empty_store: None) -> None:
        with pytest.raises(IngestError, match="no chunks"):
            store_chunks([])

    def test_a_raw_query_for_exit_load_finds_the_exit_load_section(
        self, empty_store: None, corpus_chunks: list[Chunk]
    ) -> None:
        store_chunks(corpus_chunks)
        result = get_collection().query(
            query_texts=["what is the exit load"],
            n_results=3,
            include=["metadatas", "distances", "documents"],
        )
        top = result["metadatas"][0][0]
        assert top["scheme_id"].startswith("hdfc-")
        assert "exit load" in top["section"].lower()
        assert 1.0 - result["distances"][0][0] > config.SIMILARITY_FLOOR
        assert "exit load" in result["documents"][0][0].lower()

    def test_a_scheme_metadata_filter_narrows_the_search(
        self, empty_store: None, corpus_chunks: list[Chunk]
    ) -> None:
        store_chunks(corpus_chunks)
        result = get_collection().query(
            query_texts=["expense ratio"],
            n_results=5,
            where={"scheme_id": "hdfc-elss-tax-saver-direct-growth"},
            include=["metadatas"],
        )
        assert result["metadatas"][0]
        assert {
            metadata["scheme_id"] for metadata in result["metadatas"][0]
        } == {"hdfc-elss-tax-saver-direct-growth"}


class TestCollectionStats:
    def test_stats_report_an_unbuilt_collection_as_empty(self, isolated_store: Path) -> None:
        store_module._client().delete_collection(config.CHROMA_COLLECTION)
        store_module._CLIENTS.clear()
        assert not collection_exists()
        stats = collection_stats()
        assert stats["count"] == 0
        assert stats["built"] is False
        assert stats["sample"] == []

    def test_stats_read_real_rows_back(self, empty_store: None, corpus_chunks: list[Chunk]) -> None:
        store_chunks(corpus_chunks)
        stats = collection_stats()
        assert stats["built"] is True
        assert stats["count"] == 30
        assert stats["name"] == config.CHROMA_COLLECTION
        assert len(stats["sample"]) == 3
        for row in stats["sample"]:
            assert set(row) == {
                "chunk_id",
                "scheme_id",
                "section",
                "source_url",
                "fetched_at",
                "text_prefix",
            }
            assert row["scheme_id"].startswith("hdfc-")
            assert row["fetched_at"] == "2026-09-27"
            assert row["text_prefix"]
