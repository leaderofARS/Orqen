"""Deterministic mock model for offline, credential-free testing."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from orqen.exceptions import ModelError, ProviderError
from orqen.models.base import Model
from orqen.models.types import (
    FinishReason,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    ModelStreamDelta,
    TokenUsage,
    ToolCall,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence


class MockResponseSpec(BaseModel):
    """Scripted response specification for `MockModel`."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid")

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: FinishReason | None = None
    usage: TokenUsage = Field(
        default_factory=lambda: TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    )
    delay_seconds: float = Field(default=0.0, ge=0.0)
    chunks: tuple[str, ...] | None = None
    error: Exception | None = None
    malformed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


def _coerce_spec(item: MockResponseSpec | dict[str, Any] | str | Exception) -> MockResponseSpec:
    if isinstance(item, MockResponseSpec):
        return item
    if isinstance(item, str):
        return MockResponseSpec(text=item)
    if isinstance(item, Exception):
        return MockResponseSpec(error=item)
    if isinstance(item, dict):
        data = dict(item)
        raw_calls = data.get("tool_calls")
        if raw_calls is not None:
            coerced_calls: list[ToolCall] = []
            for call in raw_calls:
                if isinstance(call, ToolCall):
                    coerced_calls.append(call)
                elif isinstance(call, dict):
                    coerced_calls.append(ToolCall.model_validate(call))
                else:
                    raise ModelError("Invalid mock tool_call entry.")
            data["tool_calls"] = tuple(coerced_calls)
        return MockResponseSpec.model_validate(data)
    raise ModelError(f"Unsupported MockModel response spec type: {type(item).__name__}")


class MockModel(Model):
    """Deterministic, configurable mock model for unit and integration tests."""

    def __init__(
        self,
        responses: Sequence[MockResponseSpec | dict[str, Any] | str | Exception] = (),
        *,
        model_name: str = "mock-1",
        provider: str = "mock",
        capabilities: ModelCapabilities | None = None,
        cycle: bool = False,
    ) -> None:
        self._provider = provider
        self._model_name = model_name
        self._capabilities = capabilities or ModelCapabilities(
            streaming=True,
            tool_calling=True,
            structured_output=True,
            reasoning=True,
        )
        self._specs: list[MockResponseSpec] = [_coerce_spec(r) for r in responses]
        self._cycle = cycle
        self._cursor = 0
        self.recorded_requests: list[ModelRequest] = []

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    def reset(self) -> None:
        """Reset response cursor and recorded request history."""
        self._cursor = 0
        self.recorded_requests.clear()

    def _next_spec(self, request: ModelRequest) -> MockResponseSpec:
        self.recorded_requests.append(request)
        if not self._specs:
            raise ModelError(
                "MockModel has no scripted responses configured.",
                code="MOCK_EXHAUSTED",
            )
        if self._cursor >= len(self._specs):
            if self._cycle:
                self._cursor = 0
            else:
                raise ModelError(
                    "MockModel scripted responses exhausted.",
                    code="MOCK_EXHAUSTED",
                    details={"total_responses": len(self._specs)},
                )
        spec = self._specs[self._cursor]
        self._cursor += 1
        return spec

    def _build_response(self, spec: MockResponseSpec, request_id: str) -> ModelResponse:
        if spec.malformed:
            raise ModelError(
                "Model returned a malformed response payload (missing content and tool_calls).",
                code="MALFORMED_MODEL_RESPONSE",
                details={"request_id": request_id},
            )
        finish_reason: FinishReason = spec.finish_reason or (
            "tool_calls" if spec.tool_calls else "stop"
        )
        return ModelResponse(
            text=spec.text,
            tool_calls=spec.tool_calls,
            finish_reason=finish_reason,
            usage=spec.usage,
            request_id=request_id,
            model_name=self._model_name,
            provider=self._provider,
            metadata=dict(spec.metadata),
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.validate_request(request)
        spec = self._next_spec(request)
        request_id = f"mock_req_{uuid.uuid4().hex[:12]}"

        if spec.delay_seconds > 0:
            await asyncio.sleep(spec.delay_seconds)

        if spec.error is not None:
            if isinstance(spec.error, ModelError):
                raise spec.error
            raise ProviderError(
                f"Simulated provider failure: {spec.error}",
                cause=spec.error,
                details={"request_id": request_id},
            ) from spec.error

        return self._build_response(spec, request_id)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamDelta]:
        self.validate_request(request)
        spec = self._next_spec(request)
        request_id = f"mock_req_{uuid.uuid4().hex[:12]}"

        if spec.delay_seconds > 0:
            await asyncio.sleep(spec.delay_seconds)

        if spec.error is not None:
            if isinstance(spec.error, ModelError):
                raise spec.error
            raise ProviderError(
                f"Simulated provider failure: {spec.error}",
                cause=spec.error,
                details={"request_id": request_id},
            ) from spec.error

        response = self._build_response(spec, request_id)

        chunks: tuple[str, ...]
        if spec.chunks is not None:
            chunks = spec.chunks
        elif response.text:
            # Deterministic word-level chunking for realistic streaming tests.
            words = response.text.split(" ")
            chunks = tuple(f"{w} " if idx < len(words) - 1 else w for idx, w in enumerate(words))
        else:
            chunks = ()

        for chunk in chunks:
            yield ModelStreamDelta(
                text_delta=chunk,
                request_id=request_id,
                is_final=False,
            )

        yield ModelStreamDelta(
            text_delta="",
            request_id=request_id,
            is_final=True,
            response=response,
        )
