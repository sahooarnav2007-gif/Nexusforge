# NexusForge — Architecture & Implementation Roadmap

NexusForge is an autonomous AI software-engineering platform. Given a repository and a
natural-language requirement, it analyzes the codebase, plans, implements, generates
tests, executes them in a sandbox, diagnoses failures, repairs code, scans for
security issues, reviews the change, and produces an engineering report — with every
step observable in real time.

This document is the single source of truth for architectural decisions. It is written
*before* implementation (Phase 1) and updated as decisions are made.

---

## 1. Requirements analysis (condensed)

| # | Capability | Design consequence |
|---|------------|--------------------|
| R1 | Understand a repository | Indexing pipeline: traversal, language detection, ignore rules, AST symbol extraction, chunking, hybrid retrieval (BM25 + local embeddings + metadata filters + RRF rerank) |
| R2 | Plan / implement / test / repair | Explicit agent state machine with per-step structured I/O, tool permissions, and budgets |
| R3 | Never one giant LLM call | Per-agent context builder selects *relevant* chunks; token budget enforced per call |
| R4 | Sandboxed execution | Sandbox abstraction: Docker backend (preferred) + rlimit process backend (fallback); CPU/mem/timeout/net/process limits |
| R5 | Provider-agnostic LLM | `LLMProvider` ABC; OpenAI-compatible, Anthropic, Google (Gemini REST), and a **deterministic local provider** for offline/dev/CI |
| R6 | Real-time UI | SSE stream of persisted task events (replay-safe), no polling |
| R7 | Durable jobs | TaskRun persisted in DB; in-process worker queue (dev) or Redis-backed queue (prod); crash recovery via step checkpoints |
| R8 | Security first | Argon2id passwords, JWT + rotating refresh tokens, per-user authorization, rate limiting, secure headers, path-traversal-safe file access, command allowlists, secret redaction in logs |
| R9 | Observability | Structured JSON logs with request/task correlation, Prometheus `/metrics`, `/health` + `/ready` |
| R10 | Honest behavior | No faked output. The local LLM provider is a real (limited) rule-based model; when it cannot solve a task it fails the task with a clear diagnosis instead of pretending success |

### Non-negotiables honored
- No hard-coded AI responses: the deterministic provider is a *general* structured-task
  interpreter (it parses the agent's structured context and synthesizes plans/patches/tests
  from the code it is given). It is documented as a limited local model, not a fake of a
  remote LLM.
- No fake terminal/test output: every execution result comes from a real subprocess or
  container run.
- No auto-push: Git push exists only behind an explicit user-confirmed API call and is
  disabled by default.

---

## 2. Technology selection

| Layer | Choice | Why |
|-------|--------|-----|
| API | Python 3.11, FastAPI, Pydantic v2 | Async, typed, self-documenting OpenAPI, first-class validation |
| ORM / DB | SQLAlchemy 2.0 (async), Alembic | Mature; async drivers for both SQLite (dev) and PostgreSQL (prod) |
| Dev DB | SQLite via aiosqlite | Zero-setup local run; same ORM code path |
| Prod DB | PostgreSQL 16 (asyncpg) | Required target; docker-compose provided |
| Jobs | In-process asyncio worker queue (dev) / Redis list queue (prod) | Same `JobQueue` ABC; avoids Celery's operational weight for a local-first platform while keeping the worker process separable |
| Cache / events | Redis (optional) | Pub/sub for multi-worker event fan-out; in-process pub/sub fallback |
| Sandbox | Docker (preferred) → rlimit process sandbox (fallback) | Real isolation where available; documented degradation otherwise |
| LLM | Provider ABC + OpenAI-compatible / Anthropic / Gemini REST / deterministic local | No single-provider lock-in; offline-capable |
| Embeddings | Local deterministic hash-vectorizer (default) + optional OpenAI embeddings | Works offline; swappable |
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind CSS | Fast, typed, SSR for auth pages, static for the app shell |
| Real-time | SSE (server-sent events) | Simpler than WS for one-way agent/terminal streams; replay-safe from DB |
| Frontend tests | Vitest + React Testing Library; Playwright for E2E | Standard, fast |
| Python tests | pytest + pytest-asyncio + httpx (ASGI transport) | Covers unit → API → e2e |
| Lint/format | ruff | One fast tool for lint + format |
| CI | GitHub Actions | Lint, type-adjacent checks, unit/integration tests, frontend build, image build |
| Packaging | Dockerfile per app + docker-compose (api, web, postgres, redis, worker) | One-command local deploy |

