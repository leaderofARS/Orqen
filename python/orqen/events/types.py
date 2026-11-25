"""Typed runtime events for observability and streaming."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from orqen.models.types import FinishReason, TokenUsage


class RuntimeEvent(BaseModel):
    """Base schema for all ORQEN runtime events."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run_id: str
    agent_id: str
    agent_name: str
    correlation_id: str | None = None


class RunStarted(RuntimeEvent):
    """Emitted when an agent execution begins."""

    event_type: Literal["run.started"] = "run.started"


class ModelRequestStarted(RuntimeEvent):
    """Emitted before invoking the model."""

    event_type: Literal["model.request.started"] = "model.request.started"
    iteration: int
    model_name: str
    provider: str


class ModelStreamChunk(RuntimeEvent):
    """Emitted for each incremental text chunk during model streaming."""

    event_type: Literal["model.stream.chunk"] = "model.stream.chunk"
    iteration: int
    request_id: str
    text_delta: str


class ModelRequestCompleted(RuntimeEvent):
    """Emitted when a model response completes."""

    event_type: Literal["model.request.completed"] = "model.request.completed"
    iteration: int
    request_id: str
    finish_reason: FinishReason
    usage: TokenUsage
    tool_call_count: int = 0


class ToolCallRequested(RuntimeEvent):
    """Emitted when the model requests a tool invocation."""

    event_type: Literal["tool.call.requested"] = "tool.call.requested"
    tool_call_id: str
    tool_name: str


class ToolExecutionStarted(RuntimeEvent):
    """Emitted immediately before executing a permitted tool."""

    event_type: Literal["tool.execution.started"] = "tool.execution.started"
    tool_call_id: str
    tool_name: str


class ToolExecutionCompleted(RuntimeEvent):
    """Emitted when a tool invocation succeeds."""

    event_type: Literal["tool.execution.completed"] = "tool.execution.completed"
    tool_call_id: str
    tool_name: str
    duration_seconds: float


class ToolExecutionFailed(RuntimeEvent):
    """Emitted when a tool invocation fails validation, permission, or execution."""

    event_type: Literal["tool.execution.failed"] = "tool.execution.failed"
    tool_call_id: str
    tool_name: str
    error_type: str
    error_message: str


class RunCompleted(RuntimeEvent):
    """Emitted when an agent run finishes successfully."""

    event_type: Literal["run.completed"] = "run.completed"
    output: str
    model_calls: int
    tool_calls: int
    usage: TokenUsage


class RunFailed(RuntimeEvent):
    """Emitted when an agent run terminates due to an error or timeout."""

    event_type: Literal["run.failed"] = "run.failed"
    error_type: str
    error_code: str
    error_message: str


class RunCancelled(RuntimeEvent):
    """Emitted when an agent run is cancelled."""

    event_type: Literal["run.cancelled"] = "run.cancelled"
    reason: str = "Cancelled"
