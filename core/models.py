"""Relational schema.

Conventions:
- UUID string primary keys (portable across SQLite/Postgres).
- `created_at`/`updated_at` maintained by the ORM.
- Run-scoped rows cascade from TaskRun.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskState(str, enum.Enum):
    IDLE = "IDLE"
    QUEUED = "QUEUED"
    ANALYZING = "ANALYZING"
    PLANNING = "PLANNING"
    IMPLEMENTING = "IMPLEMENTING"
    TESTING = "TESTING"
    DEBUGGING = "DEBUGGING"
    RETESTING = "RETESTING"
    SECURITY_ANALYSIS = "SECURITY_ANALYSIS"
    CODE_REVIEW = "CODE_REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    SECURITY_BLOCKED = "SECURITY_BLOCKED"


TERMINAL_STATES = {
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
    TaskState.TIMEOUT,
    TaskState.SECURITY_BLOCKED,
}


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    projects: Mapped[list["Project"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user", "user_id", "expires_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_owner_created", "owner_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active")  # active|archived
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    owner: Mapped["User"] = relationship(back_populates="projects")
    repositories: Mapped[list["Repository"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Repository(Base):
    """An imported repository: a git working copy under the project workspace."""

    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_repo_project_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    source_kind: Mapped[str] = mapped_column(String(32), default="local")  # local|upload
    source_ref: Mapped[str] = mapped_column(String(500), default="")  # path or origin url
    workspace_path: Mapped[str] = mapped_column(String(500))  # absolute, inside workspace root
    default_branch: Mapped[str] = mapped_column(String(120), default="main")
    head_commit: Mapped[str] = mapped_column(String(64), default="")
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    index_version: Mapped[int] = mapped_column(Integer, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    languages: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    project: Mapped["Project"] = relationship(back_populates="repositories")
    files: Mapped[list["IndexedFile"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["FileChunk"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class IndexedFile(Base):
    __tablename__ = "indexed_files"
    __table_args__ = (
        UniqueConstraint("repository_id", "path", name="uq_indexed_file_path"),
        Index("ix_indexed_file_repo_lang", "repository_id", "language"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(String(600))
    language: Mapped[str] = mapped_column(String(40), default="unknown")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    symbols: Mapped[list] = mapped_column(JSON, default=list)  # [{name, kind, line, end_line}]
    imports: Mapped[list] = mapped_column(JSON, default=list)  # module names
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    secret_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repository: Mapped["Repository"] = relationship(back_populates="files")


class FileChunk(Base):
    __tablename__ = "file_chunks"
    __table_args__ = (
        Index("ix_chunk_repo_score", "repository_id", "seq"),
        Index("ix_chunk_repo_path", "repository_id", "path"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(String(600))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    start_line: Mapped[int] = mapped_column(Integer, default=0)
    end_line: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list] = mapped_column(JSON, default=list)  # local vectorizer output
    symbols: Mapped[list] = mapped_column(JSON, default=list)  # symbol names in chunk

    repository: Mapped["Repository"] = relationship(back_populates="chunks")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_project_created", "project_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    repository_id: Mapped[str | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300))
    requirement: Mapped[str] = mapped_column(Text)  # the user's natural-language goal
    status: Mapped[str] = mapped_column(String(32), default=TaskState.IDLE.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    project: Mapped["Project"] = relationship(back_populates="tasks")
    runs: Mapped[list["TaskRun"]] = relationship(
        back_populates="task", cascade="all, delete-orphan",
        order_by="TaskRun.created_at",
    )


class TaskRun(Base):
    """One execution of a task through the agent pipeline (checkpointed)."""

    __tablename__ = "task_runs"
    __table_args__ = (Index("ix_task_runs_task_created", "task_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    state: Mapped[str] = mapped_column(String(32), default=TaskState.IDLE.value, index=True)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    cost_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    task: Mapped["Task"] = relationship(back_populates="runs")
    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentStep.seq"
    )
    events: Mapped[list["TaskEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="TaskEvent.seq"
    )
    changes: Mapped[list["CodeChange"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    test_runs: Mapped[list["TestRun"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    executions: Mapped[list["Execution"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    security_findings: Mapped[list["SecurityFinding"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    review_findings: Mapped[list["ReviewFinding"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    reports: Mapped[list["Report"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AgentStep(Base):
    """Checkpoint for one agent stage of a run. `output` holds structured results."""

    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_step_run_seq"),
        Index("ix_step_run_state", "run_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    agent: Mapped[str] = mapped_column(String(64))  # e.g. "planner"
    state: Mapped[str] = mapped_column(String(32))  # machine state at this step
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|running|completed|failed|skipped
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")  # concise engineering summary
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["TaskRun"] = relationship(back_populates="steps")


class TaskEvent(Base):
    """Append-only event log; the source of truth for SSE replay & terminal view."""

    __tablename__ = "task_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_event_run_seq"),
        Index("ix_event_run_type", "run_id", "type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(48))  # state_change|terminal|tool_call|...
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run: Mapped["TaskRun"] = relationship(back_populates="events")


class CodeChange(Base):
    __tablename__ = "code_changes"
    __table_args__ = (
        UniqueConstraint("run_id", "path", name="uq_change_run_path"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String(600))
    action: Mapped[str] = mapped_column(String(16))  # created|modified|deleted
    before_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    iteration: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped["TaskRun"] = relationship(back_populates="changes")


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id", ondelete="CASCADE"))
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    command: Mapped[str] = mapped_column(String(500))
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    failed_tests: Mapped[list] = mapped_column(JSON, default=list)
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run: Mapped["TaskRun"] = relationship(back_populates="test_runs")


class Execution(Base):
    """Any sandboxed command execution (tests, linters, builds, scans)."""

    __tablename__ = "executions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="test")  # test|lint|build|scan|custom
    command: Mapped[str] = mapped_column(String(800))
    backend: Mapped[str] = mapped_column(String(16), default="process")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run: Mapped["TaskRun | None"] = relationship(back_populates="executions")


class SecurityFinding(Base):
    __tablename__ = "security_findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id", ondelete="CASCADE"))
    severity: Mapped[str] = mapped_column(String(16))  # critical|high|medium|low|info
    rule_id: Mapped[str] = mapped_column(String(80))
    path: Mapped[str] = mapped_column(String(600), default="")
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="builtin")  # builtin|bandit|semgrep|llm
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run: Mapped["TaskRun"] = relationship(back_populates="security_findings")


class ReviewFinding(Base):
    __tablename__ = "review_findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id", ondelete="CASCADE"))
    severity: Mapped[str] = mapped_column(String(16))  # critical|high|medium|low|info
    category: Mapped[str] = mapped_column(String(40))  # correctness|maintainability|...
    path: Mapped[str] = mapped_column(String(600), default="")
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="reviewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run: Mapped["TaskRun"] = relationship(back_populates="review_findings")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.id", ondelete="CASCADE"))
    format: Mapped[str] = mapped_column(String(16), default="markdown")  # markdown|pdf
    content: Mapped[str] = mapped_column(Text)  # markdown text (PDF stored as path)
    content_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run: Mapped["TaskRun"] = relationship(back_populates="reports")