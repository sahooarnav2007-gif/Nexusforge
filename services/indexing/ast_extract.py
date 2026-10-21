"""Symbol extraction.

Python uses the stdlib `ast` (accurate). Other languages use a conservative
regex extractor for top-level definitions. Returns a uniform shape:
[{name, kind, line, end_line}] and a list of imported module names.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field


@dataclass
class Symbol:
    name: str
    kind: str  # function|class|method|module|variable
    line: int
    end_line: int

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "line": self.line,
                "end_line": self.end_line}


def extract_python(text: str) -> tuple[list[Symbol], list[str]]:
    symbols: list[Symbol] = []
    imports: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _regex_symbols(text, py=True), _regex_imports(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(Symbol(node.name, "function", node.lineno,
                                  getattr(node, "end_lineno", node.lineno) or node.lineno))
        elif isinstance(node, ast.ClassDef):
            symbols.append(Symbol(node.name, "class", node.lineno,
                                  getattr(node, "end_lineno", node.lineno) or node.lineno))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(Symbol(
                        f"{node.name}.{sub.name}", "method", sub.lineno,
                        getattr(sub, "end_lineno", sub.lineno) or sub.lineno,
                    ))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module.split(".")[0])
    return symbols, imports


# --- generic regex fallback -------------------------------------------------

_FUNC_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("function", re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
    ("class", re.compile(r"^(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\()")),
    ("function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")),  # go
    ("function", re.compile(r"^(?:pub\s+)?fn\s+([A-Za-z_]\w*)")),          # rust
    ("class", re.compile(r"^(?:public\s+|abstract\s+|final\s+)*class\s+([A-Za-z_]\w*)")),
    ("function", re.compile(r"^def\s+([A-Za-z_]\w*)")),
    ("function", re.compile(r"^(\w+)\s*\(.*\)\s*\{?\s*$")),
]

_IMPORT_PATTERNS: list[re.Pattern] = [
    re.compile(r"""^import\s+(?:\w+\s+from\s+)?["']([^"']+)["']"""),
    re.compile(r"""^from\s+["']([^"']+)["']"""),
    re.compile(r"""^require\(["']([^"']+)["']\)"""),
    re.compile(r"""^import\s+["']([^"']+)["']"""),
    re.compile(r"""^#include\s*[<"]([^>"]+)[>"]"""),
]


def _regex_symbols(text: str, py: bool = False) -> list[Symbol]:
    symbols: list[Symbol] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", "*", '"')):
            continue
        for kind, pat in _FUNC_PATTERNS:
            m = pat.match(stripped)
            if m:
                symbols.append(Symbol(m.group(1), kind, i, i))
                break
    return symbols


def _regex_imports(text: str) -> list[str]:
    imports: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        for pat in _IMPORT_PATTERNS:
            m = pat.match(stripped)
            if m:
                imports.append(m.group(1).split(".")[0])
                break
    return imports


def extract_symbols(language: str, text: str) -> tuple[list[Symbol], list[str]]:
    if language == "python":
        return extract_python(text)
    return _regex_symbols(text), _regex_imports(text)