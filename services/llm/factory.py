"""Provider factory: builds the configured LLM provider (with retry wrapper)."""
from __future__ import annotations

from functools import lru_cache

from core.config import get_settings
from core.errors import LLMError
from services.llm.base import LLMProvider, RetryingProvider


def build_provider() -> LLMProvider:
    s = get_settings()
    if s.llm_provider == "local":
        from services.llm.local import LocalProvider

        return RetryingProvider(LocalProvider())
    if s.llm_provider == "openai":
        from services.llm.openai_compat import OpenAICompatProvider

        return RetryingProvider(OpenAICompatProvider())
    if s.llm_provider == "anthropic":
        from services.llm.anthropic import AnthropicProvider

        return RetryingProvider(AnthropicProvider())
    if s.llm_provider == "gemini":
        from services.llm.gemini import GeminiProvider

        return RetryingProvider(GeminiProvider())
    raise LLMError(f"Unknown llm_provider: {s.llm_provider}")


@lru_cache
def get_llm() -> LLMProvider:
    return build_provider()