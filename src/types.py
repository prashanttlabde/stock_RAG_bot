"""Shared record types for the ingestion and query pipelines (architecture.md §5).

These dataclasses are the contract between phases: `load.py` produces
`SourceDoc`, `chunk.py` produces `Chunk`, `retriever.py` produces
`RetrievalHit`, `answer.py` produces `Answer`, and `chat_service.py` assembles
a `ChatReply` that is either an answer or a refusal, never both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SourceRef:
    """One entry of the corpus allowlist in `data/sources.yaml` (architecture.md §5.1).

    Carries no `fetched_at` or text: it is the citation contract that ingestion
    fills in and retrieval filters on.
    """

    scheme_id: str
    scheme_name: str
    category: str
    source_url: str


@dataclass(frozen=True, slots=True)
class SourceDoc:
    """One source document: a single scheme's cleaned page text (architecture.md §5.1)."""

    scheme_id: str
    scheme_name: str
    category: str
    source_url: str
    fetched_at: str
    file_path: Path | None = None
    raw_text: str = ""


@dataclass(frozen=True, slots=True)
class Chunk:
    """One indexable section of a `SourceDoc` (architecture.md §5.2)."""

    chunk_id: str
    scheme_id: str
    source_url: str
    section: str | None
    fetched_at: str
    text: str

    def to_metadata(self) -> dict[str, str]:
        """Flatten to Chroma metadata; Chroma rejects `None`, so section becomes `""`.

        `chunk_id` is stored too, not just used as the row id, so a retrieved row
        is self-describing: Phase 4 can rebuild a whole `Chunk` from metadata alone.
        """
        return {
            "chunk_id": self.chunk_id,
            "scheme_id": self.scheme_id,
            "source_url": self.source_url,
            "section": self.section or "",
            "fetched_at": self.fetched_at,
        }


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    """A retrieved chunk with its cosine similarity score (architecture.md §7 step 3)."""

    chunk: Chunk
    score: float

    @property
    def scheme_id(self) -> str:
        return self.chunk.scheme_id

    @property
    def source_url(self) -> str:
        return self.chunk.source_url

    @property
    def section(self) -> str | None:
        return self.chunk.section


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Outcome of a retrieval call, including what was dropped by the floor."""

    query: str
    hits: list[RetrievalHit]
    detected_scheme_id: str | None = None
    considered_count: int = 0
    floor: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.hits


@dataclass(frozen=True, slots=True)
class Answer:
    """A grounded, cited answer. `answer_text=None` routes to the no-answer path."""

    answer_text: str | None
    source_url: str | None
    fetched_at: str | None
    scheme_id: str | None = None
    scheme_name: str | None = None
    section: str | None = None
    used_chunks: list[Chunk] = field(default_factory=list)
    ambiguous_source: bool = False
    truncated: bool = False
    generation_mode: str = "template"


@dataclass(frozen=True, slots=True)
class Refusal:
    """A user-safe refusal. `link_url` may be an educational link outside the corpus."""

    kind: str
    message: str
    link_url: str | None = None
    link_label: str | None = None


@dataclass(frozen=True, slots=True)
class ChatReply:
    """One chat turn: exactly one of `answer` or `refusal` is populated.

    Carries the retrieved hits so the UI can render the optional debug panel
    (architecture.md §14). This object is never persisted (INV-3).
    """

    text: str
    answer: Answer | None = None
    refusal: Refusal | None = None
    retrieved: list[RetrievalHit] = field(default_factory=list)
    decision_kind: str | None = None
    latency_ms: float | None = None

    @property
    def is_refusal(self) -> bool:
        return self.refusal is not None


@dataclass(frozen=True, slots=True)
class IngestReport:
    """Summary returned by `src.ingest.pipeline.run_ingest` (architecture.md §6)."""

    docs: int
    chunks: int
    collection_count: int
    stage_timings: dict[str, float] = field(default_factory=dict)