### Why not X
- **Celery**: heavyweight (broker+result backend+pooling) for a platform whose default
  deployment is a single machine; the `JobQueue` ABC keeps the door open.
- **WebSockets**: the stream is server→client only (events, terminal output); SSE is
  simpler, survives proxying, and replays from the event log.
- **tree-sitter for all languages**: adds native deps; Python uses the stdlib `ast`,
  other languages use a conservative regex symbol extractor. Documented limitation.
- **Monorepo `packages/shared`**: the Python backend is a single distribution; shared
  types live in `core/schemas.py` (Pydantic) which the frontend mirrors in TS. A separate
  Python package adds tooling cost with no boundary it protects.

---

## 3. System architecture

```
                        ┌────────────────────────────────────────────┐
                        │                Next.js web                 │
                        │  (projects, runs, terminal, diff, report)  │
                        └───────────────┬───────────────┬────────────┘
                              REST/JSON │               │ SSE (task events)
                        ┌───────────────▼───────────────▼────────────┐
                        │            FastAPI application             │
                        │  auth · projects · tasks · reports · sys   │
                        │  rate limiting · secure headers · metrics  │
                        └──────┬───────────────────────────┬─────────┘
                               │ SQL (async)               │ enqueue
                        ┌──────▼──────┐             ┌──────▼─────────────┐
                        │  PostgreSQL  │             │   JobQueue (ABC)   │
                        │  / SQLite    │             │ in-proc | Redis    │
                        └─────────────┘             └──────┬─────────────┘
                                                           │ TaskRun
                        ┌──────────────────────────────────▼──────────────────┐
                        │                 Agent Engine (worker)               │
                        │  RequirementAnalyzer → RepoAnalyst → Planner →      │
                        │  Implementer → TestGenerator → ExecutionAgent →     │
                        │  FailureAnalyzer ⇄ RepairAgent → SecurityAgent →    │
                        │  CodeReviewer → Report                              │
                        │  (explicit state machine, persisted per step)       │
                        └──┬──────────┬──────────┬──────────┬─────────────────┘
                           │          │          │          │
                    ┌──────▼───┐ ┌────▼─────┐ ┌──▼───────┐ ┌▼──────────────┐
                    │ LLM      │ │ Tool     │ │ Sandbox  │ │ Repo knowledge│
                    │ Provider │ │ Registry │ │ (docker/ │ │ (index, AST,  │
                    │ (4 impls)│ │ (15 tools)│ │ rlimit)  │ │ hybrid search)│
                    └──────────┘ └──────────┘ └──────────┘ └───────────────┘
```

### 3.1 Module map (monorepo)

```
nexusforge/
├── apps/
│   ├── api/            # FastAPI entrypoint (thin: builds the app, runs uvicorn)
│   └── web/            # Next.js frontend
├── services/
│   ├── agent/          # state machine + per-role agents + context builder
│   ├── llm/            # provider ABC + openai-compat / anthropic / gemini / local
│   ├── tools/          # tool registry + repo/edit/exec/git tools (validated, logged)
│   ├── sandbox/        # sandbox ABC + docker backend + rlimit process backend
│   ├── indexing/       # ingestion, languages, AST, chunking, embeddings, search
│   ├── security/       # built-in rules + external tool integration
│   ├── review/         # structured code review of the diff
│   ├── gitops/         # safe git wrapper (subprocess, no shell)
│   └── reporting/      # engineering report (MD + minimal PDF writer)
├── core/               # config, db, models, schemas, security, events, queue,
│                       # metrics, logging, errors  (no business logic)
├── api/                # FastAPI app factory + routers + deps
├── demo/repo/          # intentionally incomplete demo application
├── tests/              # unit / integration / e2e
├── scripts/            # demo runner, seed data
├── infrastructure/     # docker, monitoring config
├── docker-compose.yml
└── docs via root *.md  # README, ARCHITECTURE, SECURITY, DEVELOPMENT, API, AGENTS, DEMO
```

