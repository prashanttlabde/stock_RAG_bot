"""Chat service orchestration: the one function the UI calls (architecture.md §7, §11).

`handle_message` is the entire runtime sequence for a single turn -- normalize,
guard, retrieve, generate, format -- assembled in exactly one place. That is what
makes two invariants provable rather than merely intended:

* **INV-4** -- a refusal from `check_query` returns immediately, before `retrieve`
  is ever called, so advice/returns/grievance/PII text never reaches the vector
  store or the generator.
* **INV-3** -- nothing here writes to disk. `ChatTurn` is a transient, in-memory-only
  debug record for the instructor demo (architecture.md §14); the ring buffer this
  module keeps dies with the process, and the except block never logs the raw
  question, so a PII-flagged message that somehow reaches an exception path still
  never touches a log line.

Every domain exception (`CorpusError`, `IngestError`, `RetrievalError`,
`GenerationError`, `PolicyRefusal`) is caught here and turned into a friendly
`Refusal` instead of a stack trace, so the UI never has to know these types exist.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass

from src import config
from src.errors import ChatbotError, RetrievalError
from src.generate.answer import answer_question
from src.generate.no_answer import no_answer_message
from src.guards.copy import DISCLAIMER
from src.guards.policy import check_query
from src.logging_config import get_logger
from src.retrieve.retriever import retrieve
from src.types import Answer, ChatReply, Refusal, RetrievalHit

logger = get_logger("chat")

# One expense-ratio, one exit-load/SIP, one ELSS lock-in question -- the three
# buttons architecture.md §9 asks the UI to render. All three are verified against
# the real store in tests/test_chat_service.py.
EXAMPLE_QUESTIONS: list[str] = [
    "What is the expense ratio of HDFC Small Cap?",
    "What is the exit load of HDFC Large Cap?",
    "What is the lock in period for ELSS?",
]

# A broader spread across schemes and fact types for Phase 9's samples file.
SAMPLE_QUESTIONS: list[str] = [
    "What is the expense ratio of HDFC Large Cap?",
    "What is the exit load of HDFC Small Cap?",
    "What is the minimum SIP amount for HDFC Balanced Advantage?",
    "What is the lock in period for ELSS?",
    "What is the benchmark for HDFC Flexi Cap?",
    "What is the risk level of HDFC Large Cap?",
    "What is the minimum SIP amount?",
    "What is the exit load of HDFC ELSS Tax Saver?",
]

_EMPTY_MESSAGE = "Please type a question about a scheme fact."
_SERVICE_ERROR_MESSAGE = (
    "Something went wrong answering that. Please rephrase your question, or try "
    "again in a moment."
)
_RETRIEVAL_ERROR_MESSAGE = (
    "The search index isn't available right now. If you're running this locally, "
    "run `make ingest` to build it, then try again."
)

# How many recent turns the in-memory debug ring buffer keeps. A demo session never
# needs more than this, and an unbounded buffer would be the one way this module
# could grow without limit for the life of a process.
_MAX_RECENT_TURNS = 50


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """One turn's full detail, for the demo debug panel only. Never persisted.

    Carries the raw `question` (unlike `ChatReply`, which only carries the reply
    text) because the debug panel is for the person who just typed it, in their own
    live session -- the same text their own chat bubble already shows. It is never
    logged and never written to disk.
    """

    request_id: str
    question: str
    retrieved: list[RetrievalHit]
    answer: Answer | None
    refusal: Refusal | None
    decision_kind: str | None
    latency_ms: float


_recent_turns: deque[ChatTurn] = deque(maxlen=_MAX_RECENT_TURNS)


def recent_turns() -> list[ChatTurn]:
    """The most recent turns, oldest first, for the UI's optional debug panel.

    Backed by an in-memory ring buffer only -- nothing here is ever written to disk,
    and the buffer is empty again the next time the process starts (INV-3).
    """
    return list(_recent_turns)


def _normalize(question: str) -> str:
    """Strip, collapse internal whitespace, and cap at `config.MAX_QUERY_CHARS`."""
    return " ".join(question.split()).strip()[: config.MAX_QUERY_CHARS]


def format_reply(answer: Answer) -> str:
    """Assemble the visible text for a factual answer. The only function that does.

    Citation and freshness stay on their own lines (not folded into the answer
    prose) so the UI can style each one separately -- a clickable link, a muted
    caption -- without re-parsing the body.
    """
    lines = [answer.answer_text or "", f"Source: {answer.source_url}"]
    if answer.fetched_at:
        lines.append(f"Last updated from sources: {answer.fetched_at}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _debug_suffix(hits: list[RetrievalHit]) -> str:
    """Retrieved scheme/section/score lines, appended only when `SHOW_RETRIEVAL` is set.

    This is the instructor-demo view architecture.md §14 asks for: proof of what was
    actually retrieved, alongside the answer it produced.
    """
    if not config.SHOW_RETRIEVAL or not hits:
        return ""
    rows = "\n".join(
        f"  [{hit.scheme_id}] {hit.section or '-'} (score={hit.score:.3f})" for hit in hits
    )
    return f"\n\n--- retrieved (debug) ---\n{rows}"


def _record_turn(
    request_id: str,
    question: str,
    retrieved: list[RetrievalHit],
    answer: Answer | None,
    refusal: Refusal | None,
    decision_kind: str | None,
    latency_ms: float,
) -> None:
    _recent_turns.append(
        ChatTurn(
            request_id=request_id,
            question=question,
            retrieved=retrieved,
            answer=answer,
            refusal=refusal,
            decision_kind=decision_kind,
            latency_ms=latency_ms,
        )
    )


def _refusal_reply(
    request_id: str,
    question: str,
    kind: str,
    message: str,
    latency_ms: float,
    link_url: str | None = None,
    link_label: str | None = None,
) -> ChatReply:
    refusal = Refusal(kind=kind, message=message, link_url=link_url, link_label=link_label)
    _record_turn(request_id, question, [], None, refusal, kind, latency_ms)
    return ChatReply(
        text=refusal.message,
        answer=None,
        refusal=refusal,
        retrieved=[],
        decision_kind=kind,
        latency_ms=latency_ms,
    )


def handle_message(question: str) -> ChatReply:
    """Run one chat turn: guard -> retrieve -> generate -> format.

    A refused or empty message returns before any retrieval happens (INV-4). Any
    domain exception is caught and rendered as a friendly refusal; the except block
    logs only the exception's type, never `question` or `cleaned`, so a message
    that somehow reaches an exception path after passing the guard is still never
    logged (INV-3).
    """
    request_id = uuid.uuid4().hex[:12]
    t_start = time.perf_counter()
    cleaned = _normalize(question)

    if not cleaned:
        latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return _refusal_reply(request_id, question, "empty", _EMPTY_MESSAGE, latency_ms)

    try:
        t0 = time.perf_counter()
        decision = check_query(cleaned)
        logger.info(
            "chat: stage=guard request_id=%s latency_ms=%.1f",
            request_id,
            (time.perf_counter() - t0) * 1000,
        )

        if not decision.allowed:
            latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
            logger.info(
                "chat: stage=total request_id=%s kind=%s latency_ms=%.1f",
                request_id,
                decision.kind,
                latency_ms,
            )
            return _refusal_reply(
                request_id,
                question,
                decision.kind or "unknown",
                decision.message or _SERVICE_ERROR_MESSAGE,
                latency_ms,
                link_url=decision.link_url,
                link_label=decision.link_label,
            )

        t0 = time.perf_counter()
        result = retrieve(cleaned)
        logger.info(
            "chat: stage=retrieve request_id=%s hits=%d latency_ms=%.1f",
            request_id,
            len(result.hits),
            (time.perf_counter() - t0) * 1000,
        )

        if result.is_empty:
            answer = Answer(
                answer_text=None,
                source_url=None,
                fetched_at=None,
                scheme_id=result.detected_scheme_id,
                generation_mode=config.GENERATION_MODE,
            )
            text = no_answer_message(result.hits, result.detected_scheme_id)
            decision_kind = "no_answer"
        else:
            t0 = time.perf_counter()
            answer = answer_question(cleaned, result.hits)
            logger.info(
                "chat: stage=generate request_id=%s latency_ms=%.1f",
                request_id,
                (time.perf_counter() - t0) * 1000,
            )

            if answer.answer_text and answer.source_url:
                text = format_reply(answer) + _debug_suffix(result.hits)
                decision_kind = "answer"
            else:
                if answer.answer_text and not answer.source_url:
                    # Generation is contracted to always cite when it answers
                    # (INV-1). A break in that contract must fail closed, as a
                    # no-answer, rather than ever emit an uncited fact.
                    logger.warning(
                        "chat: request_id=%s got an uncited answer_text; "
                        "routing to no-answer instead of violating INV-1",
                        request_id,
                    )
                    answer = Answer(
                        answer_text=None,
                        source_url=None,
                        fetched_at=None,
                        scheme_id=answer.scheme_id,
                        scheme_name=answer.scheme_name,
                        section=answer.section,
                        generation_mode=answer.generation_mode,
                    )
                text = no_answer_message(result.hits, result.detected_scheme_id)
                decision_kind = "no_answer"

        if decision_kind == "no_answer":
            text += _debug_suffix(result.hits)

        latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
        logger.info(
            "chat: stage=total request_id=%s kind=%s latency_ms=%.1f",
            request_id,
            decision_kind,
            latency_ms,
        )
        _record_turn(request_id, cleaned, result.hits, answer, None, decision_kind, latency_ms)
        return ChatReply(
            text=text,
            answer=answer,
            refusal=None,
            retrieved=result.hits,
            decision_kind=decision_kind,
            latency_ms=latency_ms,
        )
    except RetrievalError as exc:
        logger.warning("chat: request_id=%s retrieval failed (%s)", request_id, type(exc).__name__)
        latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return _refusal_reply(
            request_id, question, "retrieval_error", _RETRIEVAL_ERROR_MESSAGE, latency_ms
        )
    except ChatbotError as exc:
        logger.warning("chat: request_id=%s failed (%s)", request_id, type(exc).__name__)
        latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return _refusal_reply(request_id, question, "error", _SERVICE_ERROR_MESSAGE, latency_ms)
