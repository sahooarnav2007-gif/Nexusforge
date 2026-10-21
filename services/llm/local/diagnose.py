"""Failure diagnosis & repair for the local provider.

Parses real pytest output (tracebacks, assertion errors, collection errors)
into a structured diagnosis, then derives a targeted repair patch when the
root cause is inside its capability envelope.
"""
from __future__ import annotations

import re

from core.errors import LLMError
from services.llm.local.synth import (
    FuncInfo,
    _find_func_in,
    _replace_function_body,
    parse_contract,
    parse_functions,
)


def _files(ctx: dict) -> dict[str, str]:
    return {f["path"]: f["content"] for f in ctx.get("files", [])}


def _last_traceback(stderr: str) -> str:
    """Extract the last traceback block from pytest output."""
    blocks = re.findall(r"Traceback \(most recent call last\):.*?(?=\n\n|\n={3,}|\Z)",
                        stderr, re.DOTALL)
    return blocks[-1] if blocks else ""


def _parse_failure_line(stderr: str) -> dict | None:
    """Parse the failing frame: file, line, code."""
    m = re.search(
        r"^(?:E\s+)?([\w./\\-]+\.py):(\d+):\s*(.*)$",
        stderr, re.MULTILINE)
    if m:
        return {"file": m.group(1).split("/")[-1], "line": int(m.group(2)),
                "code": m.group(3).strip()}
    # "app/service.py:12: KeyError: 'email'" style
    m = re.search(r"([\w./\\-]+\.py):(\d+):\s*(\w*Error|\w*Exception|\w+):?\s*(.*)$",
                  stderr)
    if m:
        return {"file": m.group(1).split("/")[-1], "line": int(m.group(2)),
                "code": f"{m.group(3)}: {m.group(4)}".strip()}
    return None


def analyze(payload: dict) -> dict:
    test_output = payload.get("test_output", {})
    stderr = test_output.get("stderr", "") or test_output.get("stdout", "")
    failed_tests = test_output.get("failed_tests", [])
    exit_code = test_output.get("exit_code")

    if exit_code == 0:
        return {"status": "pass", "root_cause": None, "summary": "tests passed"}

    tb = _last_traceback(stderr)
    frame = _parse_failure_line(stderr)
    exc_match = re.search(r"^E\s+(\w*(?:Error|Exception))[:\s]", stderr, re.MULTILINE)
    exc = exc_match.group(1) if exc_match else "UnknownError"
    exc_detail = ""
    if exc_match:
        dm = re.search(rf"^E\s+{re.escape(exc)}[:\s](.*)$", stderr, re.MULTILINE)
        if dm:
            exc_detail = dm.group(1).strip()

    # assertion failure detail (also the primary signal when no explicit
    # exception line exists, e.g. TestClient returning 500)
    assert_m = re.search(r"^E\s+assert\s+(.*)$", stderr, re.MULTILINE)
    if assert_m:
        if exc == "UnknownError":
            exc = "AssertionError"
        exc_detail = assert_m.group(1).strip()

    root_cause = _classify(exc, exc_detail, frame, tb)
    return {
        "status": "fail",
        "failed_tests": failed_tests,
        "exception": exc,
        "exception_detail": exc_detail,
        "failing_frame": frame,
        "root_cause": root_cause,
        "summary": f"{exc}: {exc_detail[:200]}" if exc_detail else exc,
        "traceback_tail": tb[-1500:] if tb else "",
    }


