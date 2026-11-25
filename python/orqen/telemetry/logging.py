"""Structured logging foundation with correlation context and secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TextIO

from orqen.config.settings import LoggingConfig

if TYPE_CHECKING:
    from collections.abc import Iterator

_REDACTED = "[REDACTED]"
_DEFAULT_REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "password",
        "passwd",
        "private_key",
        "secret",
        "token",
    }
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"\bsk-[A-Za-z0-9\-_]{8,}\b"),
    re.compile(r"(?i)((?:api_key|apikey|token|secret|password)\s*[=:]\s*['\"]?)([^\s'\",;]+)"),
)

_correlation_id_var: ContextVar[str | None] = ContextVar("orqen_correlation_id", default=None)

_STANDARD_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
        "correlation_id",
    }
)


def get_correlation_id() -> str | None:
    """Return the active correlation ID from context, if any."""
    return _correlation_id_var.get()


@contextmanager
def bind_correlation_id(correlation_id: str | None) -> Iterator[str | None]:
    """Temporarily bind a correlation ID to the current execution context."""
    token = _correlation_id_var.set(correlation_id)
    try:
        yield correlation_id
    finally:
        _correlation_id_var.reset(token)


def _redact_string(value: str) -> str:
    """Mask common credential patterns in a string."""
    redacted = value
    redacted = _SECRET_PATTERNS[0].sub(rf"\1{_REDACTED}", redacted)
    redacted = _SECRET_PATTERNS[1].sub(_REDACTED, redacted)
    redacted = _SECRET_PATTERNS[2].sub(rf"\1{_REDACTED}", redacted)
    return redacted


def _redact_value(value: Any, sensitive_keys: frozenset[str]) -> Any:
    """Recursively redact sensitive keys and string patterns."""
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return {
            k: (_REDACTED if str(k).lower() in sensitive_keys else _redact_value(v, sensitive_keys))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, sensitive_keys) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, sensitive_keys) for item in value)
    return value


class CorrelationFilter(logging.Filter):
    """Inject the current context's `correlation_id` onto each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = _correlation_id_var.get()
        return True


class RedactingFilter(logging.Filter):
    """Redact secret-like patterns and sensitive extra keys on log records."""

    def __init__(self, extra_redacted_keys: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._sensitive_keys = _DEFAULT_REDACTED_KEYS | frozenset(
            k.lower() for k in extra_redacted_keys
        )

    def filter(self, record: logging.LogRecord) -> bool:
        # Format message first so args are interpolated before redaction.
        formatted_msg = record.getMessage()
        record.msg = _redact_string(formatted_msg)
        record.args = None

        for attr, value in list(record.__dict__.items()):
            if attr in _STANDARD_RECORD_ATTRS:
                continue
            if attr.lower() in self._sensitive_keys:
                record.__dict__[attr] = _REDACTED
            else:
                record.__dict__[attr] = _redact_value(value, self._sensitive_keys)
        return True


def _extract_extra(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value for key, value in record.__dict__.items() if key not in _STANDARD_RECORD_ATTRS
    }


class TextFormatter(logging.Formatter):
    """Human-readable structured text formatter."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        corr_id = getattr(record, "correlation_id", None)
        corr_part = f" [cid={corr_id}]" if corr_id else ""
        base = f"{timestamp} {record.levelname:<8} {record.name}{corr_part}: {record.getMessage()}"
        extra = _extract_extra(record)
        if extra:
            extra_str = " ".join(f"{k}={v!r}" for k, v in sorted(extra.items()))
            base = f"{base} | {extra_str}"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


class JsonFormatter(logging.Formatter):
    """Structured JSON lines log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        corr_id = getattr(record, "correlation_id", None)
        if corr_id is not None:
            payload["correlation_id"] = corr_id
        extra = _extract_extra(record)
        if extra:
            payload["extra"] = extra
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    config: LoggingConfig | None = None,
    *,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure the root `orqen` logger and return it."""
    cfg = config or LoggingConfig()
    logger = logging.getLogger("orqen")
    logger.setLevel(getattr(logging, cfg.level))
    logger.propagate = False

    # Clear existing handlers to make reconfiguration deterministic.
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        existing.close()

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setLevel(getattr(logging, cfg.level))
    handler.addFilter(CorrelationFilter())
    if cfg.redact_secrets:
        handler.addFilter(RedactingFilter(cfg.extra_redacted_keys))

    formatter: logging.Formatter = JsonFormatter() if cfg.format == "json" else TextFormatter()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def get_logger(name: str = "orqen") -> logging.Logger:
    """Return a namespaced logger under `orqen`."""
    if name == "orqen" or name.startswith("orqen."):
        return logging.getLogger(name)
    return logging.getLogger(f"orqen.{name}")
