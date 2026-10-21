# ⚒️ NexusForge

### Autonomous AI Software Engineering Platform

**NexusForge turns natural-language software requirements into tested, secured, reviewed, production-ready code changes.**

Give NexusForge a repository and tell it what you want.

It **understands the codebase → analyzes the requirement → plans the implementation → writes the code → generates tests → executes them in an isolated sandbox → diagnoses failures → repairs the implementation → scans for security issues → reviews the final diff → generates an engineering report.**

And unlike a black-box coding agent, **every stage is observable in real time.**

---

## 🚀 Why NexusForge?

Modern AI coding assistants can generate code.

**NexusForge is designed to engineer software.**

Instead of stopping after generating a patch, NexusForge operates as a complete autonomous engineering pipeline:

```text
Natural-Language Requirement
          │
          ▼
   🔍 Understand
          │
          ▼
   🧠 Analyze Repository
          │
          ▼
   📋 Create Implementation Plan
          │
          ▼
   💻 Implement Changes
          │
          ▼
   🧪 Generate & Run Tests
          │
          ▼
   ❌ Failure?
      ┌───┴───┐
     YES      NO
      │        │
      ▼        ▼
   🔧 Repair  🔐 Security Analysis
      │        │
      └──►─────┘
               │
               ▼
          👀 Code Review
               │
               ▼
          📊 Engineering Report
```

The result is not simply *"AI-generated code."*

It is a **traceable engineering workflow with execution, validation, security analysis, recovery, and review built into the loop.**

---

# ✨ Core Capabilities

| Capability                 | What NexusForge Does                                                           |
| -------------------------- | ----------------------------------------------------------------------------- |
| 🧠 Requirement Analysis    | Converts natural-language requirements into structured specifications         |
| 🔍 Repository Intelligence | Understands repositories using AST-aware hybrid retrieval                     |
| 📋 Autonomous Planning     | Produces ordered implementation steps and acceptance criteria                 |
| 💻 Code Implementation     | Modifies repositories through a controlled tool registry                      |
| 🧪 Test Generation         | Generates tests following repository conventions                              |
| ▶️ Real Execution          | Runs commands inside an isolated sandbox                                      |
| 🔧 Autonomous Repair       | Diagnoses failures and iteratively repairs implementations                    |
| 🔐 Security Analysis       | Scans generated changes for security issues                                   |
| 👀 Code Review             | Performs structured review of the resulting diff                              |
| 📡 Live Observability      | Streams task events through Server-Sent Events                                |
| 📊 Engineering Reports     | Produces structured Markdown and PDF reports                                  |
| 💾 Crash Recovery          | Persists state and resumes from completed checkpoints                         |
| 🤖 Provider Agnostic       | Supports local, OpenAI-compatible, Anthropic, and Gemini providers            |
| 🛡️ Secure by Default      | Sandboxing, path confinement, secret hygiene, rate limiting and audit logging |

---

# 🏗️ Architecture

NexusForge is built around a **single Python 3.11 + FastAPI backend** with an autonomous agent engine running inside the service.

The queue can operate in-process for simplicity or use Redis when `NEXUSFORGE_REDIS_URL` is configured.