def _classify(exc: str, detail: str, frame: dict | None, tb: str) -> dict:
    if exc == "KeyError":
        key = detail.strip("'\" ")
        return {
            "type": "missing_key",
            "description": f"KeyError '{key}' — the data object lacks the expected key",
            "strategy": "fix the data lookup to include the missing key, or guard the access",
            "key": key,
        }
    if exc == "AttributeError":
        attr = detail.split("'")[1] if "'" in detail else detail
        return {
            "type": "missing_attribute",
            "description": f"AttributeError on '{attr}'",
            "strategy": "the object does not expose that attribute; fix construction or access",
        }
    if exc == "NameError":
        name = detail.split("'")[1] if "'" in detail else detail
        return {
            "type": "undefined_name",
            "description": f"NameError: '{name}' is not defined",
            "strategy": "add the missing import or define the name",
            "name": name,
        }
    if exc == "TypeError":
        return {
            "type": "type_error",
            "description": f"TypeError: {detail}",
            "strategy": "argument/return type mismatch; align the call with the signature",
        }
    if exc == "AssertionError":
        return {
            "type": "assertion",
            "description": f"Assertion failed: {detail[:200]}",
            "strategy": "the implemented behavior does not match the test expectation",
        }
    if exc == "ImportError":
        mod = detail.split("cannot import name")[-1].strip(" :")
        return {
            "type": "import_error",
            "description": f"ImportError: {detail}",
            "strategy": "missing module or symbol; check imports and module layout",
            "module": mod,
        }
    if "collection" in (detail or "").lower() or "no tests ran" in (detail or "").lower():
        return {
            "type": "collection_error",
            "description": "pytest could not collect tests",
            "strategy": "fix import/collection errors in test files",
        }
    return {
        "type": "unclassified",
        "description": f"{exc}: {detail[:200]}",
        "strategy": "inspect the failing frame and fix the indicated code path",
    }


def repair(payload: dict) -> dict:
    """Derive a repair patch from the diagnosis + repository context."""
    diagnosis = payload.get("diagnosis", {})
    files = _files(payload)
    rc = diagnosis.get("root_cause", {})
    frame = diagnosis.get("failing_frame") or {}

    # Strategy 0: the failing frame is in a *test* file and the assertion is a
    # status-code mismatch (e.g. `assert 200 == 404`) — the real bug is a route
    # handler that returns 200 for a missing item. Repair the route directly.
    if rc.get("type") == "assertion" and frame.get("file", "").endswith(".py"):
        route_fix = _repair_route_status(files, diagnosis)
        if route_fix:
            return route_fix

    # locate the failing function in the context
    target_path = None
    target_fn: FuncInfo | None = None
    if frame.get("file"):
        for path in files:
            if path.endswith(frame["file"]):
                target_path = path
                break
    if target_path is None:
        target_path = next(iter(files), None)

    # find the function containing the failing line
    if target_path:
        for fn in parse_functions(files[target_path]):
            if frame.get("line") and fn.start_line <= frame["line"] <= fn.end_line:
                target_fn = fn
                break
    if target_fn is None and target_path:
        fns = parse_functions(files[target_path])
        target_fn = fns[0] if fns else None

    if target_fn is None or target_path is None:
        raise LLMError(
            "local provider: repair target not found in context "
            "(capability envelope: failures inside a known Python function)"
        )

    contract = parse_contract(target_fn.docstring)
    strategy = rc.get("strategy", "")
    rtype = rc.get("type", "")

    # Strategy 1: missing key -> make the lookup return a dict that includes it
    # (the body likely does `return store.get_user(user_id)` where the store
    #  returns a dict missing the key; we cannot change the store here, so we
    #  normalize the returned dict to include the documented keys.)
    if rtype == "missing_key":
        key = rc.get("key", "")
        new_body = _normalize_body(target_fn, key)
        content = _replace_function_body(files[target_path], target_fn, new_body)
        return {
            "operations": [{
                "op": "modify",
                "path": target_path,
                "content": content,
                "reasoning": (
                    f"Repair for KeyError '{key}': normalize the returned object so it "
                    f"includes the key documented in the contract"
                ),
            }],
            "summary": f"patched {target_fn.name}() to include key '{key}'",
        }

    # Strategy 2: undefined name -> add import
    if rtype == "undefined_name":
        name = rc.get("name", "")
        import_line = _infer_import(name, files)
        if import_line:
            content = files[target_path]
            lines = content.splitlines(keepends=True)
            last_import = 0
            for i, l in enumerate(lines):
                if l.startswith(("import ", "from ")):
                    last_import = i
            lines.insert(last_import + 1, import_line + "\n")
            return {
                "operations": [{
                    "op": "modify",
                    "path": target_path,
                    "content": "".join(lines),
                    "reasoning": f"Added missing import for '{name}'",
                }],
                "summary": f"added import for {name}",
            }

    # Strategy 3: incomplete model construction -> map ALL model fields.
    # Try the failing function first; if that is not the culprit (e.g. the
    # failing frame is a router delegating to a service), scan the other
    # context files for a function that constructs a model with a subset of
    # its fields — that is the real bug (demo: UserProfile built with 2 of 5).
    model_body = _complete_model_body(target_fn, files)
    if model_body:
        content = _replace_function_body(files[target_path], target_fn, model_body)
        return _repair_result(target_path, target_fn, content)
    for path, content_src in files.items():
        if path == target_path or not path.endswith(".py"):
            continue
        for fn in parse_functions(content_src):
            body = _complete_model_body(fn, files)
            if body:
                new_content = _replace_function_body(content_src, fn, body)
                return _repair_result(path, fn, new_content)

    # Strategy 4: assertion/type -> re-synthesize the body from the contract
    body = _resynthesize(target_fn, files, rc)
    if body:
        content = _replace_function_body(files[target_path], target_fn, body)
        return {
            "operations": [{
                "op": "modify",
                "path": target_path,
                "content": content,
                "reasoning": f"Re-synthesized {target_fn.name}() per contract after {rtype}",
            }],
            "summary": f"re-synthesized {target_fn.name}()",
        }

    raise LLMError(
        "local provider: no repair strategy available for "
        f"root-cause type '{rtype}' within its capability envelope"
    )


