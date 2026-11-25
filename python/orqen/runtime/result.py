"""Structured execution result types."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from orqen.models.types import TokenUsage

RunStatus = Literal["completed", "failed", "cancelled"]


class RunResult(BaseModel):
    """Structured outcome of a single agent execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    agent_id: str
    agent_name: str
    session_id: str | None = None
    task_id: str | None = None
    output: str | None = None
    status: RunStatus
    started_at: datetime
    completed_at: datetime
    duration_seconds: float = Field(ge=0.0)
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
