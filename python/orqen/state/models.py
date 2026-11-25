"""Persistent structured state entry model and JSON validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orqen.exceptions import StateError

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list[Any] | dict[str, Any]


def validate_json_value(value: Any) -> str:
    """Validate that `value` is strictly JSON-serializable and return its JSON text.

    Rejects arbitrary Python objects and non-standard floats (`NaN`, `Infinity`).
    """
    try:
        return json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise StateError(
            f"State value is not strictly JSON-serializable: {exc}",
            code="INVALID_STATE_VALUE",
            cause=exc,
        ) from exc


class StateEntry(BaseModel):
    """Versioned key/value entry scoped by namespace, session, and optional task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    namespace: str = Field(default="default", min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str | None = None
    key: str = Field(min_length=1)
    value: Any
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
