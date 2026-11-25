"""Tool contracts, schema validation, and execution normalization."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from orqen.exceptions import OrqenTimeoutError, ToolError, ToolValidationError
from orqen.models.types import ToolSchema

if TYPE_CHECKING:
    from collections.abc import Callable

ToolExecutionMode = Literal["async", "sync"]


class ToolResult(BaseModel):
    """Normalized outcome of a tool invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: str
    output: str
    is_error: bool = False
    duration_seconds: float = Field(default=0.0, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


def normalize_tool_output(value: Any) -> str:
    """Normalize arbitrary Python return values from a tool into a string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, default=str)
        except TypeError as exc:
            raise ToolValidationError(
                f"Tool returned non-serializable output of type {type(value).__name__}.",
                cause=exc,
            ) from exc
    return str(value)


class Tool:
    """Executable tool definition with schema validation and timeout enforcement.

    Note on synchronous tools:
        Synchronous functions are executed via `asyncio.to_thread` so they do not
        block the event loop. On timeout or cancellation, `asyncio.wait_for` stops
        awaiting the worker thread and raises `OrqenTimeoutError` or `CancelledError`,
        but Python cannot forcibly terminate an arbitrary OS/blocking thread.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        fn: Callable[..., Any],
        input_model: type[BaseModel],
        mode: ToolExecutionMode,
        timeout_seconds: float | None = None,
        dangerous: bool = False,
        permissions: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not name or not name.strip():
            raise ToolValidationError("Tool name must be a non-empty string.")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ToolValidationError("Tool timeout_seconds must be positive.")

        self.name = name
        self.description = description
        self.fn = fn
        self.input_model = input_model
        self.mode: ToolExecutionMode = mode
        self.timeout_seconds = timeout_seconds
        self.dangerous = dangerous
        self.permissions = tuple(permissions)
        self.metadata: dict[str, Any] = dict(metadata) if metadata is not None else {}

        # Preserve wrapped function metadata for introspection.
        self.__name__ = getattr(fn, "__name__", name)
        self.__doc__ = getattr(fn, "__doc__", description)
        self.__wrapped__ = fn

    def to_schema(self) -> ToolSchema:
        """Return the provider-neutral JSON schema for this tool."""
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.input_model.model_json_schema(),
        )

    def validate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate raw model-supplied arguments against `input_model`."""
        if not isinstance(arguments, dict):
            raise ToolValidationError(
                f"Tool '{self.name}' arguments must be a dictionary.",
                details={"tool": self.name, "received_type": type(arguments).__name__},
            )
        try:
            validated = self.input_model.model_validate(arguments)
        except PydanticValidationError as exc:
            raise ToolValidationError(
                f"Invalid arguments for tool '{self.name}': {exc}",
                details={"tool": self.name, "validation_errors": exc.errors()},
                cause=exc,
            ) from exc
        return validated.model_dump()

    async def invoke(
        self,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> str:
        """Validate arguments and execute the tool with timeout enforcement."""
        validated_kwargs = self.validate_arguments(arguments)
        effective_timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds

        async def _call() -> Any:
            if self.mode == "async":
                return await self.fn(**validated_kwargs)
            return await asyncio.to_thread(self.fn, **validated_kwargs)

        try:
            if effective_timeout is not None:
                raw_output = await asyncio.wait_for(_call(), timeout=effective_timeout)
            else:
                raw_output = await _call()
        except TimeoutError as exc:
            raise OrqenTimeoutError(
                f"Tool '{self.name}' timed out after {effective_timeout}s.",
                details={"tool": self.name, "timeout_seconds": effective_timeout},
                cause=exc,
            ) from exc
        except asyncio.CancelledError:
            raise
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"Tool '{self.name}' execution failed: {exc}",
                details={"tool": self.name, "error_type": type(exc).__name__},
                cause=exc,
            ) from exc

        return normalize_tool_output(raw_output)
