"""Secret redaction for logs and error messages."""
from __future__ import annotations

import logging
import re

_PATTERNS = [
    (re.compile(r"(?i)\b(authorization|cookie|set-cookie)\s*[:=]\s*[^\n]+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b(auth_token|ct0|api[_-]?key|access[_-]?token|secret|password|passwd|token)\b(\s*[=:]\s*|\"\s*:\s*\")([^\s\"&;,]{4,})"), r"\1\2[REDACTED]"),
    (re.compile(r"\b(sk|pk|ghp|gho|xox[abp]|AKIA)[-_A-Za-z0-9]{12,}\b"), "[REDACTED]"),
    # license keys:  PREFIX-<b64url>.<b64url sig>
    (re.compile(r"\b([A-Z0-9]{2,8})-[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"), r"\1-[REDACTED-LICENSE]"),
]


def redact(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


def install(logger: logging.Logger | None = None) -> None:
    (logger or logging.getLogger()).addFilter(RedactingFilter())
    for h in (logger or logging.getLogger()).handlers:
        h.addFilter(RedactingFilter())
