"""Artifact metadata domain model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ArtifactMetadata(BaseModel):
    """Persisted metadata for a filesystem-backed artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(default_factory=lambda: f"art_{uuid.uuid4().hex[:16]}")
    session_id: str
    task_id: str | None = None
    filename: str
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(ge=0)
    sha256: str
    storage_key: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
