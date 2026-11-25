"""Typed message, capability, request, and response contracts for models."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SystemMessage(BaseModel):
    """System instruction message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system"] = "system"
    content: str


class UserMessage(BaseModel):
    """User input message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["user"] = "user"
    content: str


class ToolCall(BaseModel):
    """A model-requested tool invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AssistantMessage(BaseModel):
    """Assistant response message, optionally containing tool calls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


class ToolResultMessage(BaseModel):
    """Normalized result of a tool execution returned to the model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["tool"] = "tool"
    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool = False


Message = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolResultMessage,
    Field(discriminator="role"),
]


class TokenUsage(BaseModel):
    """Token accounting for a model call or aggregated run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


FinishReason = Literal["stop", "tool_calls", "length", "error", "cancelled"]


class ModelCapabilities(BaseModel):
    """Explicit capability flags for a model provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    streaming: bool = False
    tool_calling: bool = False
    structured_output: bool = False
    vision: bool = False
    audio: bool = False
    reasoning: bool = False


class ToolSchema(BaseModel):
    """Provider-neutral JSON schema description of a tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ModelRequest(BaseModel):
    """Typed request passed to a `Model`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...] = ()
    stream: bool = False
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    """Normalized response returned by a `Model`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: FinishReason = "stop"
    usage: TokenUsage = Field(default_factory=TokenUsage)
    request_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}")
    model_name: str = ""
    provider: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelStreamDelta(BaseModel):
    """Incremental chunk yielded during streaming model generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text_delta: str = ""
    request_id: str = ""
    is_final: bool = False
    response: ModelResponse | None = None
