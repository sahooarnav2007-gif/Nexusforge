"""Tool registry: the ONLY way agents touch the repository or run commands.

Every tool has:
- input validation (JSON-schema-ish, checked in `validate`)
- authorization (agents declare permissions; the registry enforces them)
- timeout (exec-style tools)
- logging + audit (every call is logged with agent/run context)
- safe error handling (ToolError, never raw exceptions to the agent)

The AI never gets unrestricted OS access: file tools are confined to the
project workspace, and execution tools go through the sandbox.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.audit import audit
from core.errors import ToolError
from core.logging import get_logger

log = get_logger("nexusforge.tools")


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {"ok": self.ok, "data": self.data, "error": self.error,
                "duration_ms": self.duration_ms}


@dataclass
class ToolContext:
    """Per-run execution context handed to every tool call."""

    workspace: Path
    run_id: str
    task_id: str
    project_id: str
    agent: str
    permissions: set[str]
    sandbox: Any | None = None  # Sandbox instance for exec tools
    extra: dict = field(default_factory=dict)


class Tool:
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}
    needs_sandbox: bool = False

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError(f"Unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def specs_for(self, permissions: set[str]) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self._tools.values() if t.name in permissions
        ]

    async def call(self, ctx: ToolContext, name: str, args: dict[str, Any]) -> ToolResult:
        if name not in ctx.permissions:
            msg = f"Tool '{name}' is not permitted for agent '{ctx.agent}'"
            log.warning(msg)
            audit("tool_denied", task_id=ctx.task_id, detail={"tool": name, "agent": ctx.agent})
            return ToolResult(ok=False, error=msg)
        tool = self.get(name)
        # input validation
        err = self._validate(tool, args)
        if err:
            return ToolResult(ok=False, error=f"Invalid arguments: {err}")
        t0 = time.perf_counter()
        try:
            result = await tool.run(ctx, args)
        except ToolError as e:
            result = ToolResult(ok=False, error=str(e))
        except Exception as e:  # noqa: BLE001 - tools must never crash the agent
            log.exception("tool %s crashed", name)
            result = ToolResult(ok=False, error=f"Tool error: {type(e).__name__}: {e}")
        result.duration_ms = int((time.perf_counter() - t0) * 1000)
        log.info("tool call", extra={
            "tool": name, "agent": ctx.agent, "ok": result.ok,
            "duration_ms": result.duration_ms,
        })
        audit("tool_call", task_id=ctx.task_id,
              detail={"tool": name, "agent": ctx.agent, "ok": result.ok})
        return result

    def _validate(self, tool: Tool, args: dict[str, Any]) -> str | None:
        schema = tool.parameters or {}
        props = schema.get("properties", {})
        required = schema.get("required", [])
        for req in required:
            if req not in args:
                return f"missing required argument '{req}'"
        for key, val in args.items():
            if key not in props:
                continue
            typ = props[key].get("type")
            if typ == "string" and not isinstance(val, str):
                return f"argument '{key}' must be a string"
            if typ == "integer" and not isinstance(val, int):
                return f"argument '{key}' must be an integer"
            if typ == "boolean" and not isinstance(val, bool):
                return f"argument '{key}' must be a boolean"
        return None


# ---------------------------------------------------------------------------
# Path safety: confine every file operation to the workspace
# ---------------------------------------------------------------------------


def safe_path(workspace: Path, rel: str) -> Path:
    """Resolve `rel` inside `workspace`; raise ToolError on traversal.

    Cross-platform: `rel` may use `/` or OS-native separators; containment
    is checked with `is_relative_to` (no hardcoded path separator).
    """
    if not rel or rel.startswith(("/", "~")) or ".." in Path(rel).parts:
        raise ToolError(f"Unsafe path: {rel!r}")
    target = (workspace / rel).resolve()
    root = workspace.resolve()
    if not target.is_relative_to(root):
        raise ToolError(f"Path escapes workspace: {rel!r}")
    return target


MAX_READ_BYTES = 200_000


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------


class ListFilesTool(Tool):
    name = "list_files"
    description = "List files in the repository (relative paths), optionally under a directory"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "subdirectory (optional)"}},
    }

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        base = ctx.workspace
        if args.get("path"):
            base = safe_path(ctx.workspace, args["path"])
        if not base.is_dir():
            return ToolResult(ok=False, error=f"Not a directory: {args.get('path')}")
        files = []
        for p in sorted(base.rglob("*")):
            if p.is_file() and ".git" not in p.parts:
                files.append(p.relative_to(ctx.workspace).as_posix())
        return ToolResult(ok=True, data={"files": files[:2000], "count": len(files)})


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a text file from the repository (max 200KB)"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        target = safe_path(ctx.workspace, args["path"])
        if not target.is_file():
            return ToolResult(ok=False, error=f"File not found: {args['path']}")
        if target.stat().st_size > MAX_READ_BYTES:
            return ToolResult(ok=False, error="File too large (>200KB)")
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, error="Binary file; cannot read as text")
        return ToolResult(ok=True, data={"path": args["path"], "content": text})


class SearchCodeTool(Tool):
    name = "search_code"
    description = "Search file contents with a regex; returns matching lines"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "optional subdirectory"},
        },
        "required": ["pattern"],
    }

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        try:
            rx = re.compile(args["pattern"])
        except re.error as e:
            return ToolResult(ok=False, error=f"Invalid regex: {e}")
        base = ctx.workspace
        if args.get("path"):
            base = safe_path(ctx.workspace, args["path"])
        matches = []
        for p in sorted(base.rglob("*")):
            if not p.is_file() or ".git" in p.parts:
                continue
            if p.suffix.lower() in {".png", ".jpg", ".zip", ".pdf", ".pyc"}:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    matches.append({"path": p.relative_to(ctx.workspace).as_posix(),
                                    "line": i, "text": line.strip()[:200]})
                    if len(matches) >= 200:
                        return ToolResult(ok=True, data={"matches": matches, "truncated": True})
        return ToolResult(ok=True, data={"matches": matches, "truncated": False})


class InspectSymbolTool(Tool):
    name = "inspect_symbol"
    description = "Find a function/class definition and return its source"
    parameters = {
        "type": "object",
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
    }

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        from services.indexing.ast_extract import extract_symbols

        name = args["symbol"]
        for p in sorted(ctx.workspace.rglob("*.py")):
            if ".git" in p.parts:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            syms, _ = extract_symbols("python", text)
            for s in syms:
                if s.name == name or s.name.endswith("." + name):
                    lines = text.splitlines()
                    src = "\n".join(lines[s.line - 1:s.end_line])
                    return ToolResult(ok=True, data={
                        "symbol": s.name, "kind": s.kind,
                        "path": p.relative_to(ctx.workspace).as_posix(),
                        "line": s.line, "source": src,
                    })
        return ToolResult(ok=False, error=f"Symbol not found: {name}")


class InspectDependenciesTool(Tool):
    name = "inspect_dependencies"
    description = "List dependency manifests and their declared dependencies"
    parameters = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        manifests = {}
        for name in ("requirements.txt", "pyproject.toml", "package.json",
                     "Pipfile", "go.mod", "Cargo.toml"):
            p = ctx.workspace / name
            if p.exists():
                try:
                    manifests[name] = p.read_text(encoding="utf-8")[:4000]
                except (UnicodeDecodeError, OSError):
                    manifests[name] = "<unreadable>"
        return ToolResult(ok=True, data={"manifests": manifests})


class CreateFileTool(Tool):
    name = "create_file"
    description = "Create a new file (fails if it already exists)"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        target = safe_path(ctx.workspace, args["path"])
        if target.exists():
            return ToolResult(ok=False, error=f"File already exists: {args['path']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["content"], encoding="utf-8")
        return ToolResult(ok=True, data={"path": args["path"], "bytes": len(args["content"])})


class ModifyFileTool(Tool):
    name = "modify_file"
    description = "Replace the full content of an existing file"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        target = safe_path(ctx.workspace, args["path"])
        if not target.is_file():
            return ToolResult(ok=False, error=f"File not found: {args['path']}")
        target.write_text(args["content"], encoding="utf-8")
        return ToolResult(ok=True, data={"path": args["path"], "bytes": len(args["content"])})


class PatchFileTool(Tool):
    name = "patch_file"
    description = ("Apply an exact text replacement: replace `old_text` with "
                   "`new_text` in a file. old_text must occur exactly once.")
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        target = safe_path(ctx.workspace, args["path"])
        if not target.is_file():
            return ToolResult(ok=False, error=f"File not found: {args['path']}")
        text = target.read_text(encoding="utf-8")
        count = text.count(args["old_text"])
        if count == 0:
            return ToolResult(ok=False, error="old_text not found in file")
        if count > 1:
            return ToolResult(ok=False,
                              error=f"old_text occurs {count} times; must be unique")
        target.write_text(text.replace(args["old_text"], args["new_text"], 1),
                          encoding="utf-8")
        return ToolResult(ok=True, data={"path": args["path"]})


class DeleteFileTool(Tool):
    name = "delete_file"
    description = "Delete a file from the repository"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        target = safe_path(ctx.workspace, args["path"])
        if not target.is_file():
            return ToolResult(ok=False, error=f"File not found: {args['path']}")
        # refuse to delete dependency manifests or the repo root config
        if target.name in ("requirements.txt", "pyproject.toml", "setup.py",
                           "package.json", "pytest.ini"):
            return ToolResult(ok=False, error="Refusing to delete a dependency manifest")
        target.unlink()
        return ToolResult(ok=True, data={"path": args["path"]})


def register_repo_tools(registry: ToolRegistry) -> None:
    for t in (ListFilesTool(), ReadFileTool(), SearchCodeTool(), InspectSymbolTool(),
              InspectDependenciesTool(), CreateFileTool(), ModifyFileTool(),
              PatchFileTool(), DeleteFileTool()):
        registry.register(t)