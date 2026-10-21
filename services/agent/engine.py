"""The agent orchestrator: drives one run through the state machine.

    QUEUED → ANALYZING → PLANNING → IMPLEMENTING → TESTING
         → (fail & budget left) DEBUGGING → RETESTING → TESTING ...
         → SECURITY_ANALYSIS → (blocking?) SECURITY_BLOCKED
         → CODE_REVIEW → COMPLETED

Every transition is validated and persisted; every step is checkpointed in
AgentStep rows so a crashed run can be re-queued and re-executed from its
last completed step.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from sqlalchemy import select

from core.config import get_settings
from core.db import get_session_factory
from core.errors import NexusForgeError, LLMError
from core.logging import get_logger, project_id_var, task_id_var
from core.metrics import TASK_RUNS
from core.models import (
    Project,
    Repository,
    Task,
    TaskRun,
    TaskState,
    TERMINAL_STATES,
)
from services.agent.agents import (
    CodeReviewer,
    ExecutionAgent,
    FailureAnalyzer,
    Implementer,
    Planner,
    RepairAgent,
    ReportWriter,
    RequirementAnalyzer,
    RepoAnalyst,
    SecurityAgent,
    TestGenerator,
)
from services.agent.context import AgentContext
from services.llm.factory import get_llm
from services.sandbox.sandbox import build_sandbox
from services.tools.git_tools import build_registry

log = get_logger("nexusforge.engine")


async def execute_run(run_id: str) -> None:
    """Entry point for the worker: execute one run to a terminal state."""
    factory = get_session_factory()
    async with factory() as db:
        from sqlalchemy.orm import selectinload

        run = (
            await db.execute(
                select(TaskRun)
                .where(TaskRun.id == run_id)
                .options(
                    selectinload(TaskRun.steps),
                    selectinload(TaskRun.events),
                )
            )
        ).scalar_one_or_none()
        if run is None:
            log.error("run not found", extra={"run_id": run_id})
            return
        task = await db.get(Task, run.task_id)
        project = await db.get(Project, run.project_id)
        if task is None or project is None:
            await _fail(db, run, "Task or project missing")
            return
        repo = await db.get(Repository, task.repository_id) if task.repository_id else None
        if repo is None:
            await _fail(db, run, "Task has no repository")
            return
        if not Path(repo.workspace_path).is_dir():
            await _fail(db, run, "Repository workspace missing; re-import the repository")
            return

        task_id_var.set(task.id)
        project_id_var.set(project.id)

        sandbox = build_sandbox()
        tools = build_registry()
        llm = get_llm()
        ctx = AgentContext(
            db,
            project=project,
            task=task,
            run=run,
            repo=repo,
            workspace=Path(repo.workspace_path),
            sandbox=sandbox,
            tools=tools,
            llm=llm,
        )
        # resume-safe sequence counters: a re-queued run already has
        # events/steps in the DB, so continue numbering after the max
        from sqlalchemy import func
        from core.models import AgentStep, TaskEvent

        max_evt = (
            await db.execute(
                select(func.coalesce(func.max(TaskEvent.seq), 0)).where(TaskEvent.run_id == run.id)
            )
        ).scalar_one()
        max_step = (
            await db.execute(
                select(func.coalesce(func.max(AgentStep.seq), 0)).where(AgentStep.run_id == run.id)
            )
        ).scalar_one()
        ctx._seq = int(max_evt)
        ctx._step_seq = int(max_step)

        started = False
        try:
            if run.state == TaskState.QUEUED.value:
                run.started_at = _now()
                started = True
            await _run_pipeline(ctx)
        except asyncio.CancelledError:
            await _recover_session(db, run, task)
            await _finish(db, ctx, TaskState.CANCELLED, "cancelled")
            raise
        except TimeoutError as e:
            await _recover_session(db, run, task)
            await _finish(db, ctx, TaskState.TIMEOUT, str(e))
        except NexusForgeError as e:
            await _recover_session(db, run, task)
            await _finish(db, ctx, TaskState.FAILED, e.message)
        except LLMError as e:
            await _recover_session(db, run, task)
            await _finish(db, ctx, TaskState.FAILED, f"LLM error: {e.message}")
        except Exception as e:  # noqa: BLE001
            log.exception("run crashed", extra={"run_id": run_id})
            await _recover_session(db, run, task)
            await _finish(db, ctx, TaskState.FAILED,
                          f"Internal error: {type(e).__name__}: {e}")
        finally:
            try:
                await db.commit()
            except Exception:
                # session may be in a rolled-back state after an exception;
                # persist what we can in a fresh transaction
                try:
                    await db.rollback()
                    run.state = TaskState.FAILED.value
                    run.error = run.error or "run aborted by internal error"
                    await db.commit()
                except Exception:
                    log.exception("could not persist final run state", extra={"run_id": run_id})
            try:
                await sandbox.close()
            except Exception:
                pass
            if started and run.state not in {s.value for s in TERMINAL_STATES}:
                # safety net: anything still non-terminal is failed
                run.state = TaskState.FAILED.value
                run.error = run.error or "run did not reach a terminal state"
                try:
                    await db.commit()
                except Exception:
                    pass


async def _restore_context(ctx: AgentContext) -> None:
    """Rebuild in-memory inter-agent data from persisted step outputs so a
    resumed run continues where it left off."""
    for s in ctx.run.steps:
        if s.status != "completed" or not s.output:
            continue
        if s.agent == "requirement_analyzer" and not ctx.get_data("spec"):
            ctx.set_data("spec", s.output)
        elif s.agent == "planner" and not ctx.get_data("plan"):
            ctx.set_data("plan", s.output)
    if not ctx.get_data("repo_summary"):
        # deterministic: recompute from the index (no LLM needed)
        from core.models import IndexedFile

        res = await ctx.db.execute(
            select(IndexedFile).where(IndexedFile.repository_id == ctx.repo.id)
        )
        files = list(res.scalars())
        test_files = [f.path for f in files if f.is_test]
        ctx.set_data("repo_summary", {
            "name": ctx.repo.name,
            "languages": ctx.repo.languages or {},
            "file_count": ctx.repo.file_count,
            "total_bytes": ctx.repo.total_bytes,
            "test_files": test_files[:20],
            "top_symbols": [],
            "head_commit": ctx.repo.head_commit,
        })


async def _run_pipeline(ctx: AgentContext) -> None:
    db = ctx.db
    run = ctx.run

    # --- ANALYZING -----------------------------------------------------------
    if _not_done(run, "repo_analyst"):
        await RepoAnalyst().run(ctx)
    await _restore_context(ctx)
    if _not_done(run, "requirement_analyzer"):
        await RequirementAnalyzer().run(ctx)
    await ctx.commit()

    # --- PLANNING --------------------------------------------------------------
    if _not_done(run, "planner"):
        await Planner().run(ctx)
    await ctx.commit()

    # --- IMPLEMENTING ------------------------------------------------------------
    if _not_done(run, "implementer"):
        await Implementer().run(ctx)
    await ctx.commit()
    if _not_done(run, "test_generator"):
        await TestGenerator().run(ctx)
    await ctx.commit()

    # --- TESTING / DEBUGGING LOOP ---------------------------------------------------
    await ExecutionAgent().run(ctx)
    await ctx.commit()
    iteration = 0
    while not ctx.get_data("last_test", {}).get("ok"):
        if await ctx.check_cancel():
            await _finish(db, ctx, TaskState.CANCELLED, "cancelled by user")
            return
        ctx.budget.check_wall()
        if iteration >= ctx.budget.max_iterations:
            parsed = ctx.get_data("last_test", {}).get("parsed")
            raise NexusForgeError(
                f"Test failures persist after {iteration} repair iteration(s); "
                f"last failures: {(parsed.failed_tests if parsed else [])[:3]}"
            )
        iteration += 1
        run.iteration = iteration
        await db.flush()
        await ctx.emit("iteration", {"iteration": iteration})
        await FailureAnalyzer().run(ctx)
        await ctx.commit()
        await RepairAgent().run(ctx)
        await ctx.commit()
        await ExecutionAgent().run(ctx, retest=True)
        await ctx.commit()
    # tests green

    # --- SECURITY -----------------------------------------------------------------
    await SecurityAgent().run(ctx)
    s = get_settings()
    security = ctx.get_data("security", {})
    blocking = [f for f in security.get("findings", [])
                if f["severity"] == "critical" or
                (f["severity"] == "high" and s.security_block_on == "high")]
    if blocking and s.security_block_on != "none":
        await _finish(db, ctx, TaskState.SECURITY_BLOCKED,
                      f"blocked by {len(blocking)} security finding(s): "
                      + ", ".join(f["rule_id"] for f in blocking[:5]))
        return

    # --- REVIEW + REPORT -------------------------------------------------------------
    await CodeReviewer().run(ctx)
    await ReportWriter().run(ctx)
    await _finish(db, ctx, TaskState.COMPLETED, None)


def _not_done(run: TaskRun, agent: str) -> bool:
    return not any(s.agent == agent and s.status == "completed" for s in run.steps)


async def _fail(db, run: TaskRun, message: str) -> None:
    from core.models import TaskEvent

    run.state = TaskState.FAILED.value
    run.error = message
    run.finished_at = _now()
    task = await db.get(Task, run.task_id)
    if task:
        task.status = TaskState.FAILED.value
    seq = await _next_event_seq(db, run.id)
    db.add(TaskEvent(run_id=run.id, seq=seq, type="run_failed",
                     payload={"error": message}))
    TASK_RUNS.labels("FAILED").inc()
    await db.commit()
    from core.events import event_bus

    await event_bus.publish(f"run:{run.id}", {"type": "run_failed", "seq": seq,
                                              "error": message})


async def _recover_session(db, run: TaskRun, task: Task) -> None:
    """After an exception, the session's transaction is dead. Roll it back and
    refresh the ORM objects so the terminal-state handler can persist in a
    fresh transaction (rollback expires all loaded attributes)."""
    try:
        await db.rollback()
        await db.refresh(run)
        await db.refresh(task)
    except Exception:
        pass


async def _next_event_seq(db, run_id: str) -> int:
    """Max event seq for the run straight from the DB (the ORM relationship
    only reflects rows that were loaded before this run started)."""
    from sqlalchemy import func
    from core.models import TaskEvent

    res = await db.execute(select(func.coalesce(func.max(TaskEvent.seq), 0)).where(TaskEvent.run_id == run_id))
    return int(res.scalar_one()) + 1


async def _finish(db, ctx: AgentContext, state: TaskState, error: str | None) -> None:
    from core.models import TaskEvent

    run = ctx.run
    run.state = state.value
    run.error = error
    run.finished_at = _now()
    ctx.task.status = state.value
    seq = await _next_event_seq(db, run.id)
    event_type = {
        TaskState.COMPLETED: "run_completed",
        TaskState.FAILED: "run_failed",
        TaskState.CANCELLED: "run_cancelled",
        TaskState.TIMEOUT: "run_timeout",
        TaskState.SECURITY_BLOCKED: "run_security_blocked",
    }.get(state, "run_finished")
    payload = {"state": state.value, "iteration": run.iteration,
               "tokens_used": run.tokens_used, "llm_calls": run.llm_calls}
    if error:
        payload["error"] = error
    db.add(TaskEvent(run_id=run.id, seq=seq, type=event_type, payload=payload))
    TASK_RUNS.labels(state.value).inc()
    await db.flush()
    from core.events import event_bus

    await event_bus.publish(f"run:{run.id}", {"type": event_type, "seq": seq, **payload})
    log.info("run finished", extra={"run_id": run.id, "state": state.value,
                                    "iteration": run.iteration,
                                    "tokens": run.tokens_used})


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)