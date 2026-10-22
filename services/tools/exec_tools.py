"""Execution tools: run commands through the sandbox (never on the host).

Commands are argv lists (no shell). The sandbox enforces CPU/memory/timeout/
network/process limits and confines the working directory to the workspace.
"""
from __future__ import annotations

from core.errors import ToolError
from services.tools.registry import Tool, ToolContext, ToolResult

# allowlisted interpreters/tools the agent may invoke
ALLOWED_PROGRAMS = {
    "python", "python3", "pytest", "pip", "ruff", "bandit", "semgrep",
    "node", "npm", "git", "make",
}


def _validate_argv(argv: list[str]) -> str | None:
    if not argv or not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return "argv must be a list of strings"
    prog = argv[0].rsplit("/", 1)[-1]
    if prog not in ALLOWED_PROGRAMS:
        return f"program '{prog}' is not in the allowlist {sorted(ALLOWED_PROGRAMS)}"
    return None


class _ExecTool(Tool):
    needs_sandbox = True

    def _argv(self, args: dict) -> list[str]:
        raise NotImplementedError

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        argv = self._argv(args)
        err = _validate_argv(argv)
        if err:
            return ToolResult(ok=False, error=err)
        if ctx.sandbox is None:
            return ToolResult(ok=False, error="Sandbox not available for this run")
        timeout = min(args.get("timeout", 120), 300)
        result = await ctx.sandbox.run(
            argv=argv,
            workdir=str(ctx.workspace),
            timeout=timeout,
            env_extra=args.get("env", {}),
        )
        return ToolResult(
            ok=result.exit_code == 0,
            data={
                "exit_code": result.exit_code,
                "stdout": result.stdout[-20000:],
                "stderr": result.stderr[-20000:],
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
            },
            error=None if result.exit_code == 0 else
            (f"command exited {result.exit_code}" +
             (" (timeout)" if result.timed_out else "")),
        )


class RunTestsTool(_ExecTool):
    name = "run_tests"
    description = "Run the test suite (pytest) inside the sandbox"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string",
                        "description": "full test command, e.g. 'python -m pytest tests/ -q'"},
            "args": {"type": "string",
                     "description": "extra pytest args, e.g. 'tests/test_x.py'"},
            "timeout": {"type": "integer"},
        },
    }

    def _argv(self, args: dict) -> list[str]:
        command = (args.get("command") or "").strip()
        if command:
            argv = command.split()
        else:
            extra = (args.get("args") or "").split()
            argv = ["python", "-m", "pytest", *extra]
        # ensure a verbosity flag only for pytest commands
        if any("pytest" in a for a in argv):
            if not any(a in ("-q", "--quiet", "-v", "--verbose", "-ra") for a in argv):
                argv.append("-q")
        return argv


class RunLinterTool(_ExecTool):
    name = "run_linter"
    description = "Run ruff (lint) on the repository inside the sandbox"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "timeout": {"type": "integer"}},
    }

    def _argv(self, args: dict) -> list[str]:
        return ["ruff", "check", args.get("path") or "."]


class RunFormatterTool(_ExecTool):
    name = "run_formatter"
    description = "Run ruff format (check mode) inside the sandbox"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "timeout": {"type": "integer"}},
    }

    def _argv(self, args: dict) -> list[str]:
        return ["ruff", "format", "--check", args.get("path") or "."]


class RunBuildTool(_ExecTool):
    name = "run_build"
    description = "Run a build/compile check (python -m compileall) in the sandbox"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "timeout": {"type": "integer"}},
    }

    def _argv(self, args: dict) -> list[str]:
        return ["python", "-m", "compileall", "-q", args.get("path") or "."]


class RunSecurityScanTool(_ExecTool):
    name = "run_security_scan"
    description = "Run bandit (Python security linter) inside the sandbox"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "timeout": {"type": "integer"}},
    }

    def _argv(self, args: dict) -> list[str]:
        return ["python", "-m", "bandit", "-q", "-r", args.get("path") or "."]


def register_exec_tools(registry) -> None:
    for t in (RunTestsTool(), RunLinterTool(), RunFormatterTool(),
              RunBuildTool(), RunSecurityScanTool()):
        registry.register(t)