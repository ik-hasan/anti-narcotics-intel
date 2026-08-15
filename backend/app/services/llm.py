"""Provider-agnostic access to the extraction LLM.

Everything downstream depends on this interface, not on a vendor SDK, so swapping
providers is an env var rather than a code change. That mattered in practice: the
project moved off Gemini mid-build after its API project was blocked.
"""

import logging
from typing import Protocol, runtime_checkable

from app.config import settings
from app.models.extraction import ArticleExtraction

logger = logging.getLogger(__name__)


@runtime_checkable
class LLMService(Protocol):
    name: str

    @property
    def configured(self) -> bool: ...

    @property
    def model(self) -> str: ...

    async def extract_article(self, text: str, title: str = "") -> ArticleExtraction: ...

    async def complete(
        self, system: str, user: str, *, max_tokens: int = 900, temperature: float = 0.2
    ) -> str: ...

    async def health(self) -> bool: ...


_cache: dict[str, LLMService] = {}


def get_llm() -> LLMService:
    provider = (settings.llm_provider or "groq").strip().lower()

    if provider in _cache:
        return _cache[provider]

    if provider == "groq":
        from app.services.groq_service import GroqService

        service: LLMService = GroqService()
    elif provider == "gemini":
        from app.services.gemini import GeminiService

        service = GeminiService()
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER={provider!r}. Supported providers: groq, gemini"
        )

    _cache[provider] = service
    return service


def reset_llm_cache() -> None:
    """Drop cached clients so a settings change takes effect (used by scripts)."""
    _cache.clear()
