"""Logging setup: every log line passes through PII redaction before it is written."""

import logging

from glaka.guardrails import redact


class RedactingFilter(logging.Filter):
    """Rewrites each record's final message with personal data replaced."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message)
        if redacted != message:  # only rewrite when needed: some formatters read record.args
            record.msg = redacted
            record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging and attach redaction to every handler (ours and uvicorn's).

    uvicorn.access is left alone: it logs method, path and status only, and its
    formatter needs the original record.args.
    """
    logging.basicConfig(level=level)
    redacting = RedactingFilter()
    for name in ("", "uvicorn", "uvicorn.error"):
        for handler in logging.getLogger(name).handlers:
            handler.addFilter(redacting)
