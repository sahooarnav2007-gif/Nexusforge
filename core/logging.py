"""Structured JSON logging with request/task correlation and secret redaction."""
from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from typing import Any

# Correlation context -------------------------------------------------------
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
task_id_var: ContextVar[str] = ContextVar("task_id", default="-")
project_id_var: ContextVar[str] = ContextVar("project_id", default="-")

_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|authorization|bearer)\b[\s:=]+(\S+)"
)


def redact(value: str) -> str:
    """Redact obvious secret values from log strings."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", value)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        rid = request_id_var.get()
        if rid != "-":
            payload["request_id"] = rid
        tid = task_id_var.get()
        if tid != "-":
            payload["task_id"] = tid
        pid = project_id_var.get()
        if pid != "-":
            payload["project_id"] = pid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # extra fields
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                payload[k] = v
        return json.dumps(payload, default=str, ensure_ascii=False)


_RESERVED = set(
    "name msg args levelno pathname filename module levelname funcName "
    "lineno exc_info exc_text stack_info task created msecs relativeCreated "
    "thread threadName process processName asctime message".split()
)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def setup_logging(level: str = "INFO", json_output: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    root.addHandler(handler)
    # keep third-party noise down
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)