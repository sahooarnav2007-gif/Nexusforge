"""Git tools: read-only by default; commit requires the run's explicit flag.

Push is NOT exposed as a tool — it only exists behind a dedicated API call
with explicit user confirmation (see services/gitops/git.push).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from services.tools.registry import Tool, ToolContext, ToolResult

GIT_TIMEOUT = 30


def _git(ctx: ToolContext, *args: str) -> ToolResult:
    if shutil.which("git") is None:
        return ToolResult(ok=False, error="git not installed")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(ctx.workspace),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, error="git timed out")
    return ToolResult(
        ok=proc.returncode == 0,
        data={"stdout": proc.stdout, "stderr": proc.stderr,
              "exit_code": proc.returncode},
        error=None if proc.returncode == 0 else proc.stderr.strip()[:300],
    )


class GitStatusTool(Tool):
    name = "git_status"
    description = "Show git working-tree status (porcelain)"
    parameters = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        return _git(ctx, "status", "--porcelain")


class GitDiffTool(Tool):
    name = "git_diff"
    description = "Show the diff of uncommitted changes vs HEAD"
    parameters = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        return _git(ctx, "diff", "HEAD")


class GitLogTool(Tool):
    name = "git_log"
    description = "Show recent commit history"
    parameters = {
        "type": "object",
        "properties": {"limit": {"type": "integer"}},
    }

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        limit = min(int(args.get("limit", 20)), 100)
        return _git(ctx, "log", f"--max-count={limit}",
                    "--pretty=format:%h %an %s")


class GitCommitTool(Tool):
    name = "git_commit"
    description = "Stage all changes and create a commit (requires run flag allow_commit)"
    parameters = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        if not ctx.extra.get("allow_commit", False):
            return ToolResult(ok=False,
                              error="Committing is not enabled for this run")
        msg = args["message"][:300]
        r1 = _git(ctx, "add", "-A")
        if not r1.ok:
            return r1
        r2 = _git(ctx, "commit", "-m", msg)
        return r2


def register_git_tools(registry) -> None:
    for t in (GitStatusTool(), GitDiffTool(), GitLogTool(), GitCommitTool()):
        registry.register(t)


def build_registry() -> "ToolRegistry":
    """Build the full tool registry (repo + exec + git tools)."""
    from services.tools import exec_tools, git_tools, registry as reg_mod

    reg = reg_mod.ToolRegistry()
    reg_mod.register_repo_tools(reg)
    exec_tools.register_exec_tools(reg)
    git_tools.register_git_tools(reg)
    return reg