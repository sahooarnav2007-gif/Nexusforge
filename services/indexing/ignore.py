"""Minimal .gitignore-style ignore matcher (no external deps).

Supports: comments, blank lines, negation (!), directory patterns (dir/),
gitignore wildcards (*, ?, **), and leading-slash anchoring.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field


@dataclass
class _Rule:
    pattern: str
    negate: bool
    is_dir: bool
    regex: re.Pattern


def _compile(pattern: str, is_dir: bool, anchored: bool = True) -> re.Pattern:
    # translate gitignore glob to regex
    i = 0
    out = []
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # ** matches across path separators
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c in ".+()|^$\\{}[]":
            out.append("\\" + c)
        else:
            out.append(c)
        i += 1
    body = "".join(out)
    # a pattern with no slash matches at any depth — wrap AFTER translation
    # (never pre-wrap the glob text, or the regex chars get re-escaped)
    if not anchored and "/" not in pattern:
        body = "(?:.*/)?" + body
    if is_dir:
        body = body + "(/.*)?"
    return re.compile("^" + body + "$")


class IgnoreMatcher:
    def __init__(self) -> None:
        self._rules: list[_Rule] = []

    def add_line(self, line: str) -> None:
        line = line.rstrip("\n").strip()
        if not line or line.startswith("#"):
            return
        negate = line.startswith("!")
        if negate:
            line = line[1:].lstrip()
        is_dir = line.endswith("/")
        if is_dir:
            line = line[:-1].rstrip("/")
        if not line:
            return
        # leading slash anchors to root
        anchored = line.startswith("/")
        if anchored:
            line = line.lstrip("/")
        self._rules.append(_Rule(line, negate, is_dir, _compile(line, is_dir, anchored)))

    def load(self, text: str) -> None:
        for line in text.splitlines():
            self.add_line(line)

    def is_ignored(self, rel_path: str, is_dir: bool = False) -> bool:
        rel_path = rel_path.lstrip("/")
        ignored = False
        for rule in self._rules:
            if rule.regex.match(rel_path):
                ignored = not rule.negate
        return ignored


def build_default_ignores() -> IgnoreMatcher:
    """Built-in ignores applied even without a .gitignore."""
    from services.indexing.languages import NEVER_INDEX_DIRS

    m = IgnoreMatcher()
    for d in NEVER_INDEX_DIRS:
        if d.startswith("*"):
            continue
        m.add_line(d + "/")
    m.add_line("*.pyc")
    m.add_line("*.lock")
    m.add_line(".env")
    m.add_line(".env.*")
    return m


def load_gitignore(root) -> IgnoreMatcher:
    """Merge built-in ignores with any .gitignore at the repo root."""
    from pathlib import Path

    m = build_default_ignores()
    gi = Path(root) / ".gitignore"
    if gi.exists():
        try:
            m.load(gi.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return m


def fnmatch_ignore(rel_path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(
            rel_path, f"*/{pat}"
        ):
            return True
    return False