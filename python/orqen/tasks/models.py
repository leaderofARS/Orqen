"""Persistent Task domain model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orqen.tasks.lifecycle import TaskState


class Task(BaseModel):
    """Discrete execution request associated with a `Session`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:16]}")
    session_id: str
    run_id: str | None = None
    agent_id: str | None = None
    agent_name: str = ""
    status: TaskState = TaskState.PENDING
    input: str
    output: str | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_seconds: float | None = Field(default=None, ge=0.0)
    revision: int = Field(default=1, ge=1)
