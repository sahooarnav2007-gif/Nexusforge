"""Symbol-aware chunking.

Python files are split at top-level symbol boundaries (functions/classes) so
each chunk is a coherent unit; other languages fall back to line windows with
overlap.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.indexing.ast_extract import Symbol


@dataclass
class Chunk:
    seq: int
    start_line: int  # 1-based inclusive
    end_line: int    # 1-based inclusive
    text: str
    symbols: list[str]


def _estimate_tokens(text: str) -> int:
    # ~4 chars/token heuristic, good enough for budgeting
    return max(1, len(text) // 4)


def chunk_python(lines: list[str], symbols: list[Symbol],
                 max_lines: int = 120) -> list[Chunk]:
    """Group consecutive top-level symbols into chunks of bounded size."""
    top_level = [s for s in symbols if "." not in s.name]
    if not top_level:
        return _line_chunks(lines)
    chunks: list[Chunk] = []
    seq = 0
    cur_start = 1
    cur_end = 1
    cur_syms: list[str] = []
    for i, sym in enumerate(top_level):
        end = sym.end_line if i + 1 < len(top_level) else min(sym.end_line, len(lines))
        # extend to include the gap before the next symbol
        next_start = top_level[i + 1].line if i + 1 < len(top_level) else len(lines) + 1
        block_end = next_start - 1
        if (block_end - cur_start + 1) > max_lines and cur_syms:
            chunks.append(_make_chunk(seq, lines, cur_start, cur_end, cur_syms))
            seq += 1
            cur_start, cur_end, cur_syms = sym.line, block_end, [sym.name]
        else:
            cur_end = max(cur_end, block_end)
            if sym.name not in cur_syms:
                cur_syms.append(sym.name)
    if cur_syms:
        chunks.append(_make_chunk(seq, lines, cur_start, cur_end, cur_syms))
    return chunks


def _line_chunks(lines: list[str], window: int = 60, overlap: int = 10) -> list[Chunk]:
    if not lines:
        return []
    chunks: list[Chunk] = []
    seq = 0
    start = 0
    while start < len(lines):
        end = min(start + window, len(lines))
        text = "\n".join(lines[start:end])
        chunks.append(Chunk(seq, start + 1, end, text, []))
        seq += 1
        if end == len(lines):
            break
        start = end - overlap
    return chunks


def _make_chunk(seq: int, lines: list[str], start: int, end: int,
                syms: list[str]) -> Chunk:
    start = max(1, start)
    end = min(len(lines), end)
    text = "\n".join(lines[start - 1:end])
    return Chunk(seq, start, end, text, syms)


def chunk_file(language: str, text: str, symbols: list[Symbol],
               max_lines: int = 120) -> list[Chunk]:
    lines = text.splitlines()
    if language == "python":
        return chunk_python(lines, symbols, max_lines)
    return _line_chunks(lines, window=max(20, max_lines // 2))