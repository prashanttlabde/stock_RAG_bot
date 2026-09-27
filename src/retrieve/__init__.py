"""Retrieval package: the public surface the chat service and demo script import."""

from src.retrieve.retriever import (
    OUT_OF_CORPUS_FUND_HOUSES,
    OUT_OF_CORPUS_SCHEMES,
    SCHEME_ALIASES,
    RetrievalError,
    detect_out_of_corpus_fund_house,
    detect_out_of_corpus_scheme,
    detect_scheme_id,
    normalize_query,
    retrieve,
)
from src.types import RetrievalResult

__all__ = [
    "OUT_OF_CORPUS_FUND_HOUSES",
    "OUT_OF_CORPUS_SCHEMES",
    "SCHEME_ALIASES",
    "RetrievalError",
    "RetrievalResult",
    "detect_out_of_corpus_fund_house",
    "detect_out_of_corpus_scheme",
    "detect_scheme_id",
    "normalize_query",
    "retrieve",
]
