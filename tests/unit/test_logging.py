"""Unit tests for the ORQEN structured logging foundation."""

import io
import json

from orqen.config import LoggingConfig
from orqen.telemetry import (
    bind_correlation_id,
    configure_logging,
    get_correlation_id,
    get_logger,
)


def test_json_logging_and_correlation_id() -> None:
    stream = io.StringIO()
    configure_logging(LoggingConfig(level="DEBUG", format="json"), stream=stream)
    logger = get_logger("runtime")

    assert get_correlation_id() is None
    with bind_correlation_id("req-123"):
        assert get_correlation_id() == "req-123"
        logger.info("Task started", extra={"task_id": "t-1"})
    assert get_correlation_id() is None

    line = stream.getvalue().strip()
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "orqen.runtime"
    assert payload["message"] == "Task started"
    assert payload["correlation_id"] == "req-123"
    assert payload["extra"] == {"task_id": "t-1"}


def test_secret_redaction_in_message_and_extra() -> None:
    stream = io.StringIO()
    configure_logging(
        LoggingConfig(
            level="INFO",
            format="json",
            redact_secrets=True,
            extra_redacted_keys=("custom_secret",),
        ),
        stream=stream,
    )
    logger = get_logger("security")
    logger.warning(
        "Auth failed with Bearer abcdef123456 and key sk-1234567890abcdef",
        extra={
            "api_key": "super-secret-value",
            "custom_secret": "also-secret",
            "safe_field": "visible",
        },
    )

    payload = json.loads(stream.getvalue().strip())
    assert "abcdef123456" not in payload["message"]
    assert "sk-1234567890abcdef" not in payload["message"]
    assert "[REDACTED]" in payload["message"]
    assert payload["extra"]["api_key"] == "[REDACTED]"
    assert payload["extra"]["custom_secret"] == "[REDACTED]"
    assert payload["extra"]["safe_field"] == "visible"


def test_text_logging_format() -> None:
    stream = io.StringIO()
    configure_logging(LoggingConfig(level="INFO", format="text"), stream=stream)
    logger = get_logger()

    with bind_correlation_id("cid-99"):
        logger.info("Engine ready")

    output = stream.getvalue().strip()
    assert "INFO" in output
    assert "orqen [cid=cid-99]: Engine ready" in output
