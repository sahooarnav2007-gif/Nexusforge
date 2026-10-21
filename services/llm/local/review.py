"""Heuristic code review for the local provider.

Reviews the produced diff for correctness, maintainability, security, and
test-quality signals. Findings are structured (severity/category/path/line).
"""
from __future__ import annotations

import re

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def review(payload: dict) -> dict:
    changes = payload.get("changes", [])  # [{path, diff, action}]
    files = {f["path"]: f["content"] for f in payload.get("files", [])}
    findings: list[dict] = []
    seq = 0

    def add(sev: str, category: str, path: str, line: int | None,
            title: str, detail: str) -> None:
        nonlocal seq
        seq += 1
        findings.append({
            "id": seq, "severity": sev, "category": category, "path": path,
            "line": line, "title": title, "detail": detail,
        })

    for ch in changes:
        path = ch.get("path", "")
        diff = ch.get("diff", "")
        added = [l[1:] for l in diff.splitlines()
                 if l.startswith("+") and not l.startswith("+++")]
        content = files.get(path, "")

        # --- security heuristics on added lines ---
        for i, line in enumerate(added):
            s = line.strip()
            if re.search(r"(?i)(api[_-]?key|secret|password|token)\s*=\s*['\"][^'\"]{8,}['\"]", s):
                add("critical", "security", path, None,
                    "Hard-coded secret in new code",
                    f"Line adds what looks like a literal credential: {s[:80]}")
            if "subprocess" in s and re.search(r"shell\s*=\s*True", s):
                add("high", "security", path, None,
                    "subprocess with shell=True",
                    "shell=True enables command injection; pass an argv list instead")
            if re.search(r"eval\(|exec\(", s):
                add("high", "security", path, None,
                    "Use of eval/exec", "Dynamic code execution is a code-injection risk")
            if "pickle.loads" in s:
                add("medium", "security", path, None,
                    "pickle deserialization", "Unpickling untrusted data can execute code")
            if re.search(r"requests\.(get|post)\(.*\+", s) or \
               re.search(r"cursor\.execute\(.*\+", s):
                add("high", "security", path, None,
                    "String-built query/request",
                    "String concatenation in queries/requests is an injection risk; use parameters")

        # --- correctness heuristics ---
        if re.search(r"except\s+:\s*$", "\n".join(added)):
            add("medium", "correctness", path, None,
                "Bare except clause",
                "Bare except swallows all exceptions including KeyboardInterrupt; catch specific types")
        if re.search(r"except\s+\w*Error\s*:\s*$", "\n".join(added)) and \
           "pass" in "\n".join(added):
            add("low", "correctness", path, None,
                "Silent exception swallow",
                "Exception is caught and ignored; log it or re-raise")

        # --- maintainability ---
        if len(added) > 60:
            add("low", "maintainability", path, None,
                "Large change in one file",
                f"{len(added)} added lines; consider splitting into smaller units")
        # long functions in new code
        for m in re.finditer(r"^def\s+(\w+)\(", content, re.MULTILINE):
            # rough length check
            start = m.start()
            nxt = re.search(r"^def\s+\w+\(|^class\s+\w+", content[m.end():], re.MULTILINE)
            end = m.end() + (nxt.start() if nxt else len(content) - m.end())
            fn_text = content[start:end]
            if fn_text.count("\n") > 60:
                add("low", "maintainability", path, None,
                    f"Long function {m.group(1)}",
                    "Functions over ~60 lines are harder to test and review")

        # --- error handling: endpoints without try/except for service calls ---
        if "HTTPException" in content and path.endswith(".py"):
            for m in re.finditer(r"@router\.(get|post|put|patch|delete)\((.*?)\)\n(def \w+\(.*?\):)",
                                 content):
                block_end = m.end()
                nxt_def = re.search(r"\n@router\.|\ndef \w+\(|\nclass \w+", content[block_end:])
                block = content[block_end: block_end + (nxt_def.start() if nxt_def else 2000)]
                if "try:" not in block and "service" in block:
                    add("medium", "error-handling", path, None,
                        "Endpoint delegates to service without explicit error mapping",
                        "Service exceptions may surface as 500s; map them to HTTP errors")

        # --- test quality: new tests without assertions ---
        if "/test" in path or path.startswith("test"):
            for m in re.finditer(r"^def (test_\w+)\(", content, re.MULTILINE):
                start = m.start()
                nxt = re.search(r"^def \w+\(", content[m.end():], re.MULTILINE)
                end = m.end() + (nxt.start() if nxt else len(content) - m.end())
                block = content[start:end]
                if not re.search(r"\b(assert|pytest\.raises|\.assert)\b", block):
                    add("medium", "test-quality", path, None,
                        f"Test {m.group(1)} has no assertion",
                        "A test without an assertion cannot fail; add an assertion")

    # --- overall ---
    if not findings:
        add("info", "review", "", None,
            "No issues detected",
            "Heuristic review found no correctness, security, or style concerns in the diff")

    findings.sort(key=lambda f: _SEV_ORDER.get(f["severity"], 9))
    summary = (
        f"{sum(1 for f in findings if f['severity'] in ('critical', 'high'))} blocking, "
        f"{sum(1 for f in findings if f['severity'] == 'medium')} medium, "
        f"{sum(1 for f in findings if f['severity'] in ('low', 'info'))} minor"
    )
    return {"findings": findings, "summary": summary,
            "verdict": "needs-attention" if any(
                f["severity"] in ("critical", "high") for f in findings) else "ok"}