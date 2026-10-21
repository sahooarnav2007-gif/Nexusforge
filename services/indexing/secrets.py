"""Secret detection on ingest.

Files that look like they contain secrets are flagged and EXCLUDED from the
chunk index so their content is never sent to an LLM. Detection is heuristic
(purpose-built for the ingest guard, not a full DLP engine).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws-secret-key", re.compile(r"(?i)aws.{0,20}secret.{0,20}[=:]\s*['\"][0-9a-zA-Z/+=]{40}['\"]")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("generic-api-key", re.compile(
        r"(?i)\b(api[_-]?key|secret|access[_-]?token|auth[_-]?token|password|passwd)\b"
        r"\s*[:=]\s*['\"][^'\"]{12,}['\"]")),
    ("connection-string", re.compile(
        r"(?i)\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^/\s:]+:[^@\s]+@")),
]

# filenames that are treated as sensitive regardless of content
SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
    "credentials.json", "service-account.json", "secrets.json",
    ".npmrc", ".pypirc", "config secrets.yaml",
}


@dataclass
class SecretHit:
    rule: str
    line: int


def scan_text(text: str) -> list[SecretHit]:
    hits: list[SecretHit] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for rule, pat in _PATTERNS:
            if pat.search(line):
                hits.append(SecretHit(rule, i))
                break
    return hits


def is_sensitive_filename(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    return name in SENSITIVE_NAMES or name.startswith(".env")


def scan_file(path: str, text: str) -> list[SecretHit]:
    if is_sensitive_filename(path):
        return [SecretHit("sensitive-filename", 0)]
    return scan_text(text)