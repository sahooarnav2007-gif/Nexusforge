"""Parse real pytest output into structured results (never fabricated)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ParsedTestResult:
    exit_code: int | None
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failed_tests: list[str] = field(default_factory=list)
    collected: int = 0
    no_tests: bool = False
    duration_s: float = 0.0


def parse_pytest_output(stdout: str, stderr: str, exit_code: int | None) -> ParsedTestResult:
    out = ParsedTestResult(exit_code=exit_code)
    text = stdout + "\n" + stderr

    # "no tests ran"
    if re.search(r"no tests ran", text, re.IGNORECASE):
        out.no_tests = True
        return out

    # failed test lines: "FAILED tests/test_x.py::test_y - KeyError: 'email'"
    for m in re.finditer(r"^FAILED\s+([\w./\\-]+::[\w\[\]\'\-]+)", text, re.MULTILINE):
        out.failed_tests.append(m.group(1).replace("\\", "/"))
    # errors during collection
    for m in re.finditer(r"^ERROR\s+([\w./\\-]+)", text, re.MULTILINE):
        out.failed_tests.append(m.group(1).replace("\\", "/"))

    # summary line: "===== 3 passed, 1 failed, 2 errors in 0.12s ====="
    m = re.search(
        r"=+\s*(.*?)(?:\s*in\s*([\d.]+)s)?\s*=+\s*$",
        text, re.MULTILINE)
    summary = ""
    if m:
        summary = m.group(1)
        if m.group(2):
            out.duration_s = float(m.group(2))
    # also match "short test summary info" style counts anywhere
    for pattern, attr in [
        (r"(\d+)\s+passed", "passed"),
        (r"(\d+)\s+failed", "failed"),
        (r"(\d+)\s+errors?", "errors"),
        (r"(\d+)\s+skipped", "skipped"),
    ]:
        mm = re.search(pattern, text)
        if mm:
            setattr(out, attr, int(mm.group(1)))

    # collection count: "collected N items"
    cm = re.search(r"collected\s+(\d+)\s+items?", text)
    if cm:
        out.collected = int(cm.group(1))

    # fallback: when the summary line is absent (e.g. `-qq` suppresses it),
    # count the progress line characters: '.'=passed 'F'=failed
    # 'E'=error 's'=skipped 'x'=xfail 'X'=xpass
    if out.passed == 0 and out.failed == 0 and out.errors == 0:
        pm = re.search(r"^([.FExsX]+)\s*\[100%\]", text, re.MULTILINE)
        if pm:
            prog = pm.group(1)
            out.passed = prog.count(".")
            out.failed = out.failed + prog.count("F")
            out.errors = out.errors + prog.count("E")
            out.skipped = out.skipped + prog.count("s")

    # if no counts parsed but exit code is 0 and output has "passed", treat as pass
    if out.passed == 0 and out.failed == 0 and exit_code == 0 and "passed" in text:
        pm = re.search(r"(\d+)\s+passed", text)
        if pm:
            out.passed = int(pm.group(1))
    return out