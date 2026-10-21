"""Pydantic API schemas (request/response contracts)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator


# --- auth ---
class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=200)

    @field_validator("password")
    @classmethod
    def _strong(cls, v: str) -> str:
        if not any(c.islower() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("password must contain lowercase letters and digits")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class UserOut(BaseModel):
    id: str
    email: EmailStr
    username: str
    is_active: bool
    created_at: datetime


class MeUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64,
                                 pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str | None = Field(default=None, min_length=8, max_length=200)


# --- projects ---
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime
    repository_count: int = 0
    task_count: int = 0
    latest_task_state: str | None = None


class RepositoryImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_kind: str = Field(default="local", pattern=r"^(local|upload)$")
    source_ref: str = Field(default="", max_length=500)  # local path or origin URL
    index: bool = True


class RepositoryOut(BaseModel):
    id: str
    name: str
    source_kind: str
    source_ref: str
    default_branch: str
    head_commit: str
    indexed: bool
    index_version: int
    file_count: int
    total_bytes: int
    languages: dict[str, int]
    created_at: datetime


# --- tasks ---
class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    requirement: str = Field(min_length=3, max_length=20000)
    repository_id: str | None = None


class TaskOut(BaseModel):
    id: str
    project_id: str
    repository_id: str | None
    title: str
    requirement: str
    status: str
    created_at: datetime
    updated_at: datetime
    latest_run_id: str | None = None


class TaskRunOut(BaseModel):
    id: str
    task_id: str
    project_id: str
    state: str
    iteration: int
    llm_calls: int
    tokens_used: int
    cost_estimate: float
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    created_at: datetime


class TaskEventOut(BaseModel):
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


class CodeChangeOut(BaseModel):
    path: str
    action: str
    diff: str
    reasoning: str
    iteration: int


class TestRunOut(BaseModel):
    id: str
    iteration: int
    command: str
    exit_code: int | None
    passed: int
    failed: int
    errors: int
    skipped: int
    failed_tests: list[str]
    duration_ms: int
    created_at: datetime


class SecurityFindingOut(BaseModel):
    severity: str
    rule_id: str
    path: str
    line: int | None
    message: str
    source: str


class ReviewFindingOut(BaseModel):
    severity: str
    category: str
    path: str
    line: int | None
    title: str
    detail: str
    source: str


class ReportOut(BaseModel):
    id: str
    format: str
    content: str
    created_at: datetime


class TaskDetail(BaseModel):
    task: TaskOut
    runs: list[TaskRunOut]
    latest_run: TaskRunOut | None = None
    steps: list[dict[str, Any]] = []
    changes: list[CodeChangeOut] = []
    test_runs: list[TestRunOut] = []
    security_findings: list[SecurityFindingOut] = []
    review_findings: list[ReviewFindingOut] = []
    report: ReportOut | None = None


class TaskRunSummary(BaseModel):
    id: str
    state: str
    iteration: int
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None


class DiffFile(BaseModel):
    path: str
    action: str
    diff: str
    reasoning: str


class DiffOut(BaseModel):
    run_id: str
    changed_files: int
    files: list[DiffFile]


class CancelResult(BaseModel):
    run_id: str
    state: str
    message: str


class RunRequest(BaseModel):
    resume: bool = False


class HealthOut(BaseModel):
    status: str
    version: str
    database: str
    queue: str
    sandbox: str
    llm_provider: str


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int