```text
                         ┌─────────────────────────────────────┐
                         │             Next.js Web              │
                         │                                     │
                         │  Projects · Runs · Terminal · Diff  │
                         │             · Reports               │
                         └────────────────┬────────────────────┘
                                          │
                           REST / JSON     │     SSE
                                          │
                         ┌────────────────▼────────────────────┐
                         │         FastAPI Application         │
                         │                                     │
                         │ Auth · Projects · Tasks · Reports  │
                         │ Rate Limiting · Security · Metrics │
                         └───────────────┬───────────┬─────────┘
                                         │           │
                                  SQL    │           │ Enqueue
                                         │           │
                           ┌─────────────▼───┐   ┌───▼──────────────┐
                           │   PostgreSQL    │   │    Job Queue     │
                           │    / SQLite     │   │ In-Process/Redis │
                           └─────────────────┘   └───────┬──────────┘
                                                         │
                                                         │ TaskRun
                                                         ▼
                    ┌──────────────────────────────────────────────────┐
                    │                 AGENT ENGINE                     │
                    │                                                  │
                    │ RequirementAnalyzer                              │
                    │          ↓                                       │
                    │ RepoAnalyst                                      │
                    │          ↓                                       │
                    │ Planner                                           │
                    │          ↓                                       │
                    │ Implementer                                       │
                    │          ↓                                       │
                    │ TestGenerator                                     │
                    │          ↓                                       │
                    │ ExecutionAgent                                    │
                    │          ↓                                       │
                    │ FailureAnalyzer ↔ RepairAgent                     │
                    │          ↓                                       │
                    │ SecurityAgent                                     │
                    │          ↓                                       │
                    │ CodeReviewer                                      │
                    │          ↓                                       │
                    │ ReportWriter                                      │
                    │                                                  │
                    │   Explicit persisted state machine               │
                    └───────┬───────────┬───────────┬──────────────────┘
                            │           │           │
                    ┌───────▼────┐ ┌────▼─────┐ ┌──▼────────────────┐
                    │ LLM        │ │ Tool     │ │ Secure Sandbox    │
                    │ Providers  │ │ Registry │ │ Docker / Process  │
                    └────────────┘ └──────────┘ └───────────────────┘
                            │
                    ┌───────▼────────────────────┐
                    │   Repository Intelligence   │
                    │                            │
                    │ AST · Chunking · Indexing  │
                    │ BM25 · Vector Search      │
                    │ Dependency Boosting       │
                    └────────────────────────────┘
```

### Design Principles

NexusForge is intentionally designed around several principles:

**1. Observe everything**

Every major agent step produces persisted events that can be streamed to the frontend.

**2. Execute, don't assume**

Tests and commands are actually executed. NexusForge does not treat generated code as successful until execution validates it.

**3. Fail honestly**

When the system cannot safely or reliably perform an operation, it fails with a structured error instead of fabricating success.

**4. Constrain autonomous actions**

Agents operate through permissions, validated tools, sandboxed execution, resource limits and workspace confinement.

**5. Recover from failure**

Agent state and completed steps are persisted so interrupted runs can resume from checkpoints.

---

# 🔄 The Autonomous Agent State Machine

Every NexusForge run is controlled by an explicit persisted state machine.

```text
                         ┌──────────┐
                         │   IDLE   │
                         └────┬─────┘
                              │
                              ▼
                         ┌──────────┐
                         │  QUEUED  │
                         └────┬─────┘
                              │
                              ▼
                       ┌──────────────┐
                       │   ANALYZING  │
                       └──────┬───────┘
                              │
                              ▼
                       ┌──────────────┐
                       │   PLANNING   │
                       └──────┬───────┘
                              │
                              ▼
                       ┌──────────────┐
                       │ IMPLEMENTING │
                       └──────┬───────┘
                              │
                              ▼
                         ┌──────────┐
                         │ TESTING  │
                         └────┬─────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                  PASS                 FAIL
                    │                   │
                    ▼                   ▼
            ┌───────────────┐    ┌─────────────┐
            │    SECURITY   │    │  DEBUGGING  │
            │    ANALYSIS   │    └──────┬──────┘
            └───────┬───────┘           │
                    │                   ▼
                    │             ┌────────────┐
                    │             │ RETESTING  │
                    │             └─────┬──────┘
                    │                   │
                    │                   └──────► TESTING
                    │
          ┌─────────┴──────────┐
          │                    │
       CRITICAL               OK
          │                    │
          ▼                    ▼
 ┌─────────────────┐    ┌──────────────┐
 │ SECURITY_BLOCKED│    │ CODE_REVIEW  │
 └─────────────────┘    └──────┬───────┘
                                │
                                ▼
                         ┌────────────┐
                         │ COMPLETED  │
                         └────────────┘
```

Additional terminal transitions:

```text
Any State
   ├──► CANCELLED  — user cancellation
   ├──► FAILED     — unrecoverable error
   └──► TIMEOUT    — execution budget exhausted
```

State transitions are persisted, and completed steps are checkpointed for crash recovery.

---

# 🤖 The Agent Team

NexusForge is composed of specialized agents rather than one monolithic prompt.

