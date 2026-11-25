"""Structured exception hierarchy for ORQEN."""

from __future__ import annotations

from typing import Any


class OrqenError(Exception):
    """Base exception for all ORQEN infrastructure errors."""

    default_code: str = "ORQEN_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details: dict[str, Any] = dict(details) if details is not None else {}
        self.cause = cause
        if cause is not None:
            self.__cause__ = cause

    def to_dict(self) -> dict[str, Any]:
        """Return a structured, JSON-serializable representation of the error."""
        payload: dict[str, Any] = {
            "error_type": type(self).__name__,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
        if self.cause is not None:
            payload["cause"] = f"{type(self.cause).__name__}: {self.cause}"
        return payload

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class ConfigurationError(OrqenError):
    """Raised when configuration loading or validation fails."""

    default_code = "CONFIGURATION_ERROR"


class ValidationError(OrqenError):
    """Raised when input, schema, or contract validation fails."""

    default_code = "VALIDATION_ERROR"


class LifecycleError(OrqenError):
    """Raised on invalid agent, task, session, or runtime state transitions."""

    default_code = "LIFECYCLE_ERROR"


class ExecutionError(OrqenError):
    """Raised when task, tool, or runtime execution fails."""

    default_code = "EXECUTION_ERROR"


class OrqenTimeoutError(ExecutionError):
    """Raised when an operation exceeds its configured deadline."""

    default_code = "TIMEOUT_ERROR"


class CancellationError(ExecutionError):
    """Raised when an operation is cancelled."""

    default_code = "CANCELLATION_ERROR"


class ExecutionLimitError(ExecutionError):
    """Raised when an execution exceeds configured iteration or tool-call limits."""

    default_code = "EXECUTION_LIMIT_ERROR"


class ModelError(OrqenError):
    """Raised when model validation, capability check, or response parsing fails."""

    default_code = "MODEL_ERROR"


class ProviderError(ModelError):
    """Raised when an inference provider request fails."""

    default_code = "PROVIDER_ERROR"


class ToolError(OrqenError):
    """Raised when tool registration, lookup, or execution fails."""

    default_code = "TOOL_ERROR"


class ToolValidationError(ToolError):
    """Raised when tool arguments or outputs fail schema validation."""

    default_code = "TOOL_VALIDATION_ERROR"


class SecurityError(OrqenError):
    """Raised when a security policy or permission check fails."""

    default_code = "SECURITY_ERROR"


class StorageError(OrqenError):
    """Raised when a persistence or artifact storage operation fails."""

    default_code = "STORAGE_ERROR"


class DatabaseError(StorageError):
    """Raised when a database connection or SQL execution fails."""

    default_code = "DATABASE_ERROR"


class MigrationError(StorageError):
    """Raised when schema migration or version verification fails."""

    default_code = "MIGRATION_ERROR"


class RepositoryError(StorageError):
    """Raised when a repository operation fails."""

    default_code = "REPOSITORY_ERROR"


class ArtifactError(StorageError):
    """Raised when artifact storage, retrieval, or integrity checks fail."""

    default_code = "ARTIFACT_ERROR"


class SessionError(OrqenError):
    """Raised on invalid session operations or missing sessions."""

    default_code = "SESSION_ERROR"


class TaskError(OrqenError):
    """Raised on invalid task operations or missing tasks."""

    default_code = "TASK_ERROR"


class StateError(OrqenError):
    """Raised on invalid state serialization, scope, or key operations."""

    default_code = "STATE_ERROR"


class StateConflictError(StateError):
    """Raised when an optimistic concurrency revision check fails."""

    default_code = "STATE_CONFLICT_ERROR"
