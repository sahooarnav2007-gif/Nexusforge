"""OpenAI-compatible chat provider (works with OpenAI, Azure, Ollama, vLLM,
LiteLLM, LM Studio, etc. via base_url)."""
from __future__ import annotations

import httpx

from core.config import get_settings
from core.errors import LLMError
from services.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResponse,
    ToolCall,
    ToolSpec,
    estimate_tokens,
)


class OpenAICompatProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None) -> None:
        s = get_settings()
        self.api_key = api_key or s.openai_api_key
        self.base_url = (base_url or s.openai_base_url).rstrip("/")
        self.model = model or s.llm_model or "gpt-4o-mini"
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            if not self.api_key:
                raise LLMError("OpenAI provider configured but no API key set "
                               "(NEXUSFORGE_OPENAI_API_KEY)")
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=120.0,
            )
        return self._client

    async def complete(self, messages, *, temperature=0.2, max_tokens=None,
                       tools=None, tool_choice=None) -> LLMResponse:
        client = await self._http()
        body: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens or get_settings().llm_max_output_tokens,
        }
        if tools:
            body["tools"] = [
                {"type": "function",
                 "function": {"name": t.name, "description": t.description,
                              "parameters": t.parameters}}
                for t in tools
            ]
            if tool_choice:
                body["tool_choice"] = tool_choice
        prompt_tokens = sum(estimate_tokens(m.content) for m in messages)
        t0 = _now()
        try:
            resp = await client.post("/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"OpenAI request failed: {type(e).__name__}") from e
        latency = int((_now() - t0) * 1000)
        if resp.status_code != 200:
            raise LLMError(f"OpenAI API error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            choice = data["choices"][0]
            msg = choice["message"]
            content = msg.get("content") or ""
            calls = [
                ToolCall(id=c.get("id", f"call_{i}"),
                         name=c["function"]["name"],
                         arguments=c["function"].get("arguments") or {})
                for i, c in enumerate(msg.get("tool_calls") or [])
            ]
            usage = data.get("usage") or {}
            return LLMResponse(
                content=content,
                tool_calls=calls,
                model=data.get("model", self.model),
                provider=self.name,
                prompt_tokens=usage.get("prompt_tokens", prompt_tokens),
                completion_tokens=usage.get("completion_tokens",
                                            estimate_tokens(content)),
                latency_ms=latency,
                finish_reason=choice.get("finish_reason", ""),
            )
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Malformed OpenAI response: {e}") from e

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


def _now() -> float:
    import time

    return time.monotonic()