"""Guardrails / policy layer: the deterministic gate that runs before retrieval.

Implements architecture.md §7 step 1 and §8, and enforces two invariants:

* **INV-4** — advice, returns, and grievance queries are refused *before* any vector
  search, so they never reach the generator.
* **INV-3** — a query flagged for PII is never logged, never persisted, and never
  echoed back. Only the *label* of what was detected is logged.

Everything here is pure and synchronous: no I/O, no model, no store. A refused turn
costs one regex pass, which is what makes it safe to run unconditionally as the first
step of the pipeline.

Evaluation order is PII -> advice -> returns -> grievance, and the order is load-bearing
in one place only: PII must win over everything, because a message that contains both a
PAN and "should I buy" must not be answered with the advice copy, which would invite the
user to keep typing the PAN. For the three intent kinds the order is documentation, not
safety: the plan's own example, "Which is the best performing ELSS?", is both an advice
and a returns question, and the advice-first order answers it as advice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.guards import copy
from src.logging_config import get_logger

logger = get_logger("guards")

# --- Kinds -------------------------------------------------------------------------

KIND_PII = "pii"
KIND_ADVICE = "advice"
KIND_RETURNS = "returns"
KIND_GRIEVANCE = "grievance"
KIND_OUT_OF_SCOPE = "out_of_scope"

GUARD_KINDS = (KIND_PII, KIND_ADVICE, KIND_RETURNS, KIND_GRIEVANCE, KIND_OUT_OF_SCOPE)

# `out_of_scope` is part of the vocabulary for Phase 7's no-answer path, but nothing in
# this module raises it. Phase 5 answers exactly one question -- is this message safe
# and in-scope to look up? -- and a nonsense question is answerable in the sense that
# matters: it is a legitimate lookup that will simply find nothing, which is Phase 6's
# `NOT_IN_CONTEXT` path rather than a refusal. Inventing an `out_of_scope` gate here
# would blur that line.


@dataclass(frozen=True, slots=True)
class GuardDecision:
    """The gate's verdict for one message.

    `allowed=True` means "this is a factual query; retrieve for it". `allowed=False`
    means "refuse and show `message`". `link_url` is populated only on a refusal and is
    only ever a non-corpus educational link, which is the one place a URL outside the
    5-URL allowlist may appear.
    """

    allowed: bool
    kind: str | None = None
    message: str | None = None
    link_url: str | None = None
    link_label: str | None = None

    def __post_init__(self) -> None:
        if self.allowed and self.kind is not None:
            raise ValueError(f"an allowed decision has no kind, got {self.kind!r}")
        if not self.allowed and self.kind not in GUARD_KINDS:
            raise ValueError(f"unknown refusal kind {self.kind!r}")
        if not self.allowed and not self.message:
            raise ValueError("a refusal must carry a message")


ALLOWED = GuardDecision(allowed=True)

# --- PII detection -----------------------------------------------------------------
# Ordered most-specific first, because a 12-digit Aadhaar also satisfies the looser
# phone pattern, and "aadhaar" is the more useful thing to log.
#
# `_detect_pii` returns *labels*, and nothing in this module has access to the matched
# text once the check is done, so there is no value available to leak even by mistake.
#
# Every pattern that matches is reported, not just the first, so a log line lists the
# full set of classes present. That means one number can yield two labels: in the
# worked example in implementation.md, a 12-digit Aadhaar written with spaces is also
# 10+ digits with separators, so it logs as both "aadhaar" and "phone". Over-reporting a
# class is the safe direction for a privacy log; silently dropping one would be the
# dangerous one.

_DATE_SHAPED = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}|\d{4}/\d{2}/\d{2}|\d{2}/\d{2}/\d{4}"
)

PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aadhaar", re.compile(r"\b[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}\b")),
    ("pan", re.compile(r"\b[A-Za-z]{5}\d{4}[A-Za-z]\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    (
        "otp",
        re.compile(r"\b(?:otp|one[ -]time password)\b\D{0,20}\b(\d{4,6})\b", re.IGNORECASE),
    ),
    (
        "account_number",
        re.compile(
            # `(?<!\d)\d{6,}(?!\d)` rather than `\b\d{6,}\b`: a real IFSC is four
            # letters followed by digits, so a word boundary before the digits never
            # matches and the plan's pattern misses every actual IFSC. A digit-lookaround
            # matches both a letter-prefixed IFSC and a bare all-digit account number.
            r"\b(?:account|a/c|acc no|acc\.? no\.?|ifsc|folio)\b\D{0,20}(?<!\d)\d{6,}(?!\d)",
            re.IGNORECASE,
        ),
    ),
    ("phone", re.compile(r"\+?\d[\d\s\-]{8,14}\d")),
)


def _is_phone_candidate(text: str) -> bool:
    """Whether a `phone`-pattern match is really a phone number.

    The plan's pattern `\\+?\\d[\\d\\s\\-]{8,14}\\d` also matches any ISO or DD-MM-YYYY
    date, because a date is 8 digits and 2 separators inside the character class. Left
    uncorrected, "What was the exit load as on 2026-09-27?" is refused as PII -- a false
    refusal on a legitimate question about a corpus that publishes `fetched_at` dates.
    A date is not PII, so a date-shaped match is discarded.
    """
    return not _DATE_SHAPED.fullmatch(text.strip())


def _detect_pii(text: str) -> list[str]:
    """Labels for every PII class present in `text`, most sensitive first.

    Returns labels only. The matched values are never returned, so no caller can log
    or echo them (INV-3).
    """
    labels: list[str] = []
    for label, pattern in PII_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        if label == "phone" and not _is_phone_candidate(match.group(0)):
            continue
        if label not in labels:
            labels.append(label)
    return labels


# --- Intent gates ------------------------------------------------------------------
# Named lists of regexes so each rule is reviewable and individually testable. Patterns
# are matched case-insensitively against a whitespace-collapsed, lowercased copy of the
# query. Every list is deliberately keyword-level: a learned classifier would be
# unauditable in a demo whose selling point is determinism.

ADVICE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bshould\s+i\s+(?:buy|sell|invest|switch|redeem)\b",
        r"\bwhich\s+(?:fund|scheme|mutual fund)\s+(?:should|is best|would)\b",
        r"\bbest\s+(?:fund|scheme|mutual fund|option|choice)\b",
        r"\bis\s+it\s+(?:a\s+)?good\s+(?:time|investment|idea)\b",
        r"\b(?:recommend|recommendation)\b",
        r"\bsuggest\s+(?:a|an|which|some)\b",
        r"\bsuitable\s+for\s+me\b",
        r"\bcan\s+i\s+(?:buy|sell|invest)\b",
        r"\bworth\s+(?:buying|investing)\b",
        r"\b(?:good|bad|better|best)\s+(?:time\s+)?to\s+(?:buy|sell|invest)\b",
    )
)

RETURNS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\breturns?\b",
        r"\bcagr\b",
        r"\bxirr\b",
        r"\bperform(?:ance|ances|ed|ing|ant)\b",
        r"\bhow\s+much\s+(?:did|has|do|have)\b.*\b(?:return|earn)\b",
        r"\bbest\s+perform(?:ing|er)\b",
        r"\brank(?:ing|ed)\b",
        r"\btop\s+perform(?:ing|er)\b",
        r"\b\d+\s*(?:year|yr)s?\s+return\b",
        r"\bsince\s+inception\s+return\b",
        r"\bcompare\b.*\b(?:return|performance)\b",
        r"\bwhich\s+(?:fund|scheme)\s+(?:performed|has performed|returns)\b",
    )
)

GRIEVANCE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bcomplain(?:t|ts|ing)?\b",
        r"\bgrievance\b",
        r"\bregulator\b",
        r"\bsebi\b",
        r"\brbi\b",
        r"\brefund\b.*\b(?:reversed|failed|pending|stuck)\b",
        r"\bmoney\b.*\b(?:stuck|missing|not\s+credited)\b",
        r"\bchargeback\b",
        r"\blegal\b",
    )
)

# Evaluation order, documented once and relied on by `check_query`.
INTENT_GATES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (KIND_ADVICE, ADVICE_PATTERNS),
    (KIND_RETURNS, RETURNS_PATTERNS),
    (KIND_GRIEVANCE, GRIEVANCE_PATTERNS),
)


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip()


def _detect_intent(text: str) -> str | None:
    """The first intent gate that matches, in the documented order."""
    for kind, patterns in INTENT_GATES:
        if any(pattern.search(text) for pattern in patterns):
            return kind
    return None


def _returns_refusal(text: str) -> GuardDecision:
    """Build the returns refusal, linking the named scheme's page when there is one.

    The plan asks for "the scheme page if one was detected, else the corpus allowlist
    index". Naming the scheme needs `detect_scheme_id`, which lives downstream in
    architecture.md §7 -- so it is imported lazily, inside this one branch, and any
    failure falls back to the no-scheme copy. That keeps the guard layer free of a hard
    dependency on the retrieval stack (and on chromadb) while still giving the user the
    most useful destination.

    The returned `link_url` is an allowlist URL, which is the one case where a
    `groww.in` link appears on a *refusal* rather than as a citation. It points at the
    factsheet, which is exactly what the copy tells the user to go and read.
    """
    fallback = GuardDecision(
        allowed=False,
        kind=KIND_RETURNS,
        message=copy.RETURNS_MESSAGE_NO_SCHEME,
    )
    try:
        from src.ingest.load import load_sources
        from src.retrieve.retriever import detect_scheme_id

        scheme_id = detect_scheme_id(text)
        if scheme_id is None:
            return fallback
        for ref in load_sources():
            if ref.scheme_id == scheme_id:
                return GuardDecision(
                    allowed=False,
                    kind=KIND_RETURNS,
                    message=copy.RETURNS_MESSAGE.format(link=ref.source_url),
                    link_url=ref.source_url,
                    link_label=f"{ref.scheme_name} page",
                )
    except Exception as exc:  # a refusal must still render if this lookup fails
        logger.info("policy: scheme link unavailable for returns refusal (%s)", type(exc).__name__)
        return fallback
    return fallback


def check_query(text: str) -> GuardDecision:
    """Decide whether `text` may be sent to retrieval, and produce the refusal if not.

    This is the only public entry point. It never raises for user input: a malformed or
    empty message yields a decision, because a chat turn must always have a reply.
    """
    cleaned = _normalize(text)
    if not cleaned:
        return ALLOWED

    labels = _detect_pii(cleaned)
    if labels:
        # Log the label set only. The query itself is deliberately not logged, not
        # even truncated: a 60-character prefix of "My PAN is 1234..." is still PII.
        logger.info("policy: refused kind=%s pii_detected=%s", KIND_PII, ",".join(labels))
        return GuardDecision(allowed=False, kind=KIND_PII, message=copy.PII_MESSAGE)

    kind = _detect_intent(cleaned)
    if kind is None:
        logger.info("policy: allowed query_prefix=%r", cleaned[:60])
        return ALLOWED

    logger.info("policy: refused kind=%s query_prefix=%r", kind, cleaned[:60])
    if kind == KIND_ADVICE:
        return GuardDecision(
            allowed=False,
            kind=KIND_ADVICE,
            message=copy.ADVICE_MESSAGE,
            link_url=copy.AMFI_INVESTOR_EDUCATION_URL,
            link_label=copy.AMFI_INVESTOR_EDUCATION_LABEL,
        )
    if kind == KIND_RETURNS:
        return _returns_refusal(cleaned)
    if kind == KIND_GRIEVANCE:
        return GuardDecision(
            allowed=False,
            kind=KIND_GRIEVANCE,
            message=copy.GRIEVANCE_MESSAGE,
            link_url=copy.SEBI_SCORES_URL,
            link_label=copy.SEBI_SCORES_LABEL,
        )
    raise AssertionError(f"unhandled guard kind {kind!r}")
