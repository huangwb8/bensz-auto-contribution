"""Sensitive data redaction for BAC event payloads."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


MAX_STRING_LENGTH = 4000

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"\b(ghp_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\b(gho_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)([?&](?:access_token|api[_-]?key|token|secret|password)=)[^&\s]+"),
    re.compile(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|authorization|cookie|密码|令牌|密钥)\b"
        r"(\s*[:=]\s*|\s+)(['\"]?)[^\s'\";,]+"
    ),
]


def redact_data(value: Any) -> tuple[Any, list[dict[str, str]]]:
    redactions: list[dict[str, str]] = []
    redacted = _redact_value(deepcopy(value), "$", redactions)
    return redacted, redactions


def _redact_value(value: Any, path: str, redactions: list[dict[str, str]]) -> Any:
    if isinstance(value, dict):
        return {key: _redact_value(item, f"{path}.{key}", redactions) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, f"{path}[{index}]", redactions) for index, item in enumerate(value)]
    if isinstance(value, str):
        return _redact_text(value, path, redactions)
    return value


def _redact_text(value: str, path: str, redactions: list[dict[str, str]]) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        updated = pattern.sub("[REDACTED]", redacted)
        if updated != redacted:
            redactions.append({"path": path, "reason": "sensitive_pattern"})
            redacted = updated

    if len(redacted) > MAX_STRING_LENGTH:
        redactions.append({"path": path, "reason": "truncated_long_value"})
        redacted = redacted[:MAX_STRING_LENGTH] + "...[TRUNCATED]"

    return redacted
