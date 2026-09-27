"""Domain exceptions surfaced across the pipelines (architecture.md §7).

The UI catches these and renders a friendly message instead of a stack trace,
so each one carries a message that is safe to show a user.
"""


class ChatbotError(Exception):
    """Base class for every error raised by this project."""


class CorpusError(ChatbotError):
    """Corpus files are missing, malformed, or fail fact-coverage checks."""


class IngestError(ChatbotError):
    """An ingestion stage (load, chunk, embed, store) failed."""


class RetrievalError(ChatbotError):
    """The vector store is unavailable or a search could not be completed."""


class GenerationError(ChatbotError):
    """Answer generation failed and produced no usable output."""


class PolicyRefusal(ChatbotError):
    """A query was refused by the guardrail policy layer.

    Carries only the refusal `kind` and user-safe copy. It must never carry the
    matched PII value or any other raw user text (INV-3).
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
