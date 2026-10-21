"""AgentContext: the shared, structured context every agent operates on.

Holds the run, budgets, tool access, LLM, event emitter, and retrieval.
Agents never touch the DB or event bus directly — they go through this
context so behavior is uniform, auditable, and testable.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.errors import LLMError
from core.events import event_bus
from core.logging import get_logger, project_id_var, task_id_var
from core.metrics import AGENT_STEPS
from core.models import (
    AgentStep,
    CodeChange,
    Project,
    Repository,
    Task,
    TaskEvent,
    TaskRun,
    TaskState,
)
from services.indexing.search import RetrievalQuery, search_engine
from services.llm.base import (
    LLMMessage,
    LLMProvider,
    ToolSpec,
    make_context_block,
)
from services.tools.registry import ToolContext, ToolRegistry

log = get_logger("nexusforge.agent")


@dataclass
class Budget:
    max_iterations: int
    max_wall_seconds: int
    max_llm_calls: int
    max_context_tokens: int
    llm_calls: int = 0
    tokens_used: int = 0
    started_at: float = field(default_factory=time.time)

    def check_wall(self) -> None:
        if time.time() - self.started_at > self.max_wall_seconds:
            raise TimeoutError("run wall-clock budget exceeded")

    def check_llm(self) -> None:
        if self.llm_calls >= self.max_llm_calls:
            raise LLMError("run LLM-call budget exceeded")

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at


class AgentContext:
    def __init__(
        self,
        db: AsyncSession,
        *,
        project: Project,
        task: Task,
        run: TaskRun,
        repo: Repository,
        workspace: Path,
        sandbox: Any,
        tools: ToolRegistry,
        llm: LLMProvider,
    ) -> None:
        self.db = db
        self.project = project
        self.task = task
        self.run = run
        self.repo = repo
        self.workspace = workspace
        self.sandbox = sandbox
        self.tools = tools
        self.llm = llm
        s = get_settings()
        self.budget = Budget(
            max_iterations=s.max_agent_iterations,
            max_wall_seconds=s.max_execution_time_seconds,
            max_llm_calls=s.max_llm_calls_per_run,
            max_context_tokens=s.max_context_tokens,
        )
        self._seq = 0
        self._step_seq = 0
        self._data: dict[str, Any] = {}  # inter-agent data (spec, plan, ...)
        task_id_var.set(task.id)
        project_id_var.set(project.id)

    # --- data flow between agents ------------------------------------------
    def set_data(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get_data(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    # --- events --------------------------------------------------------------
    async def emit(self, type: str, payload: dict[str, Any]) -> int:
        self._seq += 1
        evt = TaskEvent(run_id=self.run.id, seq=self._seq, type=type, payload=payload)
        self.db.add(evt)
        await self.db.flush()
        await event_bus.publish(f"run:{self.run.id}",
                                {"type": type, "seq": self._seq, **payload})
        return self._seq

    async def emit_terminal(self, line: str, stream: str = "out") -> None:
        await self.emit("terminal", {"text": line, "stream": stream})

    async def set_state(self, state: TaskState) -> None:
        from services.agent.states import transition

        current = TaskState(self.run.state)
        if current != state:
            transition(current, state)  # validates
        self.run.state = state.value
        self.task.status = state.value
        await self.db.flush()
        await self.emit("state_change", {"state": state.value,
                                         "iteration": self.run.iteration})

    async def check_cancel(self) -> bool:
        await self.db.refresh(self.run)
        return bool(self.run.cancel_requested)

    # --- persistence -----------------------------------------------------------
    async def commit(self) -> None:
        """Make all flushed progress (events, steps, changes) durable.

        Called at step boundaries so a crashed run can resume from its last
        completed step instead of losing everything to a rollback."""
        try:
            await self.db.commit()
            # keep the in-memory step list current so _not_done() sees steps
            # committed earlier in this run
            await self.db.refresh(self.run, attribute_names=["steps", "events"])
        except Exception:  # noqa: BLE001
            # a dead session can't commit; the engine's recovery handles it
            try:
                await self.db.rollback()
            except Exception:
                pass

    # --- steps ----------------------------------------------------------------
    async def begin_step(self, agent: str, state: TaskState,
                         step_input: dict[str, Any]) -> AgentStep:
        self._step_seq += 1
        step = AgentStep(
            run_id=self.run.id,
            seq=self._step_seq,
            agent=agent,
            state=state.value,
            status="running",
            input=step_input,
            started_at=_now(),
        )
        self.db.add(step)
        await self.db.flush()
        await self.emit("step_started", {"agent": agent, "state": state.value,
                                         "seq": step.seq})
        return step

    async def finish_step(self, step: AgentStep, *, status: str,
                          output: dict[str, Any] | None = None,
                          summary: str = "", error: str | None = None,
                          tokens: int = 0) -> None:
        step.status = status
        step.output = output or {}
        step.summary = summary
        step.error = error
        step.tokens_used = tokens
        step.finished_at = _now()
        self.run.tokens_used += tokens
        await self.db.flush()
        AGENT_STEPS.labels(step.agent, status).inc()
        await self.emit("step_finished", {
            "agent": step.agent, "status": status, "summary": summary,
            "error": error, "seq": step.seq,
        })

    # --- LLM --------------------------------------------------------------------
    async def llm_complete(
        self,
        *,
        role: str,
        system: str,
        user: str,
        context: dict[str, Any],
        tools: list[ToolSpec] | None = None,
    ):
        self.budget.check_llm()
        self.budget.check_wall()
        messages = [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user",
                       content=user + "\n\n" + make_context_block(
                           {"role": role, **context})),
        ]
        resp = await self.llm.complete(messages, tools=tools)
        self.budget.llm_calls += 1
        self.budget.tokens_used += resp.prompt_tokens + resp.completion_tokens
        self.run.llm_calls = self.budget.llm_calls
        self.run.tokens_used = self.budget.tokens_used
        await self.emit("llm_call", {
            "role": role, "provider": resp.provider, "model": resp.model,
            "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
            "latency_ms": resp.latency_ms,
        })
        return resp

    # --- retrieval ---------------------------------------------------------------
    async def retrieve(self, query: str, *, languages: list[str] | None = None,
                       path_globs: list[str] | None = None,
                       max_chunks: int = 10,
                       token_budget: int | None = None,
                       boost_symbols: list[str] | None = None) -> list:
        return await search_engine.search(
            self.db, self.repo.id,
            RetrievalQuery(
                text=query,
                languages=languages,
                path_globs=path_globs,
                max_chunks=max_chunks,
                token_budget=token_budget or self.budget.max_context_tokens // 2,
                boost_symbols=boost_symbols,
            ),
        )

    async def file_contents(self, paths: list[str], max_bytes: int = 60_000) -> list[dict]:
        out = []
        for p in paths:
            fp = self.workspace / p
            if not fp.is_file():
                continue
            try:
                if fp.stat().st_size > max_bytes:
                    continue
                out.append({"path": p, "content": fp.read_text(encoding="utf-8")})
            except (OSError, UnicodeDecodeError):
                continue
        return out

    def resolve_workspace_path(self, path: str) -> str | None:
        """Resolve a path (which may be a bare basename from a traceback
        frame, e.g. ``test_users.py``) to a workspace-relative file path.

        Returns the original path if it already resolves to a file, otherwise
        searches the workspace for a unique file whose name or suffix matches.
        Returns None when nothing matches.
        """
        from pathlib import PurePosixPath

        direct = self.workspace / path
        if direct.is_file():
            return path
        name = PurePosixPath(path).name
        candidates = [
            str(p.relative_to(self.workspace))
            for p in self.workspace.rglob(name)
            if p.is_file() and ".git" not in p.parts
        ]
        if len(candidates) == 1:
            return candidates[0]
        # multiple hits: disambiguate by the directory portion of `path`
        # (e.g. "app/routers/users.py" vs "app/services/users.py")
        if "/" in path:
            suffix_hits = [c for c in candidates if c == path or c.endswith("/" + path)]
            if len(suffix_hits) == 1:
                return suffix_hits[0]
        return None

    def route_file_paths(self, max_files: int = 10) -> list[str]:
        """Workspace-relative paths of Python files that define HTTP routes
        (FastAPI routers / app). Used to give the repair agent the handler
        files when a failing test points at wrong app behavior."""
        out: list[str] = []
        for p in sorted(self.workspace.rglob("*.py")):
            if not p.is_file() or ".git" in p.parts:
                continue
            if p.name.startswith("test_") or "/tests/" in str(p) or "/test/" in str(p):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "APIRouter" in text or "FastAPI(" in text:
                out.append(str(p.relative_to(self.workspace)))
                if len(out) >= max_files:
                    break
        return out

    # --- tool access ----------------------------------------------------------------
    def tool_ctx(self, agent: str, permissions: set[str],
                 extra: dict | None = None) -> ToolContext:
        return ToolContext(
            workspace=self.workspace,
            run_id=self.run.id,
            task_id=self.task.id,
            project_id=self.project.id,
            agent=agent,
            permissions=permissions,
            sandbox=self.sandbox,
            extra=extra or {},
        )

    # --- change tracking -------------------------------------------------------------
    async def record_change(self, path: str, action: str, before: str | None,
                            after: str | None, reasoning: str) -> str:
        import difflib

        diff = "".join(difflib.unified_diff(
            (before or "").splitlines(keepends=True),
            (after or "").splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        ))
        existing = (
            await self.db.execute(
                select(CodeChange).where(CodeChange.run_id == self.run.id,
                                         CodeChange.path == path)
            )
        ).scalar_one_or_none()
        if existing is None:
            change = CodeChange(
                run_id=self.run.id, path=path, action=action,
                before_content=before, after_content=after, diff=diff,
                reasoning=reasoning, iteration=self.run.iteration,
            )
            self.db.add(change)
        else:
            existing.action = action
            existing.after_content = after
            existing.diff = diff
            existing.reasoning = reasoning
            existing.iteration = self.run.iteration
        await self.db.flush()
        await self.emit("file_changed", {"path": path, "action": action,
                                         "iteration": self.run.iteration})
        return diff


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def context_to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, default=str)