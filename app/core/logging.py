"""
Structured logging for the LaTeX compiler service.

Provides a JSON formatter for production and a human-readable formatter for
development. Exposes `setup_logging()` to configure the root logger and
`log_compile_event()` to emit structured compile request logs.
"""

import json
import logging
import os
import sys
from typing import Optional


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any extra fields attached to the record
        if hasattr(record, "extra_fields"):
            payload.update(record.extra_fields)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    """
    Configure the root logger.

    Uses JSON output when the ``LOG_FORMAT`` env var is ``"json"``
    (recommended for production).  Falls back to human-readable output.
    """
    log_format = os.environ.get("LOG_FORMAT", "text")
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    # Remove existing handlers to avoid duplicates on reload
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    root.addHandler(handler)


# ---------------------------------------------------------------------------
# Compile event logger
# ---------------------------------------------------------------------------

_compile_logger = logging.getLogger("compile")


def log_compile_event(
    *,
    request_id: str,
    endpoint: str,
    main_file: str,
    engine: str,
    passes: int,
    file_count: int = 1,
    total_bytes: int = 0,
    compile_time_ms: int = 0,
    outcome: str,  # "success" | "compile_error" | "timeout" | "invalid_input" | "internal"
    error_message: Optional[str] = None,
) -> None:
    """
    Emit a structured log line for a compile request.

    Call this once per request, after the compilation has finished (or failed).
    """
    fields = {
        "request_id": request_id,
        "endpoint": endpoint,
        "main_file": main_file,
        "engine": engine,
        "passes": passes,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "compile_time_ms": compile_time_ms,
        "outcome": outcome,
    }
    if error_message:
        fields["error_message"] = error_message

    # Attach fields so the JSONFormatter can serialize them
    record_msg = (
        f"compile {outcome}  request_id={request_id}  "
        f"main_file={main_file}  engine={engine}  passes={passes}  "
        f"files={file_count}  bytes={total_bytes}  time={compile_time_ms}ms"
    )

    extra_record = _compile_logger.makeRecord(
        name=_compile_logger.name,
        level=logging.INFO,
        fn="",
        lno=0,
        msg=record_msg,
        args=(),
        exc_info=None,
    )
    extra_record.extra_fields = fields  # type: ignore[attr-defined]
    _compile_logger.handle(extra_record)
