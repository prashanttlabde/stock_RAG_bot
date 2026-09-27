"""Logging setup (architecture.md §14).

`setup_logging()` is idempotent: calling it from a library module, an ingest
CLI, and the Streamlit entry point will not stack duplicate handlers.
"""

from __future__ import annotations

import logging
import sys

from src import config

_CONFIGURED = False

# Third-party loggers that are useful when debugging a dependency and pure noise
# in this app's log. The Hugging Face hub check alone emits ~15 INFO lines per
# ingest, which would bury the per-stage timings that architecture.md §14 asks to
# be observable.
NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "urllib3",
    "filelock",
    "chromadb.telemetry",
    "sentence_transformers",
    "transformers",
    "huggingface_hub",
    "torch",
)


def setup_logging(level: str | int | None = None) -> logging.Logger:
    """Configure root logging once and return the project logger."""
    global _CONFIGURED

    resolved = level or config.LOG_LEVEL
    if isinstance(resolved, str):
        resolved = getattr(logging, resolved.upper(), logging.INFO)

    if not _CONFIGURED:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter(config.LOG_FORMAT))
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(resolved)
        for name in NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
        _CONFIGURED = True

    logger = logging.getLogger("mf_rag")
    logger.setLevel(resolved)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child of the project logger, configuring logging on first use."""
    setup_logging()
    return logging.getLogger(f"mf_rag.{name}") if name else logging.getLogger("mf_rag")
