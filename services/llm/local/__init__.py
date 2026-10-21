"""Local deterministic LLM provider.

A real, general (but deliberately limited) structured-task model that runs
fully offline. It parses the machine-readable context block that agents embed
in their prompts and synthesizes structured outputs:

- requirement analysis (spec)
- planning (ordered steps)
- Python code synthesis (stub implementation, endpoint wiring)
- test generation (from contracts + repo conventions)
- failure diagnosis (pytest traceback parsing)
- repair (targeted patches from the diagnosis)
- code review (heuristic findings over a diff)

It is NOT a fake of a remote LLM: it performs genuine parsing/synthesis over
the repository content it is given, and it REFUSES (raises LLMError) anything
outside its documented capability envelope, so the platform fails loudly
instead of pretending.

Capability envelope (documented in docs/AGENTS.md):
- Python repositories (FastAPI routers + service layer + pytest)
- Stubs whose docstrings carry a "Contract:" block
- Failures expressible as pytest tracebacks
"""
from __future__ import annotations

import json
import re
import time

from core.errors import LLMError
from services.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResponse,
    ToolCall,
    ToolSpec,
    estimate_tokens,
    extract_context_block,
)
from services.llm.local import diagnose, review, synth


class LocalProvider(LLMProvider):
    name = "local"
    model = "nexusforge-local-1"

    async def complete(self, messages, *, temperature=0.2, max_tokens=None,
                       tools=None, tool_choice=None) -> LLMResponse:
        t0 = time.monotonic()
        last_user = next(
            (m for m in reversed(messages) if m.role == "user"), None
        )
        if last_user is None:
            raise LLMError("local provider: no user message with context")
        payload = extract_context_block(last_user.content)
        if payload is None:
            raise LLMError(
                "local provider: prompt is missing the structured context block; "
                "this provider only handles structured agent requests"
            )
        role = payload.get("role", "")
        prompt_tokens = sum(estimate_tokens(m.content) for m in messages)
        try:
            result = self._dispatch(role, payload)
        except LLMError:
            raise
        except Exception as e:  # synthesis bug -> honest failure
            raise LLMError(f"local provider failed on role '{role}': {e}") from e
        content = json.dumps(result, indent=2, default=str)
        return LLMResponse(
            content=content,
            tool_calls=[],
            model=self.model,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=estimate_tokens(content),
            latency_ms=int((time.monotonic() - t0) * 1000),
            finish_reason="stop",
        )

    def _dispatch(self, role: str, payload: dict) -> dict:
        if role == "requirement_analyzer":
            return synth.analyze_requirement(payload)
        if role == "planner":
            return synth.build_plan(payload)
        if role == "implementer":
            return synth.implement(payload)
        if role == "test_generator":
            return synth.generate_tests(payload)
        if role == "failure_analyzer":
            return diagnose.analyze(payload)
        if role == "repair_agent":
            return diagnose.repair(payload)
        if role == "code_reviewer":
            return review.review(payload)
        raise LLMError(
            f"local provider: role '{role}' is outside its capability envelope"
        )