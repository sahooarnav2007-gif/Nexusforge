"""Prometheus-compatible metrics (prometheus_client)."""
from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "nexusforge_http_requests_total",
    "HTTP requests by method, path, status",
    ["method", "path", "status"],
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "nexusforge_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"],
    registry=REGISTRY,
)
TASK_RUNS = Counter(
    "nexusforge_task_runs_total",
    "Task runs by final state",
    ["state"],
    registry=REGISTRY,
)
AGENT_STEPS = Counter(
    "nexusforge_agent_steps_total",
    "Agent steps by agent and status",
    ["agent", "status"],
    registry=REGISTRY,
)
LLM_CALLS = Counter(
    "nexusforge_llm_calls_total",
    "LLM calls by provider and outcome",
    ["provider", "outcome"],
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "nexusforge_llm_tokens_total",
    "LLM tokens by provider and kind",
    ["provider", "kind"],
    registry=REGISTRY,
)
SANDBOX_RUNS = Counter(
    "nexusforge_sandbox_runs_total",
    "Sandbox executions by backend and outcome",
    ["backend", "outcome"],
    registry=REGISTRY,
)
QUEUE_DEPTH = Gauge(
    "nexusforge_queue_depth",
    "Jobs waiting in the queue",
    registry=REGISTRY,
)
DB_POOL_SIZE = Gauge(
    "nexusforge_db_pool_size",
    "DB engine pool size",
    registry=REGISTRY,
)


def metrics_body() -> bytes:
    return generate_latest(REGISTRY)