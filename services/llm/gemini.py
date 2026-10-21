"""Google Gemini (generativelanguage REST) provider."""
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


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None) -> None:
        s = get_settings()
        self.api_key = api_key or s.gemini_api_key
        self.base_url = (base_url or s.gemini_base_url).rstrip("/")
        self.model = model or s.llm_model or "gemini-1.5-flash"
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            if not self.api_key:
                raise LLMError("Gemini provider configured but no API key set "
                               "(NEXUSFORGE_GEMINI_API_KEY)")
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=120.0)
        return self._client

    async def complete(self, messages, *, temperature=0.2, max_tokens=None,
                       tools=None, tool_choice=None) -> LLMResponse:
        client = await self._http()
        contents = []
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        for m in messages:
            if m.role == "system":
                continue
            contents.append({
                "role": "user" if m.role == "user" else "model",
                "parts": [{"text": m.content}],
            })
        body: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens or get_settings().llm_max_output_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{
                "functionDeclarations": [
                    {"name": t.name, "description": t.description,
                     "parameters": t.parameters}
                    for t in tools
                ]
            }]
        prompt_tokens = sum(estimate_tokens(m.content) for m in messages)
        import time

        models_to_try = [self.model]
        for fallback in ("gemini-3.5-flash", "gemini-3.6-flash", "gemini-3-flash-preview"):
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        resp = None
        used_model = self.model
        for candidate_model in models_to_try:
            url = f"/models/{candidate_model}:generateContent"
            try:
                resp = await client.post(url, params={"key": self.api_key}, json=body)
                if resp.status_code == 200:
                    used_model = candidate_model
                    break
                elif resp.status_code in (429, 503):
                    continue
                else:
                    break
            except httpx.HTTPError:
                continue

        latency = int((time.monotonic() - t0) * 1000)
        if resp is None or resp.status_code != 200:
            err_msg = resp.text[:300] if resp else "no response"
            status = resp.status_code if resp else 500
            raise LLMError(f"Gemini API error {status}: {err_msg}")
        data = resp.json()
        try:
            cand = data["candidates"][0]
            parts = cand["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if "text" in p)
            calls = []
            for i, p in enumerate(parts):
                if "functionCall" in p:
                    fc = p["functionCall"]
                    calls.append(ToolCall(id=f"call_{i}", name=fc.get("name", ""),
                                          arguments=fc.get("args") or {}))
            usage = data.get("usageMetadata") or {}
            return LLMResponse(
                content=text,
                tool_calls=calls,
                model=data.get("modelVersion", self.model),
                provider=self.name,
                prompt_tokens=usage.get("promptTokenCount", prompt_tokens),
                completion_tokens=usage.get("candidatesTokenCount",
                                            estimate_tokens(text)),
                latency_ms=latency,
                finish_reason=str(cand.get("finishReason", "")),
            )
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Malformed Gemini response: {e}") from e

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None