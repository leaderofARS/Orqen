"""Typed configuration system for ORQEN."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from orqen.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Mapping

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["text", "json"]
Environment = Literal["development", "staging", "production"]
StorageBackend = Literal["sqlite"]
SQLiteJournalMode = Literal["WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF"]
SQLiteSynchronous = Literal["OFF", "NORMAL", "FULL", "EXTRA"]

_ENV_PREFIX = "ORQEN_"


class RuntimeConfig(BaseModel):
    """Runtime execution and bounded concurrency settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_concurrent_tasks: int = Field(
        default=64,
        ge=1,
        le=10_000,
        description="Upper bound on concurrent tasks managed by the runtime.",
    )
    default_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Default execution timeout in seconds.",
    )
    environment: Environment = Field(
        default="development",
        description="Deployment environment identifier.",
    )


class LoggingConfig(BaseModel):
    """Structured logging and redaction settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: LogLevel = Field(default="INFO", description="Minimum log severity.")
    format: LogFormat = Field(default="text", description="Log output format.")
    redact_secrets: bool = Field(
        default=True,
        description="Whether to redact credential-like values from logs.",
    )
    extra_redacted_keys: tuple[str, ...] = Field(
        default=(),
        description="Additional field/key names to redact from structured logs.",
    )


class StorageConfig(BaseModel):
    """Persistent storage backend settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether persistent storage is enabled by default.",
    )
    backend: StorageBackend = Field(
        default="sqlite",
        description="Persistence backend identifier.",
    )
    database_path: Path = Field(
        default=Path("./orqen.db"),
        description="Path to the SQLite database file.",
    )
    busy_timeout_ms: int = Field(
        default=5000,
        ge=0,
        le=60_000,
        description="SQLite busy timeout in milliseconds.",
    )
    connection_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="SQLite connection acquisition timeout in seconds.",
    )
    journal_mode: SQLiteJournalMode = Field(
        default="WAL",
        description="SQLite journal mode.",
    )
    synchronous: SQLiteSynchronous = Field(
        default="NORMAL",
        description="SQLite synchronous pragma mode.",
    )
    auto_migrate: bool = Field(
        default=True,
        description="Whether to apply pending schema migrations on initialization.",
    )


class ArtifactsConfig(BaseModel):
    """Filesystem-backed artifact storage settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether artifact storage is enabled by default.",
    )
    directory: Path = Field(
        default=Path("./orqen_artifacts"),
        description="Root directory for persisted artifact payloads.",
    )
    max_artifact_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1,
        description="Maximum size in bytes for a single artifact.",
    )


class OrqenConfig(BaseModel):
    """Root configuration for ORQEN."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    artifacts: ArtifactsConfig = Field(default_factory=ArtifactsConfig)
    data_dir: Path = Field(
        default_factory=lambda: Path.home() / ".orqen",
        description="Base directory for local state and artifacts.",
    )


def _deep_merge(base: dict[str, Any], updates: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge `updates` into a copy of `base`."""
    merged: dict[str, Any] = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parse_env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    """Extract `ORQEN_*` environment variables into a nested config dict.

    Supports double-underscore (`__`) nesting, e.g.:
    `ORQEN_RUNTIME__MAX_CONCURRENT_TASKS=128` -> `{"runtime": {"max_concurrent_tasks": "128"}}`.
    `ORQEN_CONFIG_FILE` is handled separately as the config file path.
    """
    result: dict[str, Any] = {}
    for raw_key, raw_value in env.items():
        if not raw_key.startswith(_ENV_PREFIX) or raw_key == "ORQEN_CONFIG_FILE":
            continue
        stripped = raw_key[len(_ENV_PREFIX) :].lower()
        if not stripped:
            continue
        parts = stripped.split("__")
        cursor = result
        for part in parts[:-1]:
            existing = cursor.get(part)
            if not isinstance(existing, dict):
                existing = {}
                cursor[part] = existing
            cursor = existing
        leaf = parts[-1]
        if leaf == "extra_redacted_keys":
            cursor[leaf] = tuple(item.strip() for item in raw_value.split(",") if item.strip())
        else:
            cursor[leaf] = raw_value
    return result


def _read_toml_file(path: Path) -> dict[str, Any]:
    """Read and parse a TOML configuration file."""
    if not path.exists():
        raise ConfigurationError(
            f"Configuration file not found: {path}",
            details={"path": str(path)},
        )
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(
            f"Failed to parse configuration file '{path}': {exc}",
            details={"path": str(path)},
            cause=exc,
        ) from exc

    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Configuration file '{path}' must decode to a TOML table.",
            details={"path": str(path)},
        )
    return data


def load_config(
    config_path: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> OrqenConfig:
    """Load, merge, and validate ORQEN configuration.

    Precedence (lowest to highest):
    1. Model defaults
    2. TOML file (`config_path` or `ORQEN_CONFIG_FILE`)
    3. Environment variables (`ORQEN_*`)
    4. Explicit `overrides`
    """
    env_map = os.environ if env is None else env
    resolved_path: Path | None = None
    if config_path is not None:
        resolved_path = Path(config_path)
    elif env_file := env_map.get("ORQEN_CONFIG_FILE"):
        resolved_path = Path(env_file)

    merged: dict[str, Any] = {}
    if resolved_path is not None:
        merged = _deep_merge(merged, _read_toml_file(resolved_path))

    env_overrides = _parse_env_overrides(env_map)
    if env_overrides:
        merged = _deep_merge(merged, env_overrides)

    if overrides:
        merged = _deep_merge(merged, overrides)

    try:
        return OrqenConfig.model_validate(merged)
    except PydanticValidationError as exc:
        raise ConfigurationError(
            "Invalid ORQEN configuration.",
            details={"validation_errors": exc.errors()},
            cause=exc,
        ) from exc
