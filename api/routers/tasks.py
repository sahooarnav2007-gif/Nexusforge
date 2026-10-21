"""Task & run endpoints, including the SSE event stream."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.deps import CurrentUser, DbSession
from core.audit import audit
from core.db import get_session_factory
from core.errors import ConflictError, NotFoundError, ValidationError
from core.events import event_bus
from core.logging import get_logger, project_id_var, task_id_var
from core.models import (
    CodeChange,
    Project,
    Repository,
    Report,
    ReviewFinding,
    SecurityFinding,
    Task,
    TaskEvent,
    TaskRun,
    TaskState,
    TestRun,
    TERMINAL_STATES,
)
from core.queue import get_queue
from core.schemas import (
    CancelResult,
    CodeChangeOut,
    DiffOut,
    Page,
    ReportOut,
    ReviewFindingOut,
    RunRequest,
    SecurityFindingOut,
    TaskCreate,
    TaskDetail,
    TaskEventOut,
    TaskOut,
    TaskRunOut,
    TaskRunSummary,
    TestRunOut,
)

log = get_logger("nexusforge.api.tasks")
router = APIRouter(prefix="/tasks", tags=["tasks"])


def _task_out(task: Task, latest_run: TaskRun | None) -> TaskOut:
    return TaskOut(
        id=task.id,
        project_id=task.project_id,
        repository_id=task.repository_id,
        title=task.title,
        requirement=task.requirement,
        status=task.status,
        created_at=task.created_at,
        updated_at=task.updated_at,
        latest_run_id=latest_run.id if latest_run else None,
    )


def _run_out(run: TaskRun) -> TaskRunOut:
    return TaskRunOut(
        id=run.id, task_id=run.task_id, project_id=run.project_id, state=run.state,
        iteration=run.iteration, llm_calls=run.llm_calls, tokens_used=run.tokens_used,
        cost_estimate=run.cost_estimate, started_at=run.started_at,
        finished_at=run.finished_at, error=run.error, created_at=run.created_at,
    )


async def _owned_project(db, user: CurrentUser, project_id: str) -> Project:
    project = (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    return project


@router.get("/projects/{project_id}", response_model=Page)
async def list_project_tasks(project_id: str, user: CurrentUser, db: DbSession,
                             limit: int = Query(default=50, le=200),
                             offset: int = Query(default=0, ge=0)):
    await _owned_project(db, user, project_id)
    tasks = (
        (
            await db.execute(
                select(Task)
                .where(Task.project_id == project_id)
                .order_by(Task.created_at.desc())
                .limit(limit)
                .offset(offset)
                .options(selectinload(Task.runs))
            )
        )
        .scalars()
        .all()
    )
    items = []
    for t in tasks:
        latest = t.runs[-1] if t.runs else None
        items.append(_task_out(t, latest))
    return Page(items=items, total=len(items), limit=limit, offset=offset)


@router.post("/projects/{project_id}", response_model=TaskOut, status_code=201)
async def create_task(project_id: str, body: TaskCreate, user: CurrentUser, db: DbSession):
    project = await _owned_project(db, user, project_id)
    if body.repository_id:
        repo = (
            await db.execute(
                select(Repository).where(
                    Repository.id == body.repository_id,
                    Repository.project_id == project.id,
                )
            )
        ).scalar_one_or_none()
        if repo is None:
            raise ValidationError("Repository not found in this project")
    else:
        # default to the project's first repository
        repo = (
            await db.execute(
                select(Repository).where(Repository.project_id == project.id).limit(1)
            )
        ).scalar_one_or_none()
        if repo is None:
            raise ValidationError("Project has no repository; import one first")
    task = Task(
        project_id=project.id,
        repository_id=repo.id,
        title=body.title,
        requirement=body.requirement,
    )
    db.add(task)
    await db.flush()
    audit("task_created", user_id=user.id, project_id=project.id, task_id=task.id)
    return _task_out(task, None)


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(task_id: str, user: CurrentUser, db: DbSession):
    task = (
        await db.execute(
            select(Task)
            .where(Task.id == task_id)
            .options(
                selectinload(Task.runs).selectinload(TaskRun.steps),
                selectinload(Task.runs).selectinload(TaskRun.changes),
                selectinload(Task.runs).selectinload(TaskRun.test_runs),
                selectinload(Task.runs).selectinload(TaskRun.security_findings),
                selectinload(Task.runs).selectinload(TaskRun.review_findings),
                selectinload(Task.runs).selectinload(TaskRun.reports),
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise NotFoundError("Task not found")
    project = await db.get(Project, task.project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Task not found")
    latest = task.runs[-1] if task.runs else None
    return TaskDetail(
        task=_task_out(task, latest),
        runs=[_run_out(r) for r in task.runs],
        latest_run=_run_out(latest) if latest else None,
        steps=[
            {
                "seq": s.seq, "agent": s.agent, "state": s.state, "status": s.status,
                "summary": s.summary, "error": s.error, "tokens_used": s.tokens_used,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "finished_at": s.finished_at.isoformat() if s.finished_at else None,
                "output": s.output or {},
            }
            for s in (latest.steps if latest else [])
        ],
        changes=[
            CodeChangeOut(path=c.path, action=c.action, diff=c.diff,
                          reasoning=c.reasoning, iteration=c.iteration)
            for c in (latest.changes if latest else [])
        ],
        test_runs=[
            TestRunOut(
                id=t.id, iteration=t.iteration, command=t.command, exit_code=t.exit_code,
                passed=t.passed, failed=t.failed, errors=t.errors, skipped=t.skipped,
                failed_tests=t.failed_tests or [], duration_ms=t.duration_ms,
                created_at=t.created_at,
            )
            for t in (latest.test_runs if latest else [])
        ],
        security_findings=[
            SecurityFindingOut(
                severity=f.severity, rule_id=f.rule_id, path=f.path, line=f.line,
                message=f.message, source=f.source,
            )
            for f in (latest.security_findings if latest else [])
        ],
        review_findings=[
            ReviewFindingOut(
                severity=f.severity, category=f.category, path=f.path, line=f.line,
                title=f.title, detail=f.detail, source=f.source,
            )
            for f in (latest.review_findings if latest else [])
        ],
        report=(
            ReportOut(id=latest.reports[0].id, format=latest.reports[0].format,
                      content=latest.reports[0].content, created_at=latest.reports[0].created_at)
            if (latest and latest.reports)
            else None
        ),
    )


@router.post("/{task_id}/run", response_model=TaskRunOut)
async def run_task(task_id: str, body: RunRequest, user: CurrentUser, db: DbSession):
    task = (
        await db.execute(select(Task).where(Task.id == task_id).options(selectinload(Task.runs)))
    ).scalar_one_or_none()
    if task is None:
        raise NotFoundError("Task not found")
    project = await db.get(Project, task.project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Task not found")

    active = [r for r in task.runs if r.state not in {s.value for s in TERMINAL_STATES}]
    if active:
        raise ConflictError("Task already has an active run")

    run = TaskRun(task_id=task.id, project_id=project.id, state=TaskState.QUEUED.value)
    db.add(run)
    await db.flush()
    task.status = TaskState.QUEUED.value
    await db.commit()
    audit("task_run_started", user_id=user.id, project_id=project.id, task_id=task.id,
          detail={"run_id": run.id})

    queue = get_queue()
    await queue.enqueue({"run_id": run.id, "task_id": task.id, "project_id": project.id})
    return _run_out(run)


@router.post("/{task_id}/runs/{run_id}/cancel", response_model=CancelResult)
async def cancel_run(task_id: str, run_id: str, user: CurrentUser, db: DbSession):
    run = (
        await db.execute(select(TaskRun).where(TaskRun.id == run_id))
    ).scalar_one_or_none()
    if run is None or run.task_id != task_id:
        raise NotFoundError("Run not found")
    project = await db.get(Project, run.project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Run not found")
    if run.state in {s.value for s in TERMINAL_STATES}:
        return CancelResult(run_id=run.id, state=run.state,
                            message="Run already finished")
    # cooperative cancel: the worker checks this DB flag at step boundaries
    run.cancel_requested = True
    await db.commit()
    await event_bus.publish(f"run:{run.id}", {
        "type": "cancel_requested", "payload": {"run_id": run.id, "seq": -1}
    })
    audit("task_run_cancel_requested", user_id=user.id, task_id=task.id,
          detail={"run_id": run.id})
    return CancelResult(run_id=run.id, state=run.state,
                        message="Cancellation requested; finishing current step")


@router.get("/{task_id}/runs/{run_id}/events", response_model=list[TaskEventOut])
async def get_run_events(task_id: str, run_id: str, user: CurrentUser, db: DbSession,
                         after_seq: int = Query(default=0, ge=0),
                         limit: int = Query(default=500, le=2000)):
    run = (
        await db.execute(select(TaskRun).where(TaskRun.id == run_id))
    ).scalar_one_or_none()
    if run is None or run.task_id != task_id:
        raise NotFoundError("Run not found")
    project = await db.get(Project, run.project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Run not found")
    rows = (
        (
            await db.execute(
                select(TaskEvent)
                .where(TaskEvent.run_id == run_id, TaskEvent.seq > after_seq)
                .order_by(TaskEvent.seq)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        TaskEventOut(seq=e.seq, type=e.type, payload=e.payload or {},
                     created_at=e.created_at)
        for e in rows
    ]


@router.get("/{task_id}/runs/{run_id}/stream")
async def stream_run_events(task_id: str, run_id: str, user: CurrentUser,
                            db: DbSession,
                            after_seq: int = Query(default=0, ge=0)):
    """SSE stream of run events. Replays persisted events, then live-pubs."""
    run = (
        await db.execute(select(TaskRun).where(TaskRun.id == run_id))
    ).scalar_one_or_none()
    if run is None or run.task_id != task_id:
        raise NotFoundError("Run not found")
    project = await db.get(Project, run.project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Run not found")

    channel = f"run:{run_id}"
    terminal_types = {"run_completed", "run_failed", "run_cancelled",
                      "run_timeout", "run_security_blocked"}

    async def _replay(s, after: int) -> list[TaskEvent]:
        return list(
            (
                await s.execute(
                    select(TaskEvent)
                    .where(TaskEvent.run_id == run_id, TaskEvent.seq > after)
                    .order_by(TaskEvent.seq)
                )
            )
            .scalars()
            .all()
        )

    async def _is_terminal(s) -> bool:
        state = (
            await s.execute(select(TaskRun.state).where(TaskRun.id == run_id))
        ).scalar_one()
        return state in {s2.value for s2 in TERMINAL_STATES}

    async def gen():
        factory = get_session_factory()
        next_seq = after_seq
        # 1) replay persisted events
        async with factory() as s:
            for e in await _replay(s, next_seq):
                yield f"event: {e.type}\ndata: {json.dumps({'seq': e.seq, **e.payload}, default=str)}\n\n"
                next_seq = e.seq
            if await _is_terminal(s):
                return

        # 2) live: consume the bus, with a DB poll as the safety net so a
        #    missed event can never hang the stream
        q = event_bus.subscribe(channel)
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=3)
                except asyncio.TimeoutError:
                    async with factory() as s:
                        if await _is_terminal(s):
                            # drain any persisted events we missed
                            for e in await _replay(s, next_seq):
                                yield f"event: {e.type}\ndata: {json.dumps({'seq': e.seq, **e.payload}, default=str)}\n\n"
                                next_seq = e.seq
                            return
                    yield ": keepalive\n\n"
                    continue
                seq = msg.get("seq", next_seq + 1)
                next_seq = max(next_seq, seq)
                yield f"event: {msg.get('type', 'event')}\ndata: {json.dumps(msg, default=str)}\n\n"
                if msg.get("type") in terminal_types:
                    return
        finally:
            event_bus.unsubscribe(channel, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{task_id}/runs/{run_id}/diff", response_model=DiffOut)
async def get_run_diff(task_id: str, run_id: str, user: CurrentUser, db: DbSession):
    run = (
        await db.execute(select(TaskRun).where(TaskRun.id == run_id))
    ).scalar_one_or_none()
    if run is None or run.task_id != task_id:
        raise NotFoundError("Run not found")
    project = await db.get(Project, run.project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Run not found")
    changes = (
        (
            await db.execute(
                select(CodeChange).where(CodeChange.run_id == run_id).order_by(CodeChange.path)
            )
        )
        .scalars()
        .all()
    )
    return DiffOut(
        run_id=run_id,
        changed_files=len(changes),
        files=[
            {"path": c.path, "action": c.action, "diff": c.diff, "reasoning": c.reasoning}
            for c in changes
        ],
    )


@router.get("/{task_id}/runs/{run_id}/report", response_model=ReportOut)
async def get_run_report(task_id: str, run_id: str, user: CurrentUser, db: DbSession):
    run = (
        await db.execute(select(TaskRun).where(TaskRun.id == run_id))
    ).scalar_one_or_none()
    if run is None or run.task_id != task_id:
        raise NotFoundError("Run not found")
    project = await db.get(Project, run.project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Run not found")
    report = (
        (
            await db.execute(
                select(Report).where(Report.run_id == run_id).order_by(Report.created_at.desc()).limit(1)
            )
        )
        .scalars()
        .first()
    )
    if report is None:
        raise NotFoundError("Report not available yet")
    return ReportOut(id=report.id, format=report.format, content=report.content,
                     created_at=report.created_at)


@router.get("/{task_id}/runs/{run_id}/report/pdf")
async def get_run_report_pdf(task_id: str, run_id: str, user: CurrentUser, db: DbSession):
    run = (
        await db.execute(select(TaskRun).where(TaskRun.id == run_id))
    ).scalar_one_or_none()
    if run is None or run.task_id != task_id:
        raise NotFoundError("Run not found")
    project = await db.get(Project, run.project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Run not found")
    report = (
        (
            await db.execute(
                select(Report).where(Report.run_id == run_id).order_by(Report.created_at.desc()).limit(1)
            )
        )
        .scalars()
        .first()
    )
    if report is None:
        raise NotFoundError("Report not available yet")
    from fastapi.responses import FileResponse

    if report.content_path:
        return FileResponse(report.content_path, media_type="application/pdf",
                            filename=f"nexusforge-report-{run_id[:8]}.pdf")
    # generate on demand
    from services.reporting.report import build_report_pdf

    path = await build_report_pdf(run_id, report.content)
    report.content_path = path
    await db.commit()
    return FileResponse(path, media_type="application/pdf",
                        filename=f"nexusforge-report-{run_id[:8]}.pdf")


@router.get("/{task_id}/runs", response_model=list[TaskRunSummary])
async def list_task_runs(task_id: str, user: CurrentUser, db: DbSession):
    task = (
        await db.execute(select(Task).where(Task.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        raise NotFoundError("Task not found")
    project = await db.get(Project, task.project_id)
    if project is None or project.owner_id != user.id:
        raise NotFoundError("Task not found")
    rows = (
        (
            await db.execute(
                select(TaskRun).where(TaskRun.task_id == task_id).order_by(TaskRun.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        TaskRunSummary(id=r.id, state=r.state, iteration=r.iteration,
                       started_at=r.started_at, finished_at=r.finished_at, error=r.error)
        for r in rows
    ]