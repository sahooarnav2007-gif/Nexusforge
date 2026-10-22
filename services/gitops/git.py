"""Safe Git wrapper (subprocess, argv lists — never a shell string).

All operations are read-only by default. Commit requires explicit caller
intent; push is disabled unless configured AND explicitly confirmed.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.config import get_settings
from core.errors import ValidationError

GIT_TIMEOUT = 30


@dataclass
class GitResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int


def _git(cwd: Path, *args: str, check: bool = True) -> GitResult:
    if shutil.which("git") is None:
        raise ValidationError("git is not installed on this host")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ValidationError(f"git {' '.join(args)} timed out") from e
    result = GitResult(proc.returncode == 0, proc.stdout, proc.stderr, proc.returncode)
    if check and not result.ok:
        raise ValidationError(f"git {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result


def is_repo(cwd: Path) -> bool:
    r = _git(cwd, "rev-parse", "--is-inside-work-tree", check=False)
    return r.ok and r.stdout.strip() == "true"


def status(cwd: Path) -> dict:
    r = _git(cwd, "status", "--porcelain")
    files = []
    for line in r.stdout.splitlines():
        if len(line) >= 3:
            files.append({"x": line[0], "y": line[1], "path": line[3:]})
    return {"branch": branch(cwd), "files": files, "clean": not files}


def branch(cwd: Path) -> str:
    r = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    return r.stdout.strip() if r.ok else ""


def head_commit(cwd: Path) -> str:
    r = _git(cwd, "rev-parse", "HEAD", check=False)
    return r.stdout.strip() if r.ok else ""


def log(cwd: Path, limit: int = 20) -> list[dict]:
    r = _git(cwd, "log", f"--max-count={limit}",
             "--pretty=format:%H%x1f%an%x1f%aI%x1f%s")
    entries = []
    for line in r.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            entries.append({"hash": parts[0], "author": parts[1],
                            "date": parts[2], "subject": parts[3]})
    return entries


def diff(cwd: Path, staged: bool = False) -> str:
    args = ["diff"]
    if staged:
        args.append("--cached")
    else:
        args += ["HEAD"]
    r = _git(cwd, *args, check=False)
    return r.stdout


def branches(cwd: Path) -> list[str]:
    r = _git(cwd, "branch", "--format=%(refname:short)")
    return [b for b in r.stdout.splitlines() if b.strip()]


def add_all(cwd: Path) -> None:
    _git(cwd, "add", "-A")


def commit(cwd: Path, message: str) -> str:
    add_all(cwd)
    r = _git(cwd, "commit", "-m", message)
    return head_commit(cwd)


def push(cwd: Path, confirm: bool) -> GitResult:
    s = get_settings()
    if not s.git_push_enabled:
        raise ValidationError("Git push is disabled (NEXUSFORGE_GIT_PUSH_ENABLED)")
    if not confirm:
        raise ValidationError("Explicit confirmation required to push to a remote")
    return _git(cwd, "push", check=False)


def init(cwd: Path, branch: str = "main") -> None:
    _git(cwd, "init", "-b", branch, check=False)
    _git(cwd, "config", "user.email", "nexusforge@local", check=False)
    _git(cwd, "config", "user.name", "NexusForge", check=False)


def ensure_repo(cwd: Path) -> None:
    """Ensure the directory is a git repo (init if needed) and clean-committed."""
    if not is_repo(cwd):
        init(cwd)
    # baseline commit so diffs are meaningful
    st = status(cwd)
    if st["files"] and not log(cwd, 1):
        commit(cwd, "nexusforge: baseline import")
def clone(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("git") is None:
        raise ValidationError("git is not installed on this host")
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT * 3,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ValidationError(f"Git clone timed out for {url}") from e
    if proc.returncode != 0:
        raise ValidationError(f"Git clone failed: {proc.stderr.strip()[:300]}")
