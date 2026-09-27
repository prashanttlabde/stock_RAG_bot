"""Phase 0 scaffolding tests: config, shared types, errors, and logging."""

from __future__ import annotations

import dataclasses

import pytest

from src import config, errors, types
from src.logging_config import get_logger, setup_logging


def test_config_exposes_pipeline_constants() -> None:
    assert config.EMBEDDING_MODEL == "sentence-transformers/all-MiniLM-L6-v2"
    assert config.EMBEDDING_DIM == 384
    assert config.CHROMA_COLLECTION == "mf_faq_hdfc"
    assert config.TOP_K >= 3
    assert 0.0 < config.SIMILARITY_FLOOR < 1.0
    assert config.CHUNK_TARGET_TOKENS > 0
    assert 0.0 <= config.CHUNK_OVERLAP_RATIO < 1.0
    assert config.MAX_ANSWER_SENTENCES == 3


def test_config_paths_are_absolute_and_scoped() -> None:
    for path in (config.DATA_DIR, config.CORPUS_DIR, config.CHROMA_PATH, config.SOURCES_FILE):
        assert path.is_absolute()
    assert config.CORPUS_DIR.parent == config.DATA_DIR
    assert config.CHROMA_PATH.parent == config.DATA_DIR


def test_generation_mode_defaults_to_offline_template() -> None:
    assert config.GENERATION_MODE in {"template", "llm"}


def test_every_record_type_is_a_dataclass() -> None:
    for cls in (
        types.SourceDoc,
        types.Chunk,
        types.RetrievalHit,
        types.RetrievalResult,
        types.Answer,
        types.Refusal,
        types.ChatReply,
        types.IngestReport,
    ):
        assert dataclasses.is_dataclass(cls)


def test_chunk_metadata_never_contains_none() -> None:
    chunk = types.Chunk(
        chunk_id="scheme::exit-load::001",
        scheme_id="scheme",
        source_url="https://groww.in/x",
        section=None,
        fetched_at="2026-09-27",
        text="Exit load is 1% within 12 months.",
    )
    metadata = chunk.to_metadata()
    assert metadata["section"] == ""
    assert all(value is not None for value in metadata.values())
    assert set(metadata) == {"chunk_id", "scheme_id", "source_url", "section", "fetched_at"}
    assert metadata["chunk_id"] == chunk.chunk_id


def test_retrieval_hit_proxies_its_chunk() -> None:
    chunk = types.Chunk("c", "scheme", "https://groww.in/x", "Exit load", "2026-09-27", "text")
    hit = types.RetrievalHit(chunk=chunk, score=0.42)
    assert hit.scheme_id == "scheme"
    assert hit.source_url == "https://groww.in/x"
    assert hit.section == "Exit load"
    assert hit.score == pytest.approx(0.42)


def test_chat_reply_is_answer_xor_refusal() -> None:
    empty = types.ChatReply(text="no hits")
    assert empty.answer is None
    assert empty.refusal is None
    assert not empty.is_refusal

    refusal = types.ChatReply(
        text="facts-only", refusal=types.Refusal(kind="advice", message="facts-only")
    )
    assert refusal.is_refusal
    assert refusal.answer is None


def test_error_hierarchy_and_messages() -> None:
    for exc_cls in (
        errors.CorpusError,
        errors.IngestError,
        errors.RetrievalError,
        errors.GenerationError,
        errors.PolicyRefusal,
    ):
        assert issubclass(exc_cls, errors.ChatbotError)

    refusal = errors.PolicyRefusal(kind="pii", message="I can't help with that.")
    assert refusal.kind == "pii"
    assert str(refusal) == "I can't help with that."


def test_logging_setup_is_idempotent() -> None:
    first = setup_logging("INFO")
    root_handlers = len(__import__("logging").getLogger().handlers)
    second = setup_logging("INFO")
    assert len(__import__("logging").getLogger().handlers) == root_handlers
    assert first is second
    assert get_logger("phase0").name == "mf_rag.phase0"
