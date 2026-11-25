"""Abstract provider-neutral model interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from orqen.exceptions import ModelError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from orqen.models.types import (
        ModelCapabilities,
        ModelRequest,
        ModelResponse,
        ModelStreamDelta,
    )


class Model(ABC):
    """Base contract for all inference model providers."""

    @property
    @abstractmethod
    def provider(self) -> str:
        """Identifier of the model provider (e.g. 'mock', 'openai')."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier within the provider."""

    @property
    @abstractmethod
    def capabilities(self) -> ModelCapabilities:
        """Explicit capabilities supported by this model."""

    def validate_request(self, request: ModelRequest) -> None:
        """Validate a request against this model's declared capabilities."""
        if request.stream and not self.capabilities.streaming:
            raise ModelError(
                f"Model '{self.provider}/{self.model_name}' does not support streaming.",
                code="CAPABILITY_UNSUPPORTED",
                details={"capability": "streaming", "model": self.model_name},
            )
        if request.tools and not self.capabilities.tool_calling:
            raise ModelError(
                f"Model '{self.provider}/{self.model_name}' does not support tool calling.",
                code="CAPABILITY_UNSUPPORTED",
                details={"capability": "tool_calling", "model": self.model_name},
            )

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate a complete response asynchronously."""

    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamDelta]:
        """Stream incremental response deltas asynchronously."""
