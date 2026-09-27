"""Guardrail package: the deterministic pre-retrieval gate (architecture.md §7 step 1).

The public surface is `check_query`, which the chat service calls before retrieval.
"""

from src.guards.policy import (
    ADVICE_PATTERNS,
    GRIEVANCE_PATTERNS,
    GUARD_KINDS,
    KIND_ADVICE,
    KIND_GRIEVANCE,
    KIND_OUT_OF_SCOPE,
    KIND_PII,
    KIND_RETURNS,
    PII_PATTERNS,
    RETURNS_PATTERNS,
    GuardDecision,
    check_query,
)

__all__ = [
    "ADVICE_PATTERNS",
    "GRIEVANCE_PATTERNS",
    "GUARD_KINDS",
    "KIND_ADVICE",
    "KIND_GRIEVANCE",
    "KIND_OUT_OF_SCOPE",
    "KIND_PII",
    "KIND_RETURNS",
    "PII_PATTERNS",
    "RETURNS_PATTERNS",
    "GuardDecision",
    "check_query",
]
