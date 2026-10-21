"""LLM provider abstraction.

Every provider implements `complete()` with the same message/tool contract.
Agents never talk to a vendor SDK directly — they talk to this interface,
which keeps the platform provider-agnostic and offline-capable.

Context contract: agents embed a machine-readable block in the user message:

    <<<CONTEXT_JSON>>>
    {"role": "planner", "spec": {...}, ...}
    <<<END_CONTEXT_JSON>>>

Remote providers see it as data; the local deterministic provider parses it.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from core.config import get_settings
from core.errors import LLMError
from core.logging import get_logger
from core.metrics import LLM_CALLS, LLM_TOKENS

log = get_logger("nexusforge.llm")

CONTEXT_OPEN = "<<<CONTEXT_JSON>>>"
CONTEXT_CLOSE = "<<<END_CONTEXT_JSON>>>"


@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str = ""


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def make_context_block(payload: dict[str, Any]) -> str:
    import json

    return f"{CONTEXT_OPEN}\n{json.dumps(payload, default=str)}\n{CONTEXT_CLOSE}"


def extract_context_block(text: str) -> dict[str, Any] | None:
    import json

    start = text.find(CONTEXT_OPEN)
    end = text.find(CONTEXT_CLOSE)
    if start == -1 or end == -1 or end < start:
        return None
    raw = text[start + len(CONTEXT_OPEN):end].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


class LLMProvider:
    """Interface every provider implements."""

    name: str = "base"
    model: str = ""

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        tools: list[ToolSpec] | None = None,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - trivial
        pass


class RetryingProvider(LLMProvider):
    """Wraps a provider with bounded retries + exponential backoff + metrics."""

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model

    async def complete(self, messages, **kw) -> LLMResponse:
        s = get_settings()
        last_err: Exception | None = None
        for attempt in range(s.llm_max_retries + 1):
            try:
                resp = await asyncio.wait_for(
                    self.inner.complete(messages, **kw),
                    timeout=s.llm_timeout_seconds,
                )
                LLM_CALLS.labels(self.name, "ok").inc()
                LLM_TOKENS.labels(self.name, "prompt").inc(resp.prompt_tokens)
                LLM_TOKENS.labels(self.name, "completion").inc(resp.completion_tokens)
                return resp
            except asyncio.TimeoutError as e:
                last_err = LLMError(f"LLM call timed out after {s.llm_timeout_seconds}s")
            except LLMError as e:
                last_err = e
            except Exception as e:  # network etc.
                last_err = LLMError(f"LLM provider error: {type(e).__name__}: {e}")
            LLM_CALLS.labels(self.name, "retry").inc()
            if attempt < s.llm_max_retries:
                await asyncio.sleep(2 ** attempt)
        LLM_CALLS.labels(self.name, "failed").inc()
        raise last_err  # type: ignore[misc]

    async def close(self) -> None:
        await self.inner.close()