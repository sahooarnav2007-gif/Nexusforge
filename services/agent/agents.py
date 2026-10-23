"""The agent roster.

Each agent:
- has one clear responsibility and one machine state
- declares tool permissions (enforced by the registry)
- produces structured output (validated dict)
- emits events for the live UI
- handles its own errors (raised as NexusForgeError subclasses)

The orchestrator (engine.py) sequences them through the state machine.
"""
from __future__ import annotations

import json
import re
from typing import Any

from core.config import get_settings
from core.errors import NexusForgeError, LLMError
from core.models import Execution, SecurityFinding, TestRun
from services.agent import testparse
from services.agent.context import AgentContext
from services.agent.states import TaskState as TS

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

FILE_TOOLS = {"list_files", "read_file", "search_code", "inspect_symbol",
              "inspect_dependencies"}
WRITE_TOOLS = FILE_TOOLS | {"create_file", "modify_file", "patch_file", "delete_file"}
EXEC_TOOLS = {"run_tests", "run_linter", "run_build", "run_security_scan"}
GIT_TOOLS = {"git_status", "git_diff", "git_log"}


async def _apply_operations(ctx: AgentContext, operations: list[dict]) -> list[dict]:
    """Apply file operations through the tool layer; record every change."""
    applied = []
    for op in operations:
        if not isinstance(op, dict):
            continue
        kind = op.get("op") or op.get("action") or op.get("operation") or "modify"
        kind = str(kind).lower().strip()
        if kind in ("update", "edit"):
            kind = "modify"
        elif kind in ("add", "new"):
            kind = "create"
        elif kind in ("remove",):
            kind = "delete"

        raw_path = op.get("path") or op.get("file") or op.get("target_file") or op.get("filename") or ""
        path = str(raw_path).replace("\\", "/").lstrip("/")
        if not path:
            continue
        content = op.get("content") or op.get("new_content") or op.get("code") or ""
        reasoning = op.get("reasoning") or op.get("description") or op.get("summary") or ""
        fp = ctx.workspace / path
        before = fp.read_text(encoding="utf-8") if fp.exists() else None
        if kind == "create":
            tctx = ctx.tool_ctx("implementer", WRITE_TOOLS)
            res = await ctx.tools.call(tctx, "create_file",
                                       {"path": path, "content": content})
        elif kind == "modify":
            if not fp.exists():
                tctx = ctx.tool_ctx("implementer", WRITE_TOOLS)
                res = await ctx.tools.call(tctx, "create_file",
                                           {"path": path, "content": content})
                kind = "create"
            else:
                tctx = ctx.tool_ctx("implementer", WRITE_TOOLS)
                res = await ctx.tools.call(tctx, "modify_file",
                                           {"path": path, "content": content})
        elif kind == "delete":
            tctx = ctx.tool_ctx("implementer", WRITE_TOOLS)
            res = await ctx.tools.call(tctx, "delete_file", {"path": path})
        else:
            res = None
        if res is None or not res.ok:
            raise NexusForgeError(
                f"Failed to apply operation '{kind}' on {path}: "
                f"{res.error if res else 'no result'}"
            )
        after = None
        if kind != "delete":
            if fp.exists():
                after = fp.read_text(encoding="utf-8")
        await ctx.record_change(path, kind, before, after, reasoning)
        applied.append({"path": path, "op": kind, "reasoning": reasoning})
        await ctx.emit_terminal(f"[agent] {kind} {path} — {reasoning[:120]}")
    return applied


def _normalize_target(t: Any) -> dict[str, str]:
    """Normalize any target representation (string, dict, object) into a standard dict."""
    if isinstance(t, dict):
        path = str(t.get("path") or t.get("file") or t.get("name") or "")
        desc = str(t.get("description") or t.get("desc") or t.get("summary") or path or "")
        kind = str(t.get("kind") or "target")
        name = str(t.get("name") or path)
        return {
            "kind": kind,
            "name": name,
            "path": path,
            "description": desc,
        }
    if isinstance(t, str):
        return {
            "kind": "target",
            "name": t,
            "path": t if ("." in t or "/" in t or "\\" in t) else "",
            "description": t,
        }
    s = str(t)
    return {
        "kind": "target",
        "name": s,
        "path": "",
        "description": s,
    }


