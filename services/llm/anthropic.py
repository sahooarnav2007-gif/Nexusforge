"""Anthropic Messages API provider (with tool use)."""
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


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None) -> None:
        s = get_settings()
        self.api_key = api_key or s.anthropic_api_key
        self.base_url = (base_url or s.anthropic_base_url).rstrip("/")
        self.model = model or s.llm_model or "claude-sonnet-4-20250514"
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            if not self.api_key:
                raise LLMError("Anthropic provider configured but no API key set "
                               "(NEXUSFORGE_ANTHROPIC_API_KEY)")
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=120.0,
            )
        return self._client

    async def complete(self, messages, *, temperature=0.2, max_tokens=None,
                       tools=None, tool_choice=None) -> LLMResponse:
        client = await self._http()
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        convo = [
            {"role": m.role, "content": m.content}
            for m in messages if m.role in ("user", "assistant")
        ]
        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens or get_settings().llm_max_output_tokens,
            "temperature": temperature,
            "messages": convo,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description,
                 "input_schema": t.parameters}
                for t in tools
            ]
        prompt_tokens = sum(estimate_tokens(m.content) for m in messages)
        import time

        t0 = time.monotonic()
        try:
            resp = await client.post("/messages", json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"Anthropic request failed: {type(e).__name__}") from e
        latency = int((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            raise LLMError(f"Anthropic API error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            content_parts = data.get("content", [])
            text = "".join(p.get("text", "") for p in content_parts
                           if p.get("type") == "text")
            calls = [
                ToolCall(id=p.get("id", f"call_{i}"), name=p.get("name", ""),
                         arguments=p.get("input") or {})
                for i, p in enumerate(content_parts)
                if p.get("type") == "tool_use"
            ]
            usage = data.get("usage") or {}
            return LLMResponse(
                content=text,
                tool_calls=calls,
                model=data.get("model", self.model),
                provider=self.name,
                prompt_tokens=usage.get("input_tokens", prompt_tokens),
                completion_tokens=usage.get("output_tokens", estimate_tokens(text)),
                latency_ms=latency,
                finish_reason=data.get("stop_reason", ""),
            )
        except (KeyError, TypeError) as e:
            raise LLMError(f"Malformed Anthropic response: {e}") from e

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None