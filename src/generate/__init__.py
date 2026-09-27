"""Generation package: retrieved hits in, one grounded cited answer out."""

from src.generate.answer import (
    answer_question,
    compose_context,
    generate_llm,
    generate_template,
    is_ambiguous_source,
    select_primary_source,
    split_units,
    strip_urls,
    truncate_sentences,
)
from src.generate.no_answer import no_answer_message
from src.generate.prompt import GROUNDING_CHECK, NOT_IN_CONTEXT, SYSTEM_RULES, build_user_prompt

__all__ = [
    "GROUNDING_CHECK",
    "NOT_IN_CONTEXT",
    "SYSTEM_RULES",
    "answer_question",
    "build_user_prompt",
    "compose_context",
    "generate_llm",
    "generate_template",
    "is_ambiguous_source",
    "no_answer_message",
    "select_primary_source",
    "split_units",
    "strip_urls",
    "truncate_sentences",
]