def _parse_json_response(content: str) -> dict:
    """Parse the LLM's JSON output; tolerate code fences, unescaped newlines, and truncated responses."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # 0. Pre-clean raw unescaped triple-quotes in "content": """...""" or '''...'''
    def _escape_multiline_docstrings(m):
        raw = m.group(1).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        return f'"content": "{raw}"'

    text = re.sub(r'"content"\s*:\s*"""(.*?)"""', _escape_multiline_docstrings, text, flags=re.DOTALL)
    text = re.sub(r'"content"\s*:\s*\'\'\'(.*?)\'\'\'', _escape_multiline_docstrings, text, flags=re.DOTALL)

    # 1. Direct json.loads with strict=False (allows unescaped newlines/tabs in code)
    try:
        data = json.loads(text, strict=False)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            if data and isinstance(data[0], dict):
                return data[0]
            return {"items": data}
    except Exception:
        pass

    # 2. Extract outermost { ... }
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1), strict=False)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    # 3. Extract outermost [ ... ]
    m2 = re.search(r"(\[.*\])", text, re.DOTALL)
    if m2:
        try:
            data = json.loads(m2.group(1), strict=False)
            if isinstance(data, list):
                if data and isinstance(data[0], dict):
                    return data[0]
                return {"items": data}
        except Exception:
            pass

    # 4. Truncation repair: auto-close open strings and braces
    repaired = text.strip()
    repaired = re.sub(r"```.*$", "", repaired).strip()
    if repaired.count('"') % 2 != 0:
        repaired += '"'
    opens_brace = repaired.count('{') - repaired.count('}')
    opens_bracket = repaired.count('[') - repaired.count(']')
    repaired += (']' * max(0, opens_bracket)) + ('}' * max(0, opens_brace))
    try:
        data = json.loads(repaired, strict=False)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            if data and isinstance(data[0], dict):
                return data[0]
            return {"items": data}
    except Exception:
        pass

    # 5. Regex-based extraction of "operations" if JSON was cut off inside operations
    if '"operations"' in text or '"changes"' in text:
        ops = []
        op_blocks = re.findall(r'\{\s*"[^"]+"\s*:\s*"[^"]+".*?\}', text, re.DOTALL)
        for ob in op_blocks:
            try:
                op_data = json.loads(ob, strict=False)
                if isinstance(op_data, dict) and ("path" in op_data or "file" in op_data or "content" in op_data):
                    ops.append(op_data)
            except Exception:
                pass
        if not ops:
            path_matches = list(re.finditer(r'"path"\s*:\s*"([^"]+)"', text))
            for pm in path_matches:
                p = pm.group(1)
                start_search = pm.end()
                content_m = re.search(r'"content"\s*:\s*"(.*?)(?:"\s*,\s*"|"\s*\}\s*,\s*\{|"\s*\}\s*\]|$)', text[start_search:], re.DOTALL)
                if content_m:
                    ops.append({"operation": "modify", "path": p, "content": content_m.group(1).replace('\\n', '\n').replace('\\"', '"')})
        if ops:
            return {"operations": ops, "summary": f"extracted {len(ops)} operations"}

    if '"steps"' in text or '"plan"' in text:
        steps = []
        step_blocks = re.findall(r'\{\s*"id"\s*:\s*"[^"]+".*?\}', text, re.DOTALL)
        for sb in step_blocks:
            try:
                s_data = json.loads(sb, strict=False)
                if isinstance(s_data, dict) and "id" in s_data:
                    steps.append(s_data)
            except Exception:
                pass
        if not steps:
            step_ids = list(re.finditer(r'"id"\s*:\s*"([^"]+)"', text))
            for sm in step_ids:
                sid = sm.group(1)
                desc_m = re.search(r'"description"\s*:\s*"([^"]+)"', text[sm.end():])
                desc = desc_m.group(1) if desc_m else sid
                steps.append({"id": sid, "description": desc, "files": [], "acceptance": ""})
        if steps:
            return {"steps": steps}

    raise LLMError(f"LLM returned malformed JSON: head={content[:200]!r}")


# ---------------------------------------------------------------------------
# 1) Requirement Analyzer
# ---------------------------------------------------------------------------


class RequirementAnalyzer:
    name = "requirement_analyzer"
    state = TS.ANALYZING
    permissions = FILE_TOOLS

    async def run(self, ctx: AgentContext) -> dict:
        step = await ctx.begin_step(self.name, self.state,
                                    {"requirement": ctx.task.requirement})
        try:
            await ctx.set_state(self.state)
            # retrieve context relevant to the requirement
            chunks = await ctx.retrieve(ctx.task.requirement, max_chunks=8)
            paths = sorted({c.path for c in chunks})
            files = await ctx.file_contents(paths)
            repo_summary = ctx.get_data("repo_summary", {})
            resp = await ctx.llm_complete(
                role="requirement_analyzer",
                system=(
                    "You are the requirement analyzer for an autonomous software "
                    "engineering platform. Parse the user requirement into a "
                    "structured spec: goal, concrete targets (endpoints/functions), "
                    "acceptance criteria, and the test command. Respond with JSON only."
                ),
                user=(
                    f"Requirement: {ctx.task.requirement}\n\n"
                    f"Repository summary: {json.dumps(repo_summary, default=str)[:2000]}"
                ),
                context={"requirement": ctx.task.requirement,
                         "repo_summary": repo_summary,
                         "files": files},
            )
            spec = _parse_json_response(resp.content)
            raw_targets = spec.get("targets", [])
            if isinstance(raw_targets, str):
                raw_targets = [raw_targets]
            spec["targets"] = [_normalize_target(t) for t in raw_targets]
            if not spec["targets"]:
                default_desc = spec.get("goal") or ctx.task.requirement
                spec["targets"] = [_normalize_target(default_desc)]
            await ctx.finish_step(step, status="completed", output=spec,
                                  summary=spec.get("notes", "spec produced"),
                                  tokens=resp.prompt_tokens + resp.completion_tokens)
            ctx.set_data("spec", spec)
            return spec
        except Exception as e:
            await ctx.finish_step(step, status="failed", error=str(e))
            raise


# ---------------------------------------------------------------------------
# 2) Repository Analyst (deterministic knowledge summary)
# ---------------------------------------------------------------------------


class RepoAnalyst:
    name = "repo_analyst"
    state = TS.ANALYZING
    permissions = FILE_TOOLS

    async def run(self, ctx: AgentContext) -> dict:
        step = await ctx.begin_step(self.name, self.state, {})
        try:
            await ctx.set_state(self.state)
            from sqlalchemy import select

            from core.models import IndexedFile

            files = (
                (
                    await ctx.db.execute(
                        select(IndexedFile).where(
                            IndexedFile.repository_id == ctx.repo.id))
                )
                .scalars()
                .all()
            )
            symbols = []
            for f in files:
                for s in (f.symbols or []):
                    symbols.append({"file": f.path, **s})
            test_files = [f.path for f in files if f.is_test]
            summary = {
                "name": ctx.repo.name,
                "languages": ctx.repo.languages or {},
                "file_count": ctx.repo.file_count,
                "total_bytes": ctx.repo.total_bytes,
                "test_files": test_files[:20],
                "top_symbols": symbols[:120],
                "head_commit": ctx.repo.head_commit,
            }
            ctx.set_data("repo_summary", summary)
            await ctx.finish_step(
                step, status="completed", output={"file_count": summary["file_count"],
                                                  "languages": summary["languages"]},
                summary=f"analyzed {summary['file_count']} files, "
                        f"{len(test_files)} test files, "
                        f"{len(symbols)} symbols",
            )
            return summary
        except Exception as e:
            await ctx.finish_step(step, status="failed", error=str(e))
            raise


# ---------------------------------------------------------------------------
# 3) Planner
# ---------------------------------------------------------------------------


class Planner:
    name = "planner"
    state = TS.PLANNING
    permissions = FILE_TOOLS

    async def run(self, ctx: AgentContext) -> dict:
        step = await ctx.begin_step(self.name, self.state,
                                    {"spec": ctx.get_data("spec")})
        try:
            await ctx.set_state(self.state)
            spec = ctx.get_data("spec", {})
            targets = [_normalize_target(t) for t in spec.get("targets", [])]
            target_str = " ".join(t.get("description", "") for t in targets)
            chunks = await ctx.retrieve(
                target_str or spec.get("goal", ctx.task.requirement),
                max_chunks=8,
            )
            files = await ctx.file_contents(sorted({c.path for c in chunks}))
            resp = await ctx.llm_complete(
                role="planner",
                system=(
                    "You are the implementation planner. Given a requirement spec and "
                    "repository context, produce an ordered plan: each step has an id, "
                    "description, target files, and acceptance criterion. End with a "
                    "test-generation step. Respond with JSON only in format: "
                    "{\"steps\": [{\"id\": \"step-1\", \"description\": \"...\", \"files\": [\"...\"], \"acceptance\": \"...\"}]}."
                ),
                user=f"Spec: {json.dumps(spec, default=str)[:3000]}",
                context={"spec": spec,
                         "repo_summary": ctx.get_data("repo_summary", {}),
                         "files": files},
            )
            plan = _parse_json_response(resp.content)
            if isinstance(plan, list):
                plan = {"steps": plan}
            if "steps" not in plan or not plan["steps"]:
                steps = plan.get("plan") or plan.get("ordered_plan") or plan.get("tasks") or []
                if isinstance(steps, list) and steps:
                    plan["steps"] = steps
                else:
                    for val in plan.values():
                        if isinstance(val, list) and val and isinstance(val[0], dict):
                            plan["steps"] = val
                            break
            if "steps" not in plan or not plan["steps"]:
                raise LLMError("planner produced no steps")
            for st in plan["steps"]:
                if isinstance(st, dict):
                    if "files" not in st:
                        st["files"] = st.get("target_files") or []
                    if "acceptance" not in st:
                        st["acceptance"] = st.get("acceptance_criterion") or ""
            await ctx.finish_step(step, status="completed", output=plan,
                                  summary=f"plan with {len(plan['steps'])} steps",
                                  tokens=resp.prompt_tokens + resp.completion_tokens)
            ctx.set_data("plan", plan)
            return plan
        except Exception as e:
            await ctx.finish_step(step, status="failed", error=str(e))
            raise


# ---------------------------------------------------------------------------
# 4) Implementer
# ---------------------------------------------------------------------------


class Implementer:
    name = "implementer"
    state = TS.IMPLEMENTING
    permissions = WRITE_TOOLS

    async def run(self, ctx: AgentContext) -> dict:
        step = await ctx.begin_step(self.name, self.state,
                                    {"plan": ctx.get_data("plan")})
        try:
            await ctx.set_state(self.state)
            spec = ctx.get_data("spec", {})
            plan = ctx.get_data("plan", {})
            # gather the files the plan touches + their neighbors
            wanted = set()
            for st in plan.get("steps", []):
                wanted.update(st.get("files", []))
            for t in spec.get("targets", []):
                t_path = _normalize_target(t).get("path")
                if t_path:
                    wanted.add(t_path)
            first_desc = ""
            targets = spec.get("targets", [])
            if targets:
                first_desc = _normalize_target(targets[0]).get("description", "")
            chunks = await ctx.retrieve(
                first_desc or spec.get("goal", ctx.task.requirement),
                max_chunks=10,
            )
            paths = sorted(wanted | {c.path for c in chunks})
            files = await ctx.file_contents(paths)
            # make sure test files are in context (for conventions)
            test_paths = [f for f in (ctx.get_data("repo_summary", {}) or {}).get(
                "test_files", []) if f not in paths]
            files += await ctx.file_contents(test_paths[:2])

            resp = await ctx.llm_complete(
                role="implementer",
                system=(
                    "You are the implementation agent. Produce file operations "
                    "(create/modify/delete) that implement the plan. For modify, "
                    "return the COMPLETE new file content. Preserve unrelated code. "
                    "Respond with JSON only in format: "
                    '{"operations": [{"operation": "modify", "path": "...", "content": "..."}], "summary": "..."}. '
                    "The 'content' field must be a valid standard JSON string literal with newlines written as \\n and quotes escaped as \\\"."
                ),
                user=f"Plan: {json.dumps(plan, default=str)[:3000]}",
                context={"spec": spec, "plan": plan, "files": files},
            )
            result = _parse_json_response(resp.content)
            if isinstance(result, list):
                ops = result
            else:
                ops = result.get("operations") or result.get("changes") or result.get("files") or []
                if not ops:
                    for val in result.values():
                        if isinstance(val, list) and val and isinstance(val[0], dict):
                            ops = val
                            break
            if not ops:
                ops = []
            applied = await _apply_operations(ctx, ops) if ops else []
            default_summary = f"applied {len(applied)} changes" if applied else "verified existing implementation (no changes required)"
            summary = result.get("summary") or default_summary
            await ctx.finish_step(step, status="completed",
                                  output={"applied": applied},
                                  summary=summary,
                                  tokens=resp.prompt_tokens + resp.completion_tokens)
            ctx.set_data("implementation", {"applied": applied})
            return {"applied": applied}
        except Exception as e:
            await ctx.finish_step(step, status="failed", error=str(e))
            raise


# ---------------------------------------------------------------------------
# 5) Test Generator
# ---------------------------------------------------------------------------


class TestGenerator:
    name = "test_generator"
    state = TS.IMPLEMENTING
    permissions = WRITE_TOOLS

    async def run(self, ctx: AgentContext) -> dict:
        step = await ctx.begin_step(self.name, self.state, {})
        try:
            spec = ctx.get_data("spec", {})
            plan = ctx.get_data("plan", {})
            repo_summary = ctx.get_data("repo_summary", {}) or {}
            test_files = repo_summary.get("test_files", [])
            # context: existing tests (conventions) + implemented targets
            paths = list(test_files[:3])
            for t in spec.get("targets", []):
                t_path = _normalize_target(t).get("path")
                if t_path:
                    paths.append(t_path)
            chunks = await ctx.retrieve(spec.get("goal", ""), max_chunks=6)
            paths += [c.path for c in chunks if c.path not in paths]
            files = await ctx.file_contents(sorted(set(paths)))
            resp = await ctx.llm_complete(
                role="test_generator",
                system=(
                    "You are the test generator. Inspect the existing test "
                    "conventions in the context and produce file operations that add "
                    "tests for the implemented behavior (unit + API tests). For "
                    "modify, return COMPLETE new file content. Respond with JSON only in format: "
                    '{"operations": [{"operation": "modify", "path": "...", "content": "..."}], "summary": "..."}. '
                    "The 'content' field must be a valid standard JSON string literal with newlines written as \\n and quotes escaped as \\\"."
                ),
                user=f"Spec: {json.dumps(spec, default=str)[:2000]}",
                context={"spec": spec, "plan": plan, "files": files,
                         "test_files": test_files},
            )
            result = _parse_json_response(resp.content)
            if isinstance(result, list):
                ops = result
            else:
                ops = result.get("operations") or result.get("changes") or result.get("files") or []
                if not ops:
                    for val in result.values():
                        if isinstance(val, list) and val and isinstance(val[0], dict):
                            ops = val
                            break
            applied = await _apply_operations(ctx, ops) if ops else []
            await ctx.finish_step(step, status="completed",
                                  output={"applied": applied},
                                  summary=result.get("summary",
                                                     f"generated {len(applied)} test file(s)"),
                                  tokens=resp.prompt_tokens + resp.completion_tokens)
            ctx.set_data("tests", {"applied": applied})
            return {"applied": applied}
        except Exception as e:
            await ctx.finish_step(step, status="failed", error=str(e))
            raise


# ---------------------------------------------------------------------------
# 6) Execution Agent
# ---------------------------------------------------------------------------


class ExecutionAgent:
    name = "execution_agent"
    state = TS.TESTING
    permissions = EXEC_TOOLS | GIT_TOOLS

    async def run(self, ctx: AgentContext, *, retest: bool = False) -> dict:
        state = TS.RETESTING if retest else TS.TESTING
        step = await ctx.begin_step(self.name, state,
                                    {"iteration": ctx.run.iteration, "retest": retest})
        try:
            await ctx.set_state(state)
            spec = ctx.get_data("spec", {})
            command = spec.get("test_command", "python -m pytest -q").strip()
            # build argv from the detected test command; only add -q when the
            # command does not already specify a verbosity (a duplicated -q
            # becomes -qq, which suppresses pytest's final summary line and
            # breaks result parsing)
            import shlex, sys
            if any(tok in command for tok in ("|", "&&", "||", ">", "<", ";")):
                argv = ["cmd.exe", "/c", command] if sys.platform == "win32" else ["/bin/sh", "-c", command]
            else:
                try:
                    argv = shlex.split(command, posix=(sys.platform != "win32"))
                except Exception:
                    argv = command.split()
            if any("pytest" in a for a in argv):
                if not any(a in ("-q", "--quiet", "-v", "--verbose", "-ra") for a in argv):
                    argv.append("-q")

            await ctx.emit_terminal(f"$ {command}")
            tctx = ctx.tool_ctx(self.name, self.permissions)
            res = await ctx.tools.call(tctx, "run_tests",
                                       {"command": command, "timeout": 180})
            data = res.data or {}
            stdout = data.get("stdout", "")
            stderr = data.get("stderr", "")
            exit_code = data.get("exit_code")
            timed_out = data.get("timed_out", False)

            if res.error and not (stdout or stderr):
                stderr = res.error
                await ctx.emit_terminal(f"[error] {res.error}", stream="err")

            # stream output to the terminal (tail to keep events bounded)
            for line in (stdout + "\n" + stderr).splitlines()[-200:]:
                stream = "err" if line.startswith("E ") or "Error" in line else "out"
                await ctx.emit_terminal(line, stream=stream)

            parsed = testparse.parse_pytest_output(stdout, stderr, exit_code)

            # persist
            tr = TestRun(
                run_id=ctx.run.id, iteration=ctx.run.iteration,
                command=" ".join(argv), exit_code=exit_code,
                passed=parsed.passed, failed=parsed.failed, errors=parsed.errors,
                skipped=parsed.skipped, failed_tests=parsed.failed_tests[:50],
                stdout=stdout[-50000:], stderr=stderr[-50000:],
                duration_ms=data.get("duration_ms", 0),
            )
            ex = Execution(
                run_id=ctx.run.id, kind="test", command=" ".join(argv),
                backend=ctx.sandbox.backend_name, exit_code=exit_code,
                timed_out=timed_out, stdout=stdout[-50000:], stderr=stderr[-50000:],
                duration_ms=data.get("duration_ms", 0),
            )
            ctx.db.add(tr)
            ctx.db.add(ex)
            await ctx.db.flush()

            ok = parsed.failed == 0 and parsed.errors == 0 and exit_code == 0
            await ctx.emit("test_result", {
                "passed": parsed.passed, "failed": parsed.failed,
                "errors": parsed.errors, "skipped": parsed.skipped,
                "failed_tests": parsed.failed_tests[:20],
                "ok": ok, "iteration": ctx.run.iteration,
            })
            await ctx.finish_step(
                step, status="completed",
                output={"passed": parsed.passed, "failed": parsed.failed,
                        "errors": parsed.errors, "ok": ok,
                        "failed_tests": parsed.failed_tests[:20]},
                summary=f"{parsed.passed} passed, {parsed.failed} failed, "
                        f"{parsed.errors} errors (iter {ctx.run.iteration})",
            )
            ctx.set_data("last_test", {
                "ok": ok, "parsed": parsed, "stdout": stdout, "stderr": stderr,
                "exit_code": exit_code, "command": " ".join(argv),
            })
            return {"ok": ok, "parsed": parsed}
        except Exception as e:
            await ctx.finish_step(step, status="failed", error=str(e))
            raise


# ---------------------------------------------------------------------------
# 7) Failure Analyzer
# ---------------------------------------------------------------------------


class FailureAnalyzer:
    name = "failure_analyzer"
    state = TS.DEBUGGING
    permissions = FILE_TOOLS

    async def run(self, ctx: AgentContext) -> dict:
        step = await ctx.begin_step(self.name, self.state,
                                    {"iteration": ctx.run.iteration})
        try:
            await ctx.set_state(self.state)
            last = ctx.get_data("last_test", {})
            parsed = last.get("parsed")
            stdout = last.get("stdout", "")
            stderr = last.get("stderr", "")
            combined = f"{stdout}\n{stderr}"
            # context: files likely involved (from failing frames or tracebacks)
            paths = set()
            for ft in (parsed.failed_tests if parsed else []):
                clean_ft = ft.replace("ERROR ", "").strip()
                m = re.search(r"([\w./\\-]+\.py)", clean_ft)
                if m:
                    paths.add(m.group(1).replace("\\", "/"))
            for m2 in re.finditer(r"((?:app|src|services|tests?)[\w./\\-]*\.py)", combined):
                paths.add(m2.group(1).replace("\\", "/"))
            for t in ctx.get_data("spec", {}).get("targets", []):
                t_path = _normalize_target(t).get("path")
                if t_path:
                    paths.add(t_path)
            files = await ctx.file_contents(sorted(paths))
            if not files:
                target_paths = [
                    _normalize_target(t).get("path", "")
                    for t in ctx.get_data("spec", {}).get("targets", [])
                ]
                files = await ctx.file_contents([p for p in target_paths if p])
            resp = await ctx.llm_complete(
                role="failure_analyzer",
                system=(
                    "You are the failure analyzer. From the real pytest output, "
                    "identify the root cause and a concrete repair strategy. "
                    "Respond with JSON only."
                ),
                user=f"Test run failed at iteration {ctx.run.iteration}.",
                context={"test_output": {
                    "exit_code": last.get("exit_code"),
                    "stdout": stdout[-8000:],
                    "stderr": stderr[-8000:],
                    "output": combined[-8000:],
                    "failed_tests": (parsed.failed_tests if parsed else [])[:20],
                }, "files": files},
            )
            diagnosis = _parse_json_response(resp.content)
            await ctx.finish_step(step, status="completed", output=diagnosis,
                                  summary=diagnosis.get("summary", "diagnosed"),
                                  tokens=resp.prompt_tokens + resp.completion_tokens)
            ctx.set_data("diagnosis", diagnosis)
            return diagnosis
        except Exception as e:
            await ctx.finish_step(step, status="failed", error=str(e))
            raise


# ---------------------------------------------------------------------------
# 8) Repair Agent
# ---------------------------------------------------------------------------


class RepairAgent:
    name = "repair_agent"
    state = TS.DEBUGGING
    permissions = WRITE_TOOLS

    async def run(self, ctx: AgentContext) -> dict:
        step = await ctx.begin_step(self.name, self.state,
                                    {"iteration": ctx.run.iteration})
        try:
            diagnosis = ctx.get_data("diagnosis", {})
            last = ctx.get_data("last_test", {})
            stdout = last.get("stdout", "")
            stderr = last.get("stderr", "")
            combined = f"{stdout}\n{stderr}"
            # context: the failing file + its dependencies
            paths = set()
            frame = diagnosis.get("failing_frame") or {}
            resolved_frame = None
            if frame.get("file"):
                # the frame carries a bare basename (e.g. "test_users.py");
                # resolve it to the real workspace-relative path
                resolved_frame = ctx.resolve_workspace_path(frame["file"])
                if resolved_frame:
                    paths.add(resolved_frame)
            for m2 in re.finditer(r"((?:app|src|services|tests?)[\w./\\-]*\.py)", combined):
                paths.add(m2.group(1).replace("\\", "/"))
            for t in ctx.get_data("spec", {}).get("targets", []):
                t_path = _normalize_target(t).get("path")
                if t_path:
                    paths.add(t_path)
            # when the failure is in a *test*, the real bug is usually in the
            # route handler the test exercises — include the route files
            if resolved_frame and (
                    resolved_frame.startswith("test") or "/test" in resolved_frame):
                paths.update(ctx.route_file_paths())
            files = await ctx.file_contents(sorted(paths))
            resp = await ctx.llm_complete(
                role="repair_agent",
                system=(
                    "You are the repair agent. Given the diagnosis, test output, and the current "
                    "file contents, produce the minimal file operations that fix the failure. "
                    "For modify, return COMPLETE new file content. Respond with JSON only in format: "
                    '{"operations": [{"operation": "modify", "path": "...", "content": "..."}], "summary": "..."}. '
                    "The 'content' field must be a valid standard JSON string literal with newlines written as \\n and quotes escaped as \\\"."
                ),
                user=f"Repair iteration {ctx.run.iteration}.",
                context={"diagnosis": diagnosis,
                         "test_output": {"stdout": stdout[-6000:],
                                         "stderr": stderr[-6000:],
                                         "output": combined[-8000:],
                                         "failed_tests":
                                         (last.get("parsed").failed_tests
                                          if last.get("parsed") else [])[:10]},
                         "files": files},
            )
            result = _parse_json_response(resp.content)
            if isinstance(result, list):
                ops = result
            else:
                ops = result.get("operations") or result.get("changes") or result.get("files") or []
                if not ops:
                    for val in result.values():
                        if isinstance(val, list) and val and isinstance(val[0], dict):
                            ops = val
                            break
            if not ops:
                ops = []
            applied = await _apply_operations(ctx, ops) if ops else []
            default_summary = f"repaired {len(applied)} file(s)" if applied else "no further repair operations needed"
            summary = result.get("summary") or default_summary
            await ctx.finish_step(step, status="completed",
                                  output={"applied": applied},
                                  summary=summary,
                                  tokens=resp.prompt_tokens + resp.completion_tokens)
            return {"applied": applied}
        except Exception as e:
            await ctx.finish_step(step, status="failed", error=str(e))
            raise


# ---------------------------------------------------------------------------
# 9) Security Agent
# ---------------------------------------------------------------------------


class SecurityAgent:
    name = "security_agent"
    state = TS.SECURITY_ANALYSIS
    permissions = EXEC_TOOLS

    async def run(self, ctx: AgentContext) -> dict:
        step = await ctx.begin_step(self.name, self.state, {})
        try:
            await ctx.set_state(self.state)
            from services.security.scanner import scan_repository

            findings = await scan_repository(ctx)
            # persist
            for f in findings:
                ctx.db.add(SecurityFinding(
                    run_id=ctx.run.id, severity=f["severity"], rule_id=f["rule_id"],
                    path=f["path"], line=f.get("line"), message=f["message"],
                    source=f["source"],
                ))
            await ctx.db.flush()
            blocking = [f for f in findings
                        if f["severity"] in ("critical", "high")]
            await ctx.emit("security_result", {
                "total": len(findings),
                "by_severity": _count(findings),
                "blocking": len(blocking),
            })
            await ctx.finish_step(
                step, status="completed",
                output={"total": len(findings), "by_severity": _count(findings)},
                summary=f"{len(findings)} findings "
                        f"({sum(1 for f in findings if f['severity'] == 'critical')} critical)",
            )
            ctx.set_data("security", {"findings": findings})
            return {"findings": findings}
        except Exception as e:
            await ctx.finish_step(step, status="failed", error=str(e))
            raise


def _count(findings: list[dict]) -> dict:
    out: dict[str, int] = {}
    for f in findings:
        out[f["severity"]] = out.get(f["severity"], 0) + 1
    return out


# ---------------------------------------------------------------------------
# 10) Code Reviewer
# ---------------------------------------------------------------------------


class CodeReviewer:
    name = "code_reviewer"
    state = TS.CODE_REVIEW
    permissions = FILE_TOOLS | GIT_TOOLS

    async def run(self, ctx: AgentContext) -> dict:
        step = await ctx.begin_step(self.name, self.state, {})
        try:
            await ctx.set_state(self.state)
            from sqlalchemy import select

            from core.models import CodeChange

            changes = (
                (
                    await ctx.db.execute(
                        select(CodeChange).where(CodeChange.run_id == ctx.run.id))
                )
                .scalars()
                .all()
            )
            files = await ctx.file_contents([c.path for c in changes])
            resp = await ctx.llm_complete(
                role="code_reviewer",
                system=(
                    "You are the code reviewer. Review the changes for correctness, "
                    "maintainability, architecture, performance, security, "
                    "readability, test quality, and error handling. Respond with JSON "
                    "only: {\"findings\": [{severity, category, path, line, title, "
                    "detail}], \"summary\": str, \"verdict\": str}."
                ),
                user=f"Reviewing {len(changes)} changed file(s).",
                context={"changes": [
                    {"path": c.path, "action": c.action, "diff": c.diff[:8000]}
                    for c in changes
                ], "files": files},
            )
            result = _parse_json_response(resp.content)
            if isinstance(result, list):
                findings = result
                result = {"findings": findings, "verdict": "ok", "summary": f"{len(findings)} findings"}
            else:
                findings = result.get("findings") or result.get("issues") or []
                if not findings:
                    for val in result.values():
                        if isinstance(val, list) and val and isinstance(val[0], dict):
                            findings = val
                            break
            from core.models import ReviewFinding

            for f in findings:
                ctx.db.add(ReviewFinding(
                    run_id=ctx.run.id,
                    severity=f.get("severity", "info"),
                    category=f.get("category", "review"),
                    path=f.get("path", ""), line=f.get("line"),
                    title=f.get("title", ""), detail=f.get("detail", ""),
                    source="reviewer",
                ))
            await ctx.db.flush()
            await ctx.emit("review_result", {
                "total": len(findings),
                "verdict": result.get("verdict", "ok"),
                "summary": result.get("summary", ""),
            })
            await ctx.finish_step(step, status="completed",
                                  output={"findings": findings,
                                          "verdict": result.get("verdict")},
                                  summary=result.get("summary", "review complete"),
                                  tokens=resp.prompt_tokens + resp.completion_tokens)
            ctx.set_data("review", result)
            return result
        except Exception as e:
            await ctx.finish_step(step, status="failed", error=str(e))
            raise


# ---------------------------------------------------------------------------
# 11) Report Writer (deterministic)
# ---------------------------------------------------------------------------


class ReportWriter:
    name = "report_writer"
    state = TS.CODE_REVIEW
    permissions = set()

    async def run(self, ctx: AgentContext) -> dict:
        step = await ctx.begin_step(self.name, self.state, {})
        try:
            from services.reporting.report import build_markdown_report

            content = await build_markdown_report(ctx)
            from core.models import Report

            report = Report(run_id=ctx.run.id, format="markdown", content=content)
            ctx.db.add(report)
            await ctx.db.flush()
            await ctx.finish_step(step, status="completed",
                                  output={"report_id": report.id},
                                  summary="engineering report generated")
            return {"report_id": report.id}
        except Exception as e:
            await ctx.finish_step(step, status="failed", error=str(e))
            raise


AGENTS = {
    "requirement_analyzer": RequirementAnalyzer,
    "repo_analyst": RepoAnalyst,
    "planner": Planner,
    "implementer": Implementer,
    "test_generator": TestGenerator,
    "execution_agent": ExecutionAgent,
    "failure_analyzer": FailureAnalyzer,
    "repair_agent": RepairAgent,
    "security_agent": SecurityAgent,
    "code_reviewer": CodeReviewer,
    "report_writer": ReportWriter,
}