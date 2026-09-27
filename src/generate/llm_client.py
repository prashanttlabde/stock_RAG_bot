"""The one concrete LLM client this project wires up (architecture.md §7 step 5).

`src/generate/answer.py`'s `generate_llm` is provider-agnostic: it calls
`client.complete(system=..., user=..., temperature=...)` and treats any exception as
a reason to fall back to the template path. This module is the one provider that
implements that interface -- Groq's OpenAI-compatible chat completions endpoint --
so `GENERATION_MODE=llm` does something when `GROQ_API_KEY` is set, instead of
silently running the template path forever.

This is the one place in the project that calls the network at query time, and that
is deliberate rather than an oversight: `GENERATION_MODE=llm` is an explicit,
documented opt-in (architecture.md §12, Phase 6), with a template fallback on any
failure, precisely so the default `template` mode keeps the project's "no network
calls at query time" guarantee (INV-6) while this mode exists for whoever wants a
paraphrased rather than extractive answer.
"""

from __future__ import annotations

import requests

from src import config
from src.logging_config import get_logger

logger = get_logger("generate")


class GroqClient:
    """A minimal wrapper around Groq's chat completions endpoint.

    Carries no state beyond the key and model, and raises on any failure (a bad
    key, a timeout, a malformed response) rather than swallowing it -- `generate_llm`
    is the layer that decides a raised exception means "fall back to the template",
    so this class must not make that decision itself.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def complete(self, system: str, user: str, temperature: float = 0) -> str:
        response = requests.post(
            config.GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
            },
            timeout=config.GROQ_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"unexpected Groq response shape: {data!r}") from exc


def build_groq_client() -> GroqClient | None:
    """A `GroqClient` if a key is configured, else `None`.

    Checks `config.GROQ_API_KEY` first, then the generic `config.LLM_API_KEY` --
    either is enough to enable this path. No key configured is not an error: it is
    exactly the state a fresh clone is in before anyone edits `.env`, and
    `generate_llm` already treats a `None` client as "use the template".
    """
    api_key = config.GROQ_API_KEY or config.LLM_API_KEY
    if not api_key:
        return None
    model = config.GROQ_MODEL or config.LLM_MODEL or config.GROQ_MODEL
    logger.info("generate: llm client wired to Groq model=%s", model)
    return GroqClient(api_key=api_key, model=model)
