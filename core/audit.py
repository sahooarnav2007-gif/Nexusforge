"""Append-only JSONL audit log for security-relevant events."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from core.config import get_settings

_lock = threading.Lock()


def audit(event: str, *, user_id: str | None = None, project_id: str | None = None,
          task_id: str | None = None, detail: dict[str, Any] | None = None,
          path: Path | None = None) -> None:
    record = {
        "ts": time.time(),
        "event": event,
        "user_id": user_id,
        "project_id": project_id,
        "task_id": task_id,
        "detail": detail or {},
    }
    target = path or get_settings().audit_log_path
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with target.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
    except OSError:  # pragma: no cover - audit must never break the request
        pass