def _guess_var(fn: FuncInfo) -> str:
    return "obj"


def _first_call(fn: FuncInfo) -> str:
    for line in fn.body.splitlines():
        s = line.strip()
        if s.startswith("return") or "=" in s:
            return s
    return ""


def _normalize_body(fn: FuncInfo, key: str) -> str:
    """Rewrite the body so the returned dict includes `key`."""
    lines = [l for l in fn.body.splitlines()]
    # find the lookup assignment line (first non-docstring line with '=')
    lookup = None
    for l in lines:
        s = l.strip()
        if s and not s.startswith(("return", "raise", "#", '"""', "'''")) and "=" in s:
            lookup = s
            break
    if lookup is None:
        raise LLMError("local provider: no lookup line found to normalize")
    var = lookup.split("=")[0].strip()
    if not var.isidentifier():
        raise LLMError("local provider: could not identify lookup variable")
    return (
        f"{lookup}\n"
        f"if {var} is None:\n"
        f"    raise KeyError({key!r})\n"
        f"result = dict({var})\n"
        f"result.setdefault({key!r}, result.get({key!r}, ''))\n"
        f"return result"
    )


def _repair_result(path: str, fn: FuncInfo, content: str) -> dict:
    return {
        "operations": [{
            "op": "modify",
            "path": path,
            "content": content,
            "reasoning": (
                f"Completed the model construction in {fn.name}() so every "
                f"required field is mapped from the source record"
            ),
        }],
        "summary": f"completed model field mapping in {fn.name}()",
    }


def _find_ctor_span(body: str, cls_name: str) -> tuple[int, int] | None:
    """Locate `cls_name(` ... matching `)` in the body (paren-balanced)."""
    idx = body.find(f"{cls_name}(")
    if idx == -1:
        return None
    start = idx + len(cls_name)
    depth = 0
    for i in range(start, len(body)):
        if body[i] == "(":
            depth += 1
        elif body[i] == ")":
            depth -= 1
            if depth == 0:
                return idx, i
    return None


