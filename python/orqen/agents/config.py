"""Agent execution configuration."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AgentConfig(BaseModel):
    """Validated configuration governing a single agent's execution limits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_iterations: int = Field(
        default=10,
        ge=1,
        le=1_000,
        description="Maximum model-response iterations per run.",
    )
    max_tool_calls: int = Field(
        default=25,
        ge=0,
        le=1_000,
        description="Maximum total tool calls permitted per run.",
    )
    model_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Timeout per model generation call in seconds.",
    )
    tool_timeout_seconds: float = Field(
        default=15.0,
        gt=0.0,
        description="Default timeout per tool invocation in seconds.",
    )
    run_timeout_seconds: float | None = Field(
        default=120.0,
        gt=0.0,
        description="Overall deadline for a single agent run in seconds.",
    )
    stream_by_default: bool = Field(
        default=False,
        description="Whether to prefer streaming model responses when supported.",
    )
    fail_on_tool_error: bool = Field(
        default=False,
        description=(
            "If True, abort the run on tool execution/validation error; "
            "if False, return the error as a ToolResultMessage to the model."
        ),
    )
