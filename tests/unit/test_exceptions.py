"""Unit tests for the ORQEN structured exception hierarchy."""

from orqen.exceptions import (
    CancellationError,
    ConfigurationError,
    ExecutionError,
    LifecycleError,
    OrqenError,
    OrqenTimeoutError,
    SecurityError,
    StorageError,
    ValidationError,
)


def test_exception_hierarchy() -> None:
    assert issubclass(ConfigurationError, OrqenError)
    assert issubclass(ValidationError, OrqenError)
    assert issubclass(LifecycleError, OrqenError)
    assert issubclass(ExecutionError, OrqenError)
    assert issubclass(OrqenTimeoutError, ExecutionError)
    assert issubclass(CancellationError, ExecutionError)
    assert issubclass(SecurityError, OrqenError)
    assert issubclass(StorageError, OrqenError)


def test_exception_serialization_and_cause() -> None:
    root = ValueError("bad port")
    err = ConfigurationError(
        "Failed to load settings",
        code="CFG_BAD_PORT",
        details={"field": "port"},
        cause=root,
    )

    assert str(err) == "[CFG_BAD_PORT] Failed to load settings"
    assert err.__cause__ is root

    payload = err.to_dict()
    assert payload == {
        "error_type": "ConfigurationError",
        "code": "CFG_BAD_PORT",
        "message": "Failed to load settings",
        "details": {"field": "port"},
        "cause": "ValueError: bad port",
    }