### 3.2 Data model (relational)

`User, Project, Repository, Task, TaskRun, AgentStep, IndexedFile, FileChunk,
CodeChange, TestRun, SecurityFinding, ReviewFinding, Execution, Report,
TaskEvent, RefreshToken`

Key invariants:
- Every `TaskRun` belongs to one `Task` (requirement) on one `Project`.
- `AgentStep` rows are the checkpoint log: step name, status, structured I/O (JSON),
  token/cost usage, duration. Crash recovery resumes after the last completed step.
- `TaskEvent` is an append-only event log (type, payload JSON, seq) — the source of
  truth for SSE replay and the terminal view.
- `CodeChange` stores before/after content + unified diff per file per run.
- FKs with `ON DELETE CASCADE` for run-scoped rows; indexed `(project_id, created_at)`,
  `(run_id, seq)`, etc.

### 3.3 Agent state machine

```
IDLE → ANALYZING → PLANNING → IMPLEMENTING → TESTING → (pass) → SECURITY_ANALYSIS
                                                        → (fail, budget left) → DEBUGGING
DEBUGGING → RETESTING → TESTING ... (loop, bounded by MAX_ITERATIONS/TIME/TOKENS)
SECURITY_ANALYSIS → (critical finding) → SECURITY_BLOCKED
SECURITY_ANALYSIS → (ok) → CODE_REVIEW → COMPLETED
any state → CANCELLED (user) | FAILED (error) | TIMEOUT (budget)
```

Transitions are validated in `services/agent/states.py` (illegal transitions raise).
State is persisted on every transition; the worker re-reads it on startup.

### 3.4 Agent design

Each agent is a class with:
- `name`, `state` (the machine state it drives)
- `permissions: set[ToolName]` — the tool registry enforces this per call
- `run(ctx: AgentContext) -> AgentResult` — structured in/out (Pydantic)
- validation of its output schema; malformed output → structured error, retry budget

Agents: `RequirementAnalyzer` (parse goal into a structured spec), `RepoAnalyst`
(builds the knowledge summary via retrieval), `Planner` (ordered step list with
acceptance criteria), `Implementer` (emits patch operations via tools),
`TestGenerator` (emits test files following repo conventions), `ExecutionAgent`
(runs the test command in the sandbox, streams output), `FailureAnalyzer`
(structured diagnosis from pytest output), `RepairAgent` (patch from diagnosis),
`SecurityAgent` (rules + external tools), `CodeReviewer` (structured findings),
`ReportWriter` (assembles the engineering report).

The LLM is called with: system prompt (role + output contract), a *retrieved* context
block (bounded by token budget), and the tool schema for its permissions. Responses are
validated against the step's output schema; tool calls are executed by the registry and
results fed back (bounded rounds).

### 3.5 Local (deterministic) LLM provider

`LocalProvider` implements the same `LLMProvider` interface. It is a real, general
rule-based model:
- **plan**: derives steps from the structured spec + repo summary (endpoint gaps,
  failing tests, missing symbols).
- **patch**: pattern-based Python synthesizer — implements stub functions
  (`NotImplementedError` / `501`) from their signature + docstring contract, and
  wires missing FastAPI endpoints to service methods.
- **tests**: generates pytest tests from the function contract (docstring bullets) and
  repo test conventions.
- **diagnose**: parses pytest tracebacks → root-cause + fix strategy.
- **review**: heuristic findings over the diff.

It is *limited by design* (documented): it handles the patterns above and refuses
anything else with a clear "unsupported by local model" failure. This keeps the whole
platform runnable and testable offline while the remote providers do the heavy lifting
in production.

### 3.6 Sandbox

`Sandbox.run(argv, workdir, env, timeout, limits) -> ExecutionResult`
- **DockerSandbox**: `docker run --rm --network none --memory <m> --cpus <c>
  --pids-limit <n> -v <workspace>:/work:rw image <argv>`; image configurable
  (default `python:3.11-slim` with project deps installed on first use).
- **ProcessSandbox**: `subprocess` in a new session/process group; `preexec_fn`
  applies `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_NPROC`, `RLIMIT_FSIZE`; best-effort
  network namespace via `unshare -n` when permitted; env scrubbed (no host secrets);
  cwd confined to the project workspace; hard timeout → SIGKILL of the group.
