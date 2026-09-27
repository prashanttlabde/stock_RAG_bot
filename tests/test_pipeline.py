"""Phase 3 orchestrator tests: stage order, timing, idempotency, and error naming.

`run_ingest` is the only thing `make ingest` calls, so these tests care about two
things above all: that the count it reports is the count actually in the store,
and that a failure in any stage says which stage broke.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import config
from src.errors import CorpusError, IngestError
from src.ingest import pipeline
from src.ingest import store as store_module
from src.ingest.embed import _get_model
from src.ingest.pipeline import run_ingest
from src.ingest.store import collection_exists, get_collection


def _model_or_skip() -> None:
    try:
        _get_model()
    except IngestError as exc:
        pytest.skip(f"embedding model unavailable: {exc}")


@pytest.fixture(scope="module")
def isolated_store(tmp_path_factory):
    """Build into a throwaway store so the real data/chroma/ is left alone."""
    _model_or_skip()
    path = tmp_path_factory.mktemp("pipeline-chroma")
    patch = pytest.MonkeyPatch()
    patch.setattr(config, "CHROMA_PATH", path)
    patch.setattr(config, "CHROMA_COLLECTION", "test_pipeline_faq")
    patch.setattr(config, "EMBEDDINGS_DUMP_FILE", path / "embeddings_dump.txt")
    store_module._CLIENTS.clear()
    yield path
    store_module._CLIENTS.clear()
    patch.undo()


def test_run_ingest_reports_docs_chunks_and_stored(isolated_store) -> None:
    report = run_ingest(rebuild=True)
    assert report.docs == 5
    assert report.chunks == 30
    assert report.collection_count == 30
    assert get_collection().count() == 30


def test_run_ingest_records_a_timing_for_every_stage(isolated_store) -> None:
    timings = run_ingest(rebuild=True).stage_timings
    assert set(timings) == {pipeline.STAGE_LOAD, pipeline.STAGE_CHUNK, pipeline.STAGE_STORE, pipeline.STAGE_STATS}
    assert all(seconds >= 0 for seconds in timings.values())


def test_run_ingest_is_idempotent_across_rebuilds(isolated_store) -> None:
    first = run_ingest(rebuild=True)
    second = run_ingest(rebuild=True)
    assert first.collection_count == second.collection_count == 30
    assert get_collection().count() == 30


def test_the_upsert_path_also_leaves_the_count_alone(isolated_store) -> None:
    run_ingest(rebuild=True)
    assert run_ingest(rebuild=False).collection_count == 30


def test_upsert_without_a_collection_points_at_make_ingest(isolated_store) -> None:
    store_module._client().delete_collection(config.CHROMA_COLLECTION)
    store_module._CLIENTS.clear()
    assert not collection_exists()
    with pytest.raises(IngestError, match="make ingest"):
        run_ingest(rebuild=False)


def test_a_stage_failure_names_the_stage(isolated_store, monkeypatch) -> None:
    def boom(_docs: object) -> None:
        raise RuntimeError("chunker exploded")

    monkeypatch.setattr(pipeline, "chunk_corpus", boom)
    with pytest.raises(IngestError, match="stage 'chunk' failed"):
        run_ingest(rebuild=True)


def test_a_domain_error_keeps_its_own_message(isolated_store, monkeypatch) -> None:
    def boom() -> None:
        raise CorpusError("corpus directory is missing")

    monkeypatch.setattr(pipeline, "load_corpus", boom)
    with pytest.raises(CorpusError, match="corpus directory is missing"):
        run_ingest(rebuild=True)


def test_a_store_that_lost_rows_is_reported(isolated_store, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "store_chunks", lambda _chunks: 30)
    with pytest.raises(IngestError, match="out of sync"):
        run_ingest(rebuild=True)


class TestCli:
    def test_default_run_reports_the_counts(self, isolated_store, capsys) -> None:
        assert pipeline.main(["--rebuild"]) == 0
        out = capsys.readouterr().out
        assert "chunks          : 30" in out
        assert "stored          : 30" in out

    def test_bare_invocation_upserts(self, isolated_store, capsys) -> None:
        run_ingest(rebuild=True)
        assert pipeline.main([]) == 0
        assert "stored          : 30" in capsys.readouterr().out

    def test_stats_flag_reports_without_rebuilding(self, isolated_store, capsys) -> None:
        run_ingest(rebuild=True)
        assert pipeline.main(["--stats"]) == 0
        out = capsys.readouterr().out
        assert "chunks     : 30" in out

    def test_stats_flag_explains_a_missing_store(self, isolated_store, capsys) -> None:
        store_module._client().delete_collection(config.CHROMA_COLLECTION)
        store_module._CLIENTS.clear()
        assert pipeline.main(["--stats"]) == 0
        assert "Run `make ingest`" in capsys.readouterr().out

    def test_a_failed_stage_exits_non_zero(self, isolated_store, monkeypatch, capsys) -> None:
        def boom(_docs: object) -> None:
            raise RuntimeError("chunker exploded")

        monkeypatch.setattr(pipeline, "chunk_corpus", boom)
        assert pipeline.main(["--rebuild"]) == 1
        assert "ERROR" in capsys.readouterr().out

    def test_rebuild_and_stats_are_mutually_exclusive(self, isolated_store) -> None:
        with pytest.raises(SystemExit):
            pipeline.main(["--rebuild", "--stats"])


class TestEmbeddingDump:
    def test_it_writes_a_readable_dump(self, isolated_store, tmp_path: Path) -> None:
        run_ingest(rebuild=True)
        target = pipeline.dump_embeddings(tmp_path / "dump.txt")
        text = target.read_text(encoding="utf-8")
        assert f"dimensions   : {config.EMBEDDING_DIM}" in text
        assert "space        : cosine" in text
        for heading in ("SECTION 1", "SECTION 2", "SECTION 3", "SECTION 4"):
            assert heading in text
        assert text.count("[") >= 30

    def test_every_stored_chunk_appears_with_its_384_floats(self, isolated_store, tmp_path: Path) -> None:
        run_ingest(rebuild=True)
        text = pipeline.dump_embeddings(tmp_path / "dump.txt").read_text(encoding="utf-8")
        for chunk_id in get_collection().get(include=[])["ids"]:
            assert chunk_id in text
        # Section 1 must contain 30 blocks of exactly 384 floats, 12 per row.
        block = text.split("SECTION 2")[0]
        for chunk_block in block.split("[")[2:]:
            floats = [word for word in chunk_block.split() if word[0] in "+-"]
            assert len(floats) == config.EMBEDDING_DIM

    def test_scores_are_similarity_not_chroma_distance(self, isolated_store, tmp_path: Path) -> None:
        run_ingest(rebuild=True)
        text = pipeline.dump_embeddings(tmp_path / "dump.txt").read_text(encoding="utf-8")
        assert "SIMILARITY (higher" in text
        # A byte-identical pair must score 1.000 as a similarity; as a Chroma
        # distance it would read 0.000. This pins the label to the arithmetic.
        assert "  1.000  " in text
        assert "  0.000  " not in text

    def test_it_reports_how_many_chunks_are_duplicates(self, isolated_store, tmp_path: Path) -> None:
        run_ingest(rebuild=True)
        text = pipeline.dump_embeddings(tmp_path / "dump.txt").read_text(encoding="utf-8")
        assert "hold only 19 distinct texts" in text
        assert "byte-identical" in text
        assert "Understand terms" in text

    def test_it_explains_an_empty_store(self, isolated_store, tmp_path: Path) -> None:
        store_module._client().delete_collection(config.CHROMA_COLLECTION)
        store_module._CLIENTS.clear()
        with pytest.raises(IngestError, match="make ingest"):
            pipeline.dump_embeddings(tmp_path / "dump.txt")

    def test_the_cli_writes_the_default_path(self, isolated_store, capsys) -> None:
        run_ingest(rebuild=True)
        assert pipeline.main(["--dump-embeddings"]) == 0
        assert "embeddings" in capsys.readouterr().out
        assert config.EMBEDDINGS_DUMP_FILE.exists()
