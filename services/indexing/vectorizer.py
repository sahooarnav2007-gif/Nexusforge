"""Local deterministic vectorizer (offline, no model download).

Produces L2-normalized 512-dim vectors from character n-gram token hashing.
This is a *real* (if simple) embedding: similar text produces similar vectors,
which makes cosine retrieval meaningful. It is swappable for a real embedding
model via the same interface (see `embed_texts`).
"""
from __future__ import annotations

import math
import re

DIM = 512

_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{2,}|\d+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def vectorize(text: str) -> list[float]:
    v = [0.0] * DIM
    toks = _tokens(text)
    if not toks:
        return v
    # unigrams + bigrams hashed into buckets
    grams: list[str] = list(toks)
    grams += [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    for g in grams:
        idx = (hash(g) & 0xFFFFFFFF) % DIM
        v[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in v))
    if norm > 0:
        v = [x / norm for x in v]
    return v


def embed_texts(texts: list[str]) -> list[list[float]]:
    return [vectorize(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))