def _complete_model_body(fn: FuncInfo, files: dict[str, str]) -> str | None:
    """If the function constructs a known model class with a SUBSET of its
    fields, rewrite that construction to map every field from the source
    record. Returns None when not applicable."""
    from services.llm.local.synth import _find_class_in

    # find the model class the function constructs
    model_cls = None
    for m in re.finditer(r"([A-Z]\w*)\s*\(", fn.body):
        name = m.group(1)
        if name in ("dict", "list", "set", "tuple", "KeyError", "ValueError"):
            continue
        hit = _find_class_in(files, name)
        if hit and hit[1]["fields"]:
            model_cls = (name, hit[1]["fields"])
            break
    if model_cls is None:
        return None
    cls_name, cls_fields = model_cls
    span = _find_ctor_span(fn.body, cls_name)
    if span is None:
        return None
    ctor_text = fn.body[span[0]:span[1] + 1]
    kw_names = set(re.findall(r"(\w+)\s*=", ctor_text))
    missing = [f for f in cls_fields if f not in kw_names]
    if not missing:
        return None
    # find the source-record variable (first plain assignment in the body)
    src_var = None
    for line in fn.body.splitlines():
        s = line.strip()
        if s and "=" in s and not s.startswith(("return", "raise", "#", '"""', "'''")):
            var = s.split("=")[0].strip()
            if var.isidentifier() and var not in ("cls", "self"):
                src_var = var
                break
    if src_var is None:
        return None
    # rebuild: replace the constructor span with the full mapping.
    # Determine the indentation of the line that contains the constructor so
    # the rewritten call keeps the surrounding block's alignment (kwargs one
    # level deeper, closing paren back at the call's level).
    body_lines = fn.body.splitlines()
    ctor_line_no = fn.body[:span[0]].count("\n")
    call_indent = ""
    if ctor_line_no < len(body_lines):
        call_indent = body_lines[ctor_line_no][:len(body_lines[ctor_line_no])
                                                 - len(body_lines[ctor_line_no].lstrip())]
    kw_indent = call_indent + "    "
    lines: list[str] = []
    for f in cls_fields:
        lines.append(kw_indent + f"{f}={src_var}[{f!r}],")
    new_ctor = f"{cls_name}(\n" + "\n".join(lines) + "\n" + call_indent + ")"
    new_body = fn.body[:span[0]] + new_ctor + fn.body[span[1] + 1:]
    return new_body


def _resynthesize(fn: FuncInfo, files: dict[str, str], rc: dict) -> str | None:
    from services.llm.local.synth import _synthesize_body

    try:
        contract = parse_contract(fn.docstring)
        return _synthesize_body(fn, contract, files)
    except LLMError:
        return None


def _infer_import(name: str, files: dict[str, str]) -> str | None:
    # search the context for where `name` is defined
    for path, content in files.items():
        if re.search(rf"^(class|def)\s+{re.escape(name)}\b", content, re.MULTILINE):
            mod = path.rsplit("/", 1)[-1].removesuffix(".py")
            return f"from {mod} import {name}"
    builtin_map = {"json": "import json", "re": "import re", "math": "import math"}
    return builtin_map.get(name)


# ---------------------------------------------------------------------------
# Route status-code repair
# ---------------------------------------------------------------------------
#
# A common class of red test: the test asserts an error status for a missing
# item (``assert resp.status_code == 404``) but the route returns 200 with an
# empty/null body, because the backing lookup returns ``None`` instead of
# raising. The failing frame is in the *test* file, so the usual
# "find the function at the failing line" repair cannot locate the culprit.
# This strategy recovers the expected status from the assertion, finds the
# route handler that serves the failing test's URL, and adds an
# empty-result guard so the handler returns the expected status.


