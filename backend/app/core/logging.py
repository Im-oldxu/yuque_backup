from __future__ import annotations

import logging
import re
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"(?i)(x-auth-token|authorization|cookie|set-cookie)(\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(token|password)(\s*[:=]\s*)([^\s,;]+)"),
)


def redact(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(r"\1\2[REDACTED]", result)
    return result


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            rendered = f"{record.msg!s} {record.args!r}"
        record.msg = redact(rendered)
        record.args = ()
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def safe_log_fields(**values: Any) -> dict[str, str]:
    return {key: redact(str(value)) for key, value in values.items()}
