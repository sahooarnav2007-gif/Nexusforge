"""Repository indexing: traversal → metadata → symbols → chunks → embeddings.

Incremental: files whose sha256 is unchanged since the last index are skipped.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.logging import get_logger
from core.models import FileChunk, IndexedFile, Repository
from services.indexing.ast_extract import extract_symbols
from services.indexing.chunking import chunk_file
from services.indexing.ignore import load_gitignore
from services.indexing.languages import detect_language, is_binary
from services.indexing.secrets import scan_file
from services.indexing.vectorizer import vectorize

log = get_logger("nexusforge.indexer")

TEST_PATH_HINTS = ("/test/", "/tests/", "/spec/", "test_", "_test.", "tests.",
                   "conftest.py", "pytest.ini")


def _is_test_path(rel: str) -> bool:
    low = rel.lower()
    return any(h in low for h in TEST_PATH_HINTS)


def _read_text(path: Path, limit: int) -> str | None:
    try:
        if path.stat().st_size > limit:
            return None
        return path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None


async def index_repository(db: AsyncSession, repo: Repository) -> Repository:
    s = get_settings()
    root = Path(repo.workspace_path)
    if not root.is_dir():
        raise ValueError(f"Workspace missing for repo {repo.id}: {root}")

    ignore = load_gitignore(root)
    existing: dict[str, IndexedFile] = {
        f.path: f for f in (
            await db.execute(select(IndexedFile).where(IndexedFile.repository_id == repo.id))
        ).scalars()
    }

    files_indexed = 0
    total_bytes = 0
    languages: dict[str, int] = {}
    skipped_binary = 0
    secret_flagged: list[str] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        # ignore rules (directory-level first for efficiency)
        if ignore.is_ignored(rel):
            continue
        if is_binary(rel):
            skipped_binary += 1
            continue
        if files_indexed >= s.index_max_files:
            log.warning("index file cap reached", extra={"cap": s.index_max_files})
            break

        text = _read_text(path, s.index_max_file_bytes)
        if text is None:
            skipped_binary += 1
            continue

        language = detect_language(rel)
        sha = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        lines = text.splitlines()

        # incremental: unchanged files keep their chunks
        prev = existing.get(rel)
        if prev is not None and prev.sha256 == sha:
            files_indexed += 1
            total_bytes += path.stat().st_size
            languages[language] = languages.get(language, 0) + 1
            continue

        symbols, imports = extract_symbols(language, text)
        secret_hits = scan_file(rel, text)
        flagged = bool(secret_hits)
        if flagged:
            secret_flagged.append(rel)

        file_row = prev or IndexedFile(repository_id=repo.id, path=rel)
        file_row.language = language
        file_row.size_bytes = len(text.encode("utf-8", errors="replace"))
        file_row.sha256 = sha
        file_row.line_count = len(lines)
        file_row.symbols = [sy.to_dict() for sy in symbols]
        file_row.imports = imports
        file_row.is_test = _is_test_path(rel)
        file_row.secret_flagged = flagged
        db.add(file_row)

        # chunks (secret-flagged files are NOT chunked — content stays out of LLM context)
        await db.execute(delete(FileChunk).where(
            FileChunk.repository_id == repo.id, FileChunk.path == rel))
        if not flagged:
            for chunk in chunk_file(language, text, symbols,
                                    max_lines=s.index_default_chunk_lines * 2):
                db.add(FileChunk(
                    repository_id=repo.id,
                    path=rel,
                    seq=chunk.seq,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    text=chunk.text,
                    token_estimate=max(1, len(chunk.text) // 4),
                    embedding=vectorize(chunk.text),
                    symbols=chunk.symbols,
                ))

        files_indexed += 1
        total_bytes += file_row.size_bytes
        languages[language] = languages.get(language, 0) + 1

    # drop files that no longer exist
    for rel, f in existing.items():
        if not (root / rel).exists():
            await db.execute(delete(FileChunk).where(
                FileChunk.repository_id == repo.id, FileChunk.path == rel))
            await db.delete(f)

    repo.indexed = True
    repo.index_version += 1
    repo.file_count = files_indexed
    repo.total_bytes = total_bytes
    repo.languages = languages
    repo.head_commit = _head(root)
    await db.flush()
    log.info("repository indexed", extra={
        "repo": repo.name, "files": files_indexed, "languages": languages,
        "secret_flagged": len(secret_flagged),
    })
    return repo


def _head(root: Path) -> str:
    try:
        from services.gitops import git

        return git.head_commit(root)
    except Exception:
        return ""