def _failing_test_url(files: dict[str, str], diagnosis: dict) -> tuple[str | None, str | None]:
    """Return (url, method) of the request the failing test makes."""
    frame = diagnosis.get("failing_frame") or {}
    test_file = None
    if frame.get("file"):
        for p in files:
            if p.endswith(frame["file"]):
                test_file = p
                break
    if test_file is None:
        return None, None
    content = files[test_file]
    test_name = None
    for ft in (diagnosis.get("failed_tests") or []):
        m = re.search(r"::(\w+)$", ft)
        if m:
            test_name = m.group(1)
            break
    scope = content
    if test_name:
        mfn = re.search(
            rf"def {re.escape(test_name)}\b.*?(?=\ndef |\nclass |\Z)",
            content, re.DOTALL)
        if mfn:
            scope = mfn.group(0)
    urls = re.findall(
        r"client\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']", scope)
    if not urls:
        return None, None
    method, url = urls[-1]  # the request under test (usually the last one)
    return url, method


def _find_route_handler(files: dict[str, str], method: str, url: str):
    """Locate the route handler serving `url` (template match). Returns
    (path, FuncInfo) or None."""
    import ast

    from services.llm.local.synth import _path_matches_template, parse_functions

    for path, content in files.items():
        if "APIRouter" not in content and "FastAPI(" not in content:
            continue
        prefix = ""
        mp = re.search(r"APIRouter\(\s*prefix\s*=\s*[\"']([^\"']*)[\"']", content)
        if mp:
            prefix = mp.group(1)
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == method):
                    continue
                if not (dec.args and isinstance(dec.args[0], ast.Constant)):
                    continue
                tpl = dec.args[0].value
                full = (prefix.rstrip("/") + tpl) if prefix else tpl
                if not _path_matches_template(url, full):
                    continue
                for f in parse_functions(content):
                    if f.name == node.name and f.start_line == node.lineno:
                        return path, f
    return None


def _extract_return_expr(body: str) -> str | None:
    """The first `return <expr>` in a function body (the main result)."""
    for line in body.splitlines():
        m = re.match(r"\s*return\s+(.+)$", line)
        if m:
            return m.group(1).strip()
    return None


def _repair_route_status(files: dict[str, str], diagnosis: dict) -> dict | None:
    """Fix a route that returns the wrong status for an empty lookup.

    Triggered by an assertion like ``assert 200 == 404`` where the test
    expects an error status for a missing item. Rewrites the handler to
    return the expected status when the result is ``None`` (and to keep
    mapping ``KeyError`` to it).
    """
    detail = diagnosis.get("exception_detail", "") or ""
    m = re.match(r"^\s*(\d{3})\s*==\s*(\d{3})\s*$", detail)
    if not m:
        return None
    actual, expected = int(m.group(1)), int(m.group(2))
    if not (400 <= expected <= 599 and 200 <= actual < 300):
        return None

    url, method = _failing_test_url(files, diagnosis)
    if url is None or method is None:
        return None
    hit = _find_route_handler(files, method, url)
    if hit is None:
        return None
    path, fn = hit

    svc_call = _extract_return_expr(fn.body)
    if not svc_call:
        return None

    # canonical body: call the lookup, map KeyError -> expected, and guard
    # the empty (None) result -> expected
    body = (
        f"try:\n"
        f"    _result = {svc_call}\n"
        f"except KeyError:\n"
        f"    raise HTTPException(status_code={expected}, detail=\"not found\")\n"
        f"if _result is None:\n"
        f"    raise HTTPException(status_code={expected}, detail=\"not found\")\n"
        f"return _result"
    )
    new_content = _replace_function_body(files[path], fn, body)
    # ensure HTTPException is imported
    if "HTTPException" in new_content and "HTTPException" not in files[path].split(
            "def ", 1)[0]:
        new_content = new_content.replace(
            "from fastapi import APIRouter",
            "from fastapi import APIRouter, HTTPException", 1)
    return {
        "operations": [{
            "op": "modify",
            "path": path,
            "content": new_content,
            "reasoning": (
                f"Route {method.upper()} {url} returned {actual} for a missing "
                f"item; added an empty-result guard so it returns {expected}"
            ),
        }],
        "summary": (
            f"made route {method.upper()} {url} return {expected} for empty results"
        ),
    }