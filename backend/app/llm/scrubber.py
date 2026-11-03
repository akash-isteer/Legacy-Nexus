"""Removes secrets and personal data from text before it is sent to an AI model.

Legacy code and scripts often embed passwords, keys and connection strings. They are replaced with a
labelled placeholder, so the model still understands the code but never sees the value.
"""
from __future__ import annotations

import re

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("AWS_KEY", re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("API_KEY", re.compile(r"\b(sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}|AIza[0-9A-Za-z_\-]{30,}|ghp_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    # user:password@host in URLs and connection strings
    ("CREDENTIALS", re.compile(r"(?<=://)[^/\s:@'\"]+:[^@\s'\"]+(?=@)")),
    # password=..., pwd: ..., secret="..." and similar assignments (keeps the key name)
    ("SECRET", re.compile(r"(?i)(\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|token|client[_-]?secret)\b"
                          r"\s*[:=]\s*)(['\"]?)([^'\"\s;,]{4,})\2")),
    ("ACCOUNT_KEY", re.compile(r"(?i)(AccountKey|SharedAccessKey)=([A-Za-z0-9+/=]{20,})")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("NATIONAL_ID", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),  # US SSN shape
]


def scrub(text: str) -> tuple[str, int]:
    """Return the text with secrets and personal data replaced, and how many replacements were made."""
    count = 0

    def sub(label: str, m: re.Match) -> str:
        nonlocal count
        count += 1
        if label == "SECRET":
            return f"{m.group(1)}{m.group(2)}[REDACTED_{label}]{m.group(2)}"
        if label == "ACCOUNT_KEY":
            return f"{m.group(1)}=[REDACTED_{label}]"
        return f"[REDACTED_{label}]"

    for label, pattern in PATTERNS:
        if label == "CARD":
            text = pattern.sub(lambda m: sub(label, m) if _luhn(m.group(0)) else m.group(0), text)
        else:
            text = pattern.sub(lambda m, lb=label: sub(lb, m), text)
    return text, count


def _luhn(raw: str) -> bool:
    digits = [int(c) for c in raw if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0
