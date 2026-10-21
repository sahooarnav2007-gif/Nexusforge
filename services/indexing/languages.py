"""Language detection by file extension/name."""
from __future__ import annotations

from pathlib import Path

EXT_MAP: dict[str, str] = {
    # python
    ".py": "python", ".pyi": "python",
    # js/ts
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    # web
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
    ".less": "less", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".env": "env",
    # data
    ".md": "markdown", ".rst": "rst", ".txt": "text", ".csv": "csv",
    ".xml": "xml",
    # compiled
    ".rs": "rust", ".go": "go", ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".scala": "scala", ".lua": "lua", ".sh": "shell", ".bash": "shell",
    ".sql": "sql", ".dockerfile": "dockerfile",
}

NAME_MAP: dict[str, str] = {
    "dockerfile": "dockerfile", "makefile": "makefile", "cmakelists.txt": "cmake",
    "requirements.txt": "requirements", "pyproject.toml": "toml",
    "package.json": "json", "setup.py": "python",
}

# languages the platform can meaningfully analyze (AST or symbols)
ANALYZABLE = {"python", "javascript", "typescript", "go", "rust", "java", "ruby", "c", "cpp"}

# files that are never indexed (binary-ish or generated)
NEVER_INDEX_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pyc", ".pyo", ".class", ".jar", ".wasm",
    ".lock",
}

NEVER_INDEX_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt", ".cache", ".idea", ".vscode",
    "coverage", ".coverage", "target", ".eggs", "*.egg-info",
}


def detect_language(path: str) -> str:
    p = Path(path)
    name = p.name.lower()
    if name in NAME_MAP:
        return NAME_MAP[name]
    return EXT_MAP.get(p.suffix.lower(), "unknown")


def is_binary(path: str) -> bool:
    return Path(path).suffix.lower() in NEVER_INDEX_EXTS


def is_analyzable(language: str) -> bool:
    return language in ANALYZABLE