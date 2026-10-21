"""Security analysis: built-in rules + external tools (bandit/semgrep) when
available. Findings are structured and persisted; critical/high findings can
block the run (configurable).
"""
from __future__ import annotations

import re
import shutil

from core.config import get_settings
from core.logging import get_logger

log = get_logger("nexusforge.security")

# (rule_id, severity, pattern, message) — applied to Python/JS source
BUILTIN_RULES: list[tuple[str, str, re.Pattern, str]] = [
    ("hardcoded-secret", "critical",
     re.compile(r"(?i)(api[_-]?key|secret|access[_-]?key|password|passwd|token)"
                r"\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
     "Hard-coded credential literal"),
    ("weak-hash", "medium",
     re.compile(r"\b(md5|sha1)\s*\("),
     "Weak cryptographic hash (md5/sha1) — use sha256+"),
    ("shell-true", "high",
     re.compile(r"subprocess\.\w+\(.*shell\s*=\s*True"),
     "subprocess with shell=True enables command injection"),
    ("eval-exec", "high",
     re.compile(r"\b(eval|exec)\s*\("),
     "Dynamic code execution (eval/exec) is a code-injection risk"),
    ("pickle-loads", "medium",
     re.compile(r"pickle\.loads?\s*\("),
     "Deserializing untrusted data with pickle can execute code"),
    ("yaml-unsafe-load", "high",
     re.compile(r"yaml\.load\s*\((?![^)]*SafeLoader)"),
     "yaml.load without SafeLoader can execute arbitrary code"),
    ("sql-string-format", "high",
     re.compile(r"(execute|query)\s*\(\s*f['\"]"),
     "SQL built with an f-string is an injection risk; use bound parameters"),
    ("sql-concat", "high",
     re.compile(r"(execute|query)\s*\(\s*[^)]*['\"]\s*\+"),
     "SQL built with string concatenation is an injection risk"),
    ("tls-verify-off", "high",
     re.compile(r"verify\s*=\s*False"),
     "TLS certificate verification disabled"),
    ("debug-mode", "medium",
     re.compile(r"app\s*=\s*FastAPI\([^)]*debug\s*=\s*True|DEBUG\s*=\s*True"),
     "Debug mode enabled — may leak stack traces in production"),
    ("weak-cipher", "medium",
     re.compile(r"\bDES3?_ECB|RC4\b"),
     "Deprecated/weak cipher algorithm"),
    ("assert-security", "low",
     re.compile(r"assert\s+\w+\.is_authorized|assert\s+is_admin"),
     "assert is stripped under -O; do not use it for authorization checks"),
]

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
             "build", ".next", "tests", ".pytest_cache"}
SCAN_EXTS = {".py", ".js", ".ts"}


def _iter_files(root, only_paths: list[str] | None = None):
    import os

    from pathlib import Path

    root = Path(root)
    if only_paths:
        for p in only_paths:
            fp = root / p
            if fp.is_file():
                yield fp
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if Path(fn).suffix in SCAN_EXTS:
                yield Path(dirpath) / fn


def scan_builtins(root, only_paths: list[str] | None = None) -> list[dict]:
    findings: list[dict] = []
    seen: set[tuple] = set()
    for fp in _iter_files(root, only_paths):
        try:
            text = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = fp.relative_to(root).as_posix()
        for i, line in enumerate(text.splitlines(), 1):
            for rule_id, severity, rx, message in BUILTIN_RULES:
                if rx.search(line):
                    key = (rel, i, rule_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append({
                        "severity": severity, "rule_id": rule_id, "path": rel,
                        "line": i, "message": message, "source": "builtin",
                    })
    return findings


async def _run_bandit(ctx) -> list[dict]:
    """Run bandit in the sandbox; parse its JSON output."""
    from services.tools.registry import ToolContext

    tctx = ToolContext(
        workspace=ctx.workspace, run_id=ctx.run.id, task_id=ctx.task.id,
        project_id=ctx.project.id, agent="security_agent",
        permissions={"run_security_scan"}, sandbox=ctx.sandbox,
    )
    res = await ctx.tools.call(tctx, "run_security_scan", {"timeout": 120})
    if not res.ok:
        log.warning("bandit unavailable or failed: %s", res.error)
        return []
    data = res.data or {}
    out = data.get("stdout", "") + data.get("stderr", "")
    findings = []
    # bandit text output blocks:
    #   >> Issue: [B324:hashlib] Use of weak MD5 hash ...
    #      Severity: High   Confidence: High
    #      Location: ./app/utils.py:7:11
    blocks = re.split(r">> Issue: ", out)
    for block in blocks[1:]:
        head = block.splitlines()[0] if block else ""
        m = re.match(r"\[(\w+):([\w-]+)\]\s*(.*)", head)
        if not m:
            continue
        rule, plugin, message = m.group(1), m.group(2), m.group(3).strip()
        sev_m = re.search(r"Severity:\s*(\w+)", block)
        loc_m = re.search(r"Location:\s*(\S+?)(?::(\d+))?", block)
        path = loc_m.group(1).lstrip("./") if loc_m else ""
        line = int(loc_m.group(2)) if loc_m and loc_m.group(2) else None
        findings.append({
            "severity": _bandit_sev(sev_m.group(1)) if sev_m else "info",
            "rule_id": f"bandit-{rule}:{plugin}",
            "path": path, "line": line,
            "message": message[:200], "source": "bandit",
        })
    return findings


def _bandit_sev(level: str) -> str:
    return {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(level, "info")


def _bandit_module_ok() -> bool:
    import importlib.util

    return importlib.util.find_spec("bandit") is not None


async def scan_repository(ctx) -> list[dict]:
    """Full scan: changed files (strict) + repo-wide (built-in). External
    tools run when configured and installed."""
    from sqlalchemy import select

    from core.models import CodeChange

    findings: list[dict] = []
    changed = (
        (
            await ctx.db.execute(
                select(CodeChange).where(CodeChange.run_id == ctx.run.id))
        )
        .scalars()
        .all()
    )
    changed_paths = [c.path for c in changed if c.path.endswith((".py", ".js", ".ts"))]

    # 1) built-in rules over changed files (strict) + repo-wide
    if changed_paths:
        findings += scan_builtins(ctx.workspace, only_paths=changed_paths)
    findings += scan_builtins(ctx.workspace)

    # 2) external tools
    s = get_settings()
    if s.security_external_tools and (shutil.which("bandit") or _bandit_module_ok()):
        try:
            findings += await _run_bandit(ctx)
        except Exception as e:
            log.warning("bandit run failed: %s", e)

    # dedupe
    uniq: dict[tuple, dict] = {}
    for f in findings:
        key = (f["rule_id"], f["path"], f.get("line"), f["source"])
        if key not in uniq:
            uniq[key] = f
    result = list(uniq.values())
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    result.sort(key=lambda f: (order.get(f["severity"], 9), f["path"]))
    log.info("security scan complete", extra={
        "total": len(result),
        "by_severity": {sev: sum(1 for f in result if f["severity"] == sev)
                        for sev in ("critical", "high", "medium", "low", "info")},
    })
    return result