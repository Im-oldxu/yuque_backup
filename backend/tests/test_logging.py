from __future__ import annotations

import logging

from app.core.logging import SecretRedactionFilter


def test_redaction_filter_preserves_numeric_formats_and_redacts_rendered_secrets() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP status=%d token=%s cookie=%s',
        args=(200, "full-secret-token", "session-secret"),
        exc_info=None,
    )

    assert SecretRedactionFilter().filter(record) is True
    assert record.getMessage() == "HTTP status=200 token=[REDACTED] cookie=[REDACTED]"