- Selection: `NEXUSFORGE_SANDBOX_BACKEND=docker|process|auto` (auto = docker if the CLI
  answers, else process). The worker never executes AI-generated commands on the host
  outside these backends.

### 3.7 Retrieval (code understanding)

Pipeline: ingest → per-file metadata (language, size, hash, symbols) → chunking
(symbol-aware for Python, line-window fallback) → embed (local hash-vectorizer:
token n-grams → L2-normalized 512-d vectors; deterministic, offline) → index
(in-memory per project, persisted as JSONL for restart) → query:
1. metadata filter (language/path globs from the spec)
2. BM25 over chunk text
3. cosine similarity over embeddings
4. Reciprocal-Rank Fusion of both rankings
5. dependency-aware boost: files importing/defined-by already-selected files
6. budget trim to the agent's token limit, ordered by fused score

### 3.8 Security architecture (summary — see SECURITY.md)

- Argon2id (memory 64 MiB, t=3, p=4) password hashing; no plaintext ever stored.
- JWT access tokens (15 min) + rotating refresh tokens (30 d, hashed at rest, single-use).
- Per-user ownership checks on every project-scoped route (404 for foreign projects).
- Sliding-window rate limit per user/IP (auth endpoints stricter).
- Secure headers (CSP, HSTS, nosniff, frame-ancestors) + locked CORS (web origin).
- File access: all tool paths resolved and confined to the project workspace
  (`realpath` + prefix check) — path traversal is structurally impossible.
- Commands: sandbox backends only; argv is a list (never a shell string); env scrubbed.
- Secret scanning on ingest: files matching secret patterns are excluded from the
  index and flagged, never sent to an LLM.
- Audit log: auth events, task lifecycle, tool calls, sandbox runs (JSON lines).

### 3.9 Failure recovery

- Worker startup: runs in non-terminal state → resume from last completed `AgentStep`
  (workspace is durable on disk); if workspace missing → mark `FAILED(reset_required)`.
- LLM errors: retry with exponential backoff (bounded), then structured failure.
- Sandbox timeout: process group killed, run marked with `TIMEOUT` on the step,
  task continues to failure analysis with the captured partial output.
- DB errors: request-level 503 with correlation id; worker marks run `FAILED`.
- Cancel: cooperative flag checked at every step boundary + tool round.

### 3.10 Scaling strategy

The API is stateless (auth via JWT, events replayed from DB) → horizontal scale behind
a load balancer. Workers consume from the Redis queue → scale workers independently;
sandbox backends can be swapped to remote container workers by implementing
`Sandbox.run` over a gRPC/HTTP boundary (interface already isolated). Index is
per-project and cacheable in Redis/Postgres in a future phase.

---

## 4. Implementation roadmap (phases)

| Phase | Deliverable | Exit criteria |
|-------|-------------|---------------|
| 1 | This document + repo skeleton | Roadmap agreed |
| 2 | `core/` (config, db, models, logging, metrics, errors, events, queue) + Alembic | Migrations run on SQLite & Postgres DDL |
| 3 | Auth + projects + API app + rate limit + secure headers | Register/login/CRUD pass API tests |
| 4 | Ingestion + indexing + retrieval + secret guard | Demo repo indexed; retrieval returns relevant chunks; secret file excluded |
| 5 | LLM providers + tool registry + agent engine skeleton | Local provider drives a 3-step mini-task; tools validated/limited |
| 6 | Sandbox backends + execution agent | Timeout/limit tests pass on process backend; docker path unit-tested via mock |
| 7 | Full loop (implement→test→diagnose→repair) + demo repo | Demo task completes end-to-end offline, including a real failure→repair cycle |
| 8 | Security engine + review + reports (MD/PDF) + Git | Scanner finds planted findings; report generated; git diff/commit work |
| 9 | Frontend: auth, dashboard, run view (timeline, terminal, diff, tests, security, report), SSE | Component tests pass; E2E login→run→report |
| 10 | Observability polish, CI, Docker compose, docs | `docker compose up` works; CI green |
| 11 | Full test pass, fix everything | All suites green, no fake functionality |
| 12 | Security/architecture/UX audit | Audit checklist closed |
| 13 | Release: tags, final docs, demo script | Final response delivered |