| Agent                      | Responsibility                                                  |
| -------------------------- | --------------------------------------------------------------- |
| 🔎 **RequirementAnalyzer** | Converts the user's requirement into a structured specification |
| 🧠 **RepoAnalyst**         | Builds repository knowledge using hybrid retrieval              |
| 📋 **Planner**             | Creates implementation steps and acceptance criteria            |
| 💻 **Implementer**         | Produces repository modifications through controlled tools      |
| 🧪 **TestGenerator**       | Creates tests based on repository conventions                   |
| ▶️ **ExecutionAgent**      | Executes commands and streams real output                       |
| 🔬 **FailureAnalyzer**     | Diagnoses failures from execution results                       |
| 🔧 **RepairAgent**         | Generates repairs based on structured failure analysis          |
| 🔐 **SecurityAgent**       | Performs built-in and optional external security scanning       |
| 👀 **CodeReviewer**        | Reviews the resulting diff                                      |
| 📊 **ReportWriter**        | Produces the final engineering report                           |

Every agent has:

* Explicit tool permissions
* Validated Pydantic output schemas
* Structured errors
* Bounded retry behavior

Malformed agent output is treated as an error rather than silently accepted.

---

# 🧠 Repository Intelligence

Large repositories cannot simply be dumped into an LLM context.

NexusForge uses a **hybrid repository retrieval pipeline**:

```text
Repository
    │
    ▼
File Ingestion
    │
    ▼
Metadata Extraction
    │
    ├── Language
    ├── Size
    ├── Hash
    └── AST Symbols
    │
    ▼
Symbol-Aware Chunking
    │
    ▼
Local Deterministic Embeddings
    │
    ▼
Per-Project Index
    │
    ├──────────────┐
    ▼              ▼
  BM25       Cosine Similarity
    │              │
    └──────┬───────┘
           ▼
Reciprocal-Rank Fusion
           │
           ▼
Dependency-Aware Boosting
           │
           ▼
Token-Budget Trimming
           │
           ▼
Relevant Context → LLM
```

The system combines:

* **BM25 lexical search**
* **Cosine similarity**
* **Reciprocal-Rank Fusion**
* **Dependency-aware ranking**
* **AST-aware symbol chunking**
* **Token-budget trimming**

This allows the agent to focus on relevant repository context instead of blindly sending the entire codebase to the model.

The default embedding approach is deterministic and local, using 512-dimensional hash-vector embeddings.

---

# 🤖 Provider-Agnostic LLM Layer

NexusForge uses an `LLMProvider` abstraction, allowing the agent engine to operate independently of a specific model provider.

Supported implementations:

### `local`

A deterministic, rule-based model that works offline.

It is intentionally limited and **does not fake capabilities**. If it cannot reliably handle a task, it fails clearly.

This makes NexusForge runnable in:

* Offline development
* CI environments
* Tests
* Demonstrations without API keys

### `openai`

Supports OpenAI-compatible endpoints.

### `anthropic`

Supports Anthropic API models.

### `gemini`

Supports Google Gemini REST APIs.

Provider selection:

```bash
NEXUSFORGE_LLM_PROVIDER=local
```

Remote provider model names and credentials are configured through environment variables.

---

# 🛡️ Secure Sandboxed Execution

Autonomous agents can generate commands.

**Those commands should never blindly execute on the host.**

NexusForge provides isolated execution backends.

### Docker Backend — Preferred

Commands run using Docker with restrictions such as:

```text
--network none
--memory ...
--cpus ...
--pids-limit ...
```

The workspace is mounted into the isolated environment.

### Process Backend — Fallback

When Docker is unavailable, NexusForge can use a restricted subprocess backend with:

* CPU limits
* Address-space limits
* Process limits
* File-size limits
* Scrubbed environment
* Confined working directory
* Process-group isolation
* Hard timeouts
* Forced termination when required

Configure the backend with:

```bash
NEXUSFORGE_SANDBOX_BACKEND=auto
```

Available options:

```text
auto
docker
process
```

Network access is **disabled by default**.

---

# 🔐 Security Architecture

Security is part of the execution pipeline—not an afterthought.

### Sandbox Isolation

AI-generated commands execute inside Docker or the restricted process backend.

### Workspace Confinement

Tool paths are resolved and verified against the project workspace, preventing path traversal outside the permitted area.

### Secret Hygiene

Files matching secret patterns are excluded from indexing and flagged during ingestion.

Secrets are also redacted from logs.

### Authentication

NexusForge uses:

* Argon2id password hashing
* JWT access tokens
* Rotating refresh tokens
* Hashed single-use refresh tokens
* Per-user project ownership checks

### Rate Limiting

Sliding-window rate limiting protects the API, with stricter controls on authentication endpoints.

### Secure Headers & CORS

Security headers and restricted CORS policies are applied by the backend.

### Audit Logging

Important lifecycle events are recorded in an append-only JSON-lines audit log.

### Git Push Protection

Git push is disabled by default.

```bash
NEXUSFORGE_GIT_PUSH_ENABLED=false
```

Even when enabled, pushing requires explicit user confirmation through the API.

**There is no automatic push.**

### Security Gate

Every run passes through `SecurityAgent`.

Depending on configuration, findings can block completion:

```bash
NEXUSFORGE_SECURITY_BLOCK_ON=critical
```

Supported thresholds:

```text
critical
high
none
```

A blocked run enters:

```text
SECURITY_BLOCKED
```

instead of generating a successful report.

---

# 📡 Real-Time Observability

NexusForge is designed so autonomous execution does not happen behind a black box.

The frontend can observe task execution through **Server-Sent Events (SSE)**.

```text
Agent starts
      │
      ├── Requirement analyzed
      ├── Repository context retrieved
      ├── Plan generated
      ├── Files modified
      ├── Tests generated
      ├── Command executed
      ├── Test output streamed
      ├── Failure detected
      ├── Repair attempted
      ├── Tests re-run
      ├── Security scan completed
      ├── Diff reviewed
      └── Engineering report generated
```

The event log is also persisted, allowing completed runs to be replayed.

---

# 📊 Engineering Reports

After completing a run, NexusForge produces an engineering report containing the results of the autonomous workflow.

Reports are available through the API in:

* JSON
* Markdown
* PDF

This makes the result useful not only for code generation, but also for **engineering traceability and review.**

---

# ⚡ Quick Start

## Requirements

* Python **3.11+**

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Start NexusForge

```bash
python -m apps.api.main
```

The development API runs on:

```text
http://127.0.0.1:8000
```

### 3. Run the end-to-end demo

In another terminal:

```bash
python scripts/demo.py
```

The demo:

1. Registers a user
2. Creates a project
3. Imports the intentionally incomplete demo repository
4. Creates an engineering requirement
5. Runs the autonomous agent pipeline
6. Streams live SSE events
7. Executes tests
8. Performs analysis and repair
9. Produces the final engineering report

With the default `local` provider, the complete demonstration can run **without external API access**.

---

# 🌐 Development Endpoints

| Endpoint    | Purpose                       |
| ----------- | ----------------------------- |
| `/docs`     | Interactive API documentation |
| `/api/docs` | Production API documentation  |
| `/health`   | Liveness check                |
| `/ready`    | Readiness check               |
| `/metrics`  | Prometheus metrics            |

---

# 🐳 Docker

Run the complete backend stack with:

```bash
docker compose up --build
```

Or use Make:

```bash
make docker-build
make docker-up
make docker-down
```

The stack includes:

```text
┌───────────────────────┐
│         API           │
│   FastAPI / Python    │
└───────────┬───────────┘
            │
     ┌──────┴───────┐
     │              │
     ▼              ▼
 PostgreSQL       Redis
  optional        optional
```

The default configuration intentionally keeps the system lightweight:

```text
SQLite
   +
In-process Queue
```

No additional services are required for the basic setup.

### PostgreSQL

Configure:

```bash
NEXUSFORGE_DATABASE_URL=postgresql+asyncpg://nexusforge:nexusforge@db:5432/nexusforge
```

### Redis

Configure:

```bash
NEXUSFORGE_REDIS_URL=redis://redis:6379/0
```

---

# 🖥️ Frontend

NexusForge includes a **Next.js web application** under:

```text
frontend/
```

Install and build it with:

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run build
```

For development:

```bash
npm run dev
```

`NEXT_PUBLIC_API_BASE` is injected at build time.

The web interface is designed around:

```text
Projects
   │
   ├── Repositories
   │
   ├── Tasks
   │
   └── Runs
          │
          ├── Live Agent Events
          ├── Terminal Output
          ├── Diff
          └── Engineering Report
```

---

# 🧪 Testing & Code Quality

Run the complete test suite:

```bash
make test
```

Run linting:

```bash
make lint
```

Format the code:

```bash
make format
```

NexusForge uses:

* **pytest**
* **pytest-asyncio**
* **httpx ASGI transport**
* **Ruff**

CI runs the test and linting jobs on pushes and pull requests to `main`.

---

# ⚙️ Configuration

NexusForge is configured entirely through environment variables.

All settings use the:

```text
NEXUSFORGE_
```

prefix.

The complete configuration reference is available in:

```text
.env.example
```

### Important configuration options

| Variable                               | Default           | Purpose                          |
| -------------------------------------- | ----------------- | -------------------------------- |
| `NEXUSFORGE_ENVIRONMENT`                | `development`     | Runtime environment              |
| `NEXUSFORGE_DEBUG`                      | `true`            | Debug logging / development docs |
| `NEXUSFORGE_API_HOST`                   | `127.0.0.1`       | API bind address                 |
| `NEXUSFORGE_API_PORT`                   | `8000`            | API port                         |
| `NEXUSFORGE_DATABASE_URL`               | SQLite            | Database                         |
| `NEXUSFORGE_JWT_SECRET`                 | dev-only          | JWT signing secret               |
| `NEXUSFORGE_LLM_PROVIDER`               | `local`           | LLM backend                      |
| `NEXUSFORGE_LLM_MODEL`                  | provider default  | Remote model                     |
| `NEXUSFORGE_SANDBOX_BACKEND`            | `auto`            | Sandbox implementation           |
| `NEXUSFORGE_SANDBOX_TIMEOUT_SECONDS`    | `120`             | Command timeout                  |
| `NEXUSFORGE_SANDBOX_NETWORK`            | `false`           | Sandbox network access           |
| `NEXUSFORGE_REDIS_URL`                  | empty             | Redis queue                      |
| `NEXUSFORGE_MAX_AGENT_ITERATIONS`       | `5`               | Repair-loop limit                |
| `NEXUSFORGE_MAX_EXECUTION_TIME_SECONDS` | `600`             | Whole-run budget                 |
| `NEXUSFORGE_SECURITY_BLOCK_ON`          | `critical`        | Security blocking threshold      |
| `NEXUSFORGE_RATE_LIMIT_ENABLED`         | `true`            | API rate limiting                |
| `NEXUSFORGE_WORKSPACE_ROOT`             | `var/workspaces`  | Run workspaces                   |
| `NEXUSFORGE_AUDIT_LOG_PATH`             | `var/audit.jsonl` | Audit log                        |
| `NEXUSFORGE_GIT_PUSH_ENABLED`           | `false`           | Git push capability              |

See `.env.example` for the complete configuration reference.

---

# 🔌 API Overview

All business endpoints are mounted under:

```text
/api
```

and require:

```http
Authorization: Bearer <access_token>
```

System endpoints such as health and metrics do not require authentication.

## Authentication

```text
POST   /api/auth/register
POST   /api/auth/login
POST   /api/auth/refresh
POST   /api/auth/logout
GET    /api/auth/me
PATCH  /api/auth/me
```

## Projects & Repositories

```text
GET    /api/projects
POST   /api/projects

GET    /api/projects/{id}
PATCH  /api/projects/{id}
DELETE /api/projects/{id}

GET    /api/projects/{id}/repositories
POST   /api/projects/{id}/repositories

POST   /api/projects/{id}/repositories/{repo_id}/reindex
DELETE /api/projects/{id}/repositories/{repo_id}
```

## Tasks & Runs

```text
GET    /api/tasks/projects/{project_id}
POST   /api/tasks/projects/{project_id}

GET    /api/tasks/{task_id}

POST   /api/tasks/{task_id}/run

GET    /api/tasks/{task_id}/runs

POST   /api/tasks/{task_id}/runs/{run_id}/cancel

GET    /api/tasks/{task_id}/runs/{run_id}/events
GET    /api/tasks/{task_id}/runs/{run_id}/stream
GET    /api/tasks/{task_id}/runs/{run_id}/diff

GET    /api/tasks/{task_id}/runs/{run_id}/report
GET    /api/tasks/{task_id}/runs/{run_id}/report/pdf
```

Project-scoped routes enforce per-user ownership.

---

# 📁 Project Structure

```text
nexusforge/
│
├── apps/
│   └── api/                  # FastAPI entrypoint
│
├── services/
│   ├── agent/                # Agent engine + state machine
│   ├── llm/                  # LLM providers
│   ├── tools/                # Tool registry + repository tools
│   ├── sandbox/              # Docker + process sandbox
│   ├── indexing/             # AST + indexing + retrieval
│   ├── security/             # Security analysis
│   ├── review/               # Diff/code review
│   ├── gitops/               # Safe Git operations
│   └── reporting/            # Markdown + PDF reporting
│
├── core/                     # Config, DB, schemas, security,
│                             # events, queue, metrics, logging
│
├── api/                      # App factory + routers + dependencies
│
├── demo/
│   └── repo/                 # Intentionally incomplete demo app
│
├── tests/                    # Unit / integration / E2E tests
│
├── scripts/
│   └── demo.py               # End-to-end demo
│
├── var/                      # Runtime data (gitignored)
│
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── Makefile
├── ruff.toml
├── pytest.ini
├── requirements.txt
├── ARCHITECTURE.md
└── README.md
```

---

# 📐 Engineering Philosophy

NexusForge is built around a simple idea:

> **An AI coding agent should be judged by the software it can actually validate—not by the code it can merely generate.**

That leads to several deliberate engineering decisions:

### No fake success

The local model explicitly fails when it cannot handle a task.

### Real execution

Test and command results originate from actual subprocesses or containers.

### Bounded autonomy

Repair loops have explicit iteration and time budgets.

### Persistent state

Agent progress is persisted rather than held entirely in memory.

### Explicit permissions

Agents can only use the tools permitted to their role.

### Security gates

Security findings can stop a run before completion.

### Human-controlled Git push

NexusForge does not silently push changes to repositories.

---

# 🔭 What Makes NexusForge Different?

Many AI coding systems focus primarily on:

```text
Prompt → Code
```

NexusForge is designed around:

```text
Requirement
     ↓
Repository Understanding
     ↓
Planning
     ↓
Implementation
     ↓
Testing
     ↓
Execution
     ↓
Failure Diagnosis
     ↓
Repair
     ↓
Security
     ↓
Review
     ↓
Engineering Report
```

That distinction is fundamental.

**NexusForge is not just a code generator.**

It is an attempt to build an **observable, test-driven, security-aware autonomous software-engineering loop.**

---

# 🏆 Project Status

NexusForge currently provides the core infrastructure for:

* Autonomous multi-agent software engineering
* Repository-aware code modification
* Test generation and execution
* Iterative failure repair
* Sandboxed command execution
* Security analysis
* Structured code review
* Real-time SSE observability
* Persistent task state
* Multi-provider LLM support
* Engineering report generation
* API authentication and tenancy
* SQLite / PostgreSQL persistence
* In-process / Redis-backed job queues

The architecture is intentionally modular so individual subsystems—LLM providers, sandbox backends, retrieval strategies, scanners, queues and reporting—can evolve independently.

---

# 📚 Documentation

For the deeper technical design—including requirements, technology choices, data model and scaling strategy—see:

**[`ARCHITECTURE.md`](./ARCHITECTURE.md)**

Configuration reference:

**[`.env.example`](./.env.example)**

---

# ⭐ If You Find NexusForge Interesting

NexusForge is built around a simple question:

### **What if an AI coding agent didn't stop at writing the code?**

What if it could:

**understand → plan → implement → test → debug → secure → review → report**

with every step visible and every important result grounded in actual execution?

That's the vision behind **NexusForge**.

---

<p align="center">

### ⚒️ NexusForge

**From requirements to verified software.**

**Understand. Build. Test. Repair. Secure. Review.**

</p>
