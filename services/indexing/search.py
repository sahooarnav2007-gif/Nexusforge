"""Hybrid code retrieval: BM25 + local-embedding cosine, fused with
Reciprocal-Rank Fusion, plus metadata filters and dependency-aware boosting.

This is the "intelligent context selection" layer: agents never dump the whole
repository into a prompt — they query for what they need.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import FileChunk, IndexedFile
from services.indexing.vectorizer import cosine

_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*|\d+")
_RRF_K = 60


@dataclass
class RetrievalQuery:
    text: str
    languages: list[str] | None = None      # metadata filter
    path_globs: list[str] | None = None     # e.g. ["app/*", "tests/*"]
    include_tests: bool = True
    max_chunks: int = 12
    token_budget: int = 12000
    boost_symbols: list[str] | None = None  # dependency-aware: symbols we already know about


@dataclass
class RetrievedChunk:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float
    symbols: list[str] = field(default_factory=list)
    token_estimate: int = 0


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _glob_match(path: str, pattern: str) -> bool:
    import fnmatch

    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, f"*/{pattern}")


class SearchEngine:
    """Loads chunks for a repository into memory and answers queries."""

    def __init__(self) -> None:
        self._cache: dict[str, list[dict]] = {}  # repo_id -> chunk dicts

    async def load(self, db: AsyncSession, repo_id: str) -> None:
        rows = (
            (
                await db.execute(
                    select(FileChunk).where(FileChunk.repository_id == repo_id)
                )
            )
            .scalars()
            .all()
        )
        self._cache[repo_id] = [
            {
                "path": r.path,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "text": r.text,
                "tokens": _tokens(r.text),
                "embedding": r.embedding or [],
                "symbols": r.symbols or [],
                "token_estimate": r.token_estimate,
            }
            for r in rows
        ]

    async def invalidate(self, repo_id: str) -> None:
        self._cache.pop(repo_id, None)

    async def search(self, db: AsyncSession, repo_id: str, q: RetrievalQuery) -> list[RetrievedChunk]:
        if repo_id not in self._cache:
            await self.load(db, repo_id)
        chunks = self._cache.get(repo_id, [])
        if not chunks:
            return []

        # 1) metadata filter
        filtered = []
        for c in chunks:
            if q.languages:
                lang = _path_lang(c["path"])
                if lang not in q.languages:
                    continue
            if q.path_globs and not any(_glob_match(c["path"], g) for g in q.path_globs):
                continue
            if not q.include_tests and _is_test(c["path"]):
                continue
            filtered.append(c)

        # 2) BM25 ranking
        query_tokens = _tokens(q.text)
        bm25 = _bm25_rank(query_tokens, filtered)

        # 3) embedding ranking
        from services.indexing.vectorizer import vectorize

        qvec = vectorize(q.text)
        emb_scores = [cosine(qvec, c["embedding"]) for c in filtered]

        # 4) RRF fusion
        fused = _rrf([bm25, emb_scores])

        # 5) dependency-aware boost: chunks defining/mentioning known symbols
        if q.boost_symbols:
            for i, c in enumerate(filtered):
                names = set(c["symbols"])
                text_low = c["text"].lower()
                for sym in q.boost_symbols:
                    if sym in names or sym.lower() in text_low:
                        fused[i] *= 1.25

        # 6) order, dedupe overlapping chunks, trim to token budget
        order = sorted(range(len(filtered)), key=lambda i: fused[i], reverse=True)
        results: list[RetrievedChunk] = []
        used_tokens = 0
        seen_files: dict[str, int] = {}
        for i in order:
            c = filtered[i]
            if len(results) >= q.max_chunks:
                break
            # max 4 chunks per file to keep context diverse
            if seen_files.get(c["path"], 0) >= 4:
                continue
            if used_tokens + c["token_estimate"] > q.token_budget and results:
                continue
            results.append(RetrievedChunk(
                path=c["path"],
                start_line=c["start_line"],
                end_line=c["end_line"],
                text=c["text"],
                score=round(fused[i], 6),
                symbols=c["symbols"],
                token_estimate=c["token_estimate"],
            ))
            seen_files[c["path"]] = seen_files.get(c["path"], 0) + 1
            used_tokens += c["token_estimate"]
        return results


def _path_lang(path: str) -> str:
    from services.indexing.languages import detect_language

    return detect_language(path)


def _is_test(path: str) -> bool:
    low = path.lower()
    return "/test" in low or low.startswith("test") or "test_" in low or "_test." in low


def _bm25_rank(query_tokens: list[str], chunks: list[dict]) -> list[float]:
    if not chunks or not query_tokens:
        return [0.0] * len(chunks)
    n = len(chunks)
    avgdl = sum(len(c["tokens"]) for c in chunks) / n or 1.0
    df: dict[str, int] = {}
    for c in chunks:
        for t in set(c["tokens"]):
            df[t] = df.get(t, 0) + 1
    k1, b = 1.5, 0.75
    scores = []
    for c in chunks:
        tf: dict[str, int] = {}
        for t in c["tokens"]:
            tf[t] = tf.get(t, 0) + 1
        dl = len(c["tokens"]) or 1
        s = 0.0
        for t in query_tokens:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            s += idf * (tf[t] * (k1 + 1)) / (tf[t] + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores


def _rrf(rankings: list[list[float]], k: int = _RRF_K) -> list[float]:
    """Fuse multiple score lists into RRF scores."""
    m = len(rankings[0]) if rankings else 0
    out = [0.0] * m
    for scores in rankings:
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        for rank, i in enumerate(order):
            if scores[i] <= 0 and scores is rankings[0]:
                continue  # skip non-matching BM25 docs
            out[i] += 1.0 / (k + rank + 1)
    return out


# module-level singleton (per-process cache)
search_engine = SearchEngine()