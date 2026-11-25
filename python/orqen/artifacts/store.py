"""Filesystem-backed ArtifactStore with SQLite metadata and SHA-256 integrity."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orqen.artifacts.models import ArtifactMetadata
from orqen.config.settings import ArtifactsConfig
from orqen.exceptions import ArtifactError, SessionError, ValidationError
from orqen.sessions.lifecycle import SessionState
from orqen.telemetry.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from orqen.storage.repositories import ArtifactRepository, SessionRepository

_logger = get_logger("artifacts")
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_safe_id(value: str, *, field_name: str) -> None:
    if not value or not _SAFE_ID_PATTERN.match(value):
        raise ArtifactError(
            f"Invalid {field_name} '{value}': only alphanumeric, '_' and '-' are allowed.",
            code="INVALID_ARTIFACT_ID",
            details={"field": field_name},
        )


def _validate_filename(filename: str) -> str:
    if not filename or not filename.strip():
        raise ArtifactError(
            "Artifact filename must not be empty.", code="INVALID_ARTIFACT_FILENAME"
        )
    if "\x00" in filename or "/" in filename or "\\" in filename:
        raise ArtifactError(
            f"Unsafe artifact filename '{filename}': path separators and null bytes are forbidden.",
            code="UNSAFE_ARTIFACT_FILENAME",
            details={"filename": filename},
        )
    if filename in (".", "..") or ".." in filename:
        raise ArtifactError(
            f"Unsafe artifact filename '{filename}': path traversal segments are forbidden.",
            code="UNSAFE_ARTIFACT_FILENAME",
            details={"filename": filename},
        )
    return filename


class ArtifactStore:
    """Split-storage artifact manager (filesystem bytes + SQLite metadata)."""

    def __init__(
        self,
        artifacts: ArtifactRepository,
        *,
        config: ArtifactsConfig | None = None,
        directory: Path | str | None = None,
        sessions: SessionRepository | None = None,
    ) -> None:
        cfg = config or ArtifactsConfig()
        if directory is not None:
            cfg = cfg.model_copy(update={"directory": Path(directory)})
        self.config = cfg
        self.root_dir = cfg.directory
        self._artifacts = artifacts
        self._sessions = sessions

    def _resolve_target_path(self, storage_key: str) -> Path:
        root_resolved = self.root_dir.resolve()
        candidate = (root_resolved / storage_key).resolve()
        if not candidate.is_relative_to(root_resolved):
            raise ArtifactError(
                "Resolved artifact path escapes storage root.",
                code="ARTIFACT_PATH_ESCAPE",
            )
        if candidate.is_symlink():
            raise ArtifactError(
                "Artifact target path must not be a symlink.",
                code="UNSAFE_ARTIFACT_SYMLINK",
            )
        return candidate

    def _write_file_atomic(self, target_path: Path, data: bytes) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".tmp_orqen_",
            dir=str(target_path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    async def store(
        self,
        *,
        session_id: str,
        filename: str,
        content: bytes | str,
        task_id: str | None = None,
        content_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactMetadata:
        """Persist artifact bytes to disk and record metadata in SQLite."""
        _validate_safe_id(session_id, field_name="session_id")
        clean_filename = _validate_filename(filename)

        if self._sessions is not None:
            session = await self._sessions.get(session_id)
            if session is None:
                raise SessionError(
                    f"Session '{session_id}' not found.",
                    code="SESSION_NOT_FOUND",
                    details={"session_id": session_id},
                )
            if session.status == SessionState.CLOSED:
                raise SessionError(
                    f"Cannot store artifact in closed session '{session_id}'.",
                    code="SESSION_CLOSED",
                    details={"session_id": session_id},
                )

        raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
        size_bytes = len(raw_bytes)
        if size_bytes > self.config.max_artifact_bytes:
            raise ArtifactError(
                f"Artifact size ({size_bytes} bytes) exceeds limit "
                f"({self.config.max_artifact_bytes} bytes).",
                code="ARTIFACT_TOO_LARGE",
                details={"size_bytes": size_bytes, "max_bytes": self.config.max_artifact_bytes},
            )

        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        artifact_id = f"art_{uuid.uuid4().hex[:16]}"
        storage_key = f"{session_id}/{artifact_id}"
        target_path = self._resolve_target_path(storage_key)

        try:
            await asyncio.to_thread(self._write_file_atomic, target_path, raw_bytes)
        except OSError as exc:
            raise ArtifactError(
                f"Failed to write artifact file: {exc}",
                code="ARTIFACT_WRITE_FAILED",
                details={"artifact_id": artifact_id},
                cause=exc,
            ) from exc

        meta = ArtifactMetadata(
            artifact_id=artifact_id,
            session_id=session_id,
            task_id=task_id,
            filename=clean_filename,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            storage_key=storage_key,
            metadata=dict(metadata) if metadata is not None else {},
        )
        try:
            persisted = await self._artifacts.create(meta)
        except BaseException:
            await asyncio.to_thread(target_path.unlink, missing_ok=True)
            raise

        _logger.info(
            "Artifact stored",
            extra={
                "artifact_id": persisted.artifact_id,
                "session_id": session_id,
                "size_bytes": size_bytes,
            },
        )
        return persisted

    async def get_metadata(self, artifact_id: str) -> ArtifactMetadata | None:
        """Retrieve artifact metadata by ID, or `None` if absent."""
        return await self._artifacts.get(artifact_id)

    async def _require_metadata(self, artifact_id: str) -> ArtifactMetadata:
        meta = await self._artifacts.get(artifact_id)
        if meta is None:
            raise ArtifactError(
                f"Artifact '{artifact_id}' not found.",
                code="ARTIFACT_NOT_FOUND",
                details={"artifact_id": artifact_id},
            )
        return meta

    async def read(self, artifact_id: str, *, verify: bool = True) -> bytes:
        """Read full artifact bytes from disk, optionally verifying SHA-256 integrity."""
        meta = await self._require_metadata(artifact_id)
        target_path = self._resolve_target_path(meta.storage_key)
        if not target_path.exists():
            raise ArtifactError(
                f"Artifact payload missing on disk for '{artifact_id}'.",
                code="ARTIFACT_FILE_MISSING",
                details={"artifact_id": artifact_id},
            )
        data = await asyncio.to_thread(target_path.read_bytes)
        if verify:
            actual_hash = hashlib.sha256(data).hexdigest()
            if actual_hash != meta.sha256 or len(data) != meta.size_bytes:
                raise ArtifactError(
                    f"Artifact '{artifact_id}' failed integrity verification.",
                    code="ARTIFACT_CHECKSUM_MISMATCH",
                    details={"artifact_id": artifact_id},
                )
        return data

    async def stream(
        self,
        artifact_id: str,
        *,
        chunk_size: int = 65_536,
        verify: bool = True,
    ) -> AsyncIterator[bytes]:
        """Stream artifact bytes in chunks, verifying integrity at EOF when `verify=True`."""
        if chunk_size < 1:
            raise ValidationError("chunk_size must be >= 1.", code="INVALID_CHUNK_SIZE")
        meta = await self._require_metadata(artifact_id)
        target_path = self._resolve_target_path(meta.storage_key)
        if not target_path.exists():
            raise ArtifactError(
                f"Artifact payload missing on disk for '{artifact_id}'.",
                code="ARTIFACT_FILE_MISSING",
                details={"artifact_id": artifact_id},
            )

        hasher = hashlib.sha256() if verify else None
        total_read = 0
        handle = await asyncio.to_thread(target_path.open, "rb")
        try:
            while True:
                chunk = await asyncio.to_thread(handle.read, chunk_size)
                if not chunk:
                    break
                total_read += len(chunk)
                if hasher is not None:
                    hasher.update(chunk)
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

        if hasher is not None and (
            hasher.hexdigest() != meta.sha256 or total_read != meta.size_bytes
        ):
            raise ArtifactError(
                f"Artifact '{artifact_id}' failed integrity verification.",
                code="ARTIFACT_CHECKSUM_MISMATCH",
                details={"artifact_id": artifact_id},
            )

    async def verify_integrity(self, artifact_id: str) -> bool:
        """Return `True` if on-disk bytes match persisted size and SHA-256 checksum."""
        meta = await self._require_metadata(artifact_id)
        target_path = self._resolve_target_path(meta.storage_key)
        if not target_path.exists():
            raise ArtifactError(
                f"Artifact payload missing on disk for '{artifact_id}'.",
                code="ARTIFACT_FILE_MISSING",
                details={"artifact_id": artifact_id},
            )

        def _hash_file() -> tuple[int, str]:
            hasher = hashlib.sha256()
            size = 0
            with target_path.open("rb") as handle:
                while chunk := handle.read(65_536):
                    size += len(chunk)
                    hasher.update(chunk)
            return size, hasher.hexdigest()

        size, digest = await asyncio.to_thread(_hash_file)
        return size == meta.size_bytes and digest == meta.sha256

    async def list(
        self,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ArtifactMetadata, ...]:
        """List artifact metadata records with bounded pagination."""
        if limit < 1 or limit > 1000 or offset < 0:
            raise ValidationError("Invalid pagination parameters.", code="INVALID_PAGINATION")
        return await self._artifacts.list(
            session_id=session_id,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )

    async def delete(self, artifact_id: str) -> bool:
        """Delete artifact metadata from SQLite and unlink the payload file from disk."""
        meta = await self._artifacts.get(artifact_id)
        if meta is None:
            return False
        deleted = await self._artifacts.delete(artifact_id)
        if deleted:
            target_path = self._resolve_target_path(meta.storage_key)
            await asyncio.to_thread(target_path.unlink, missing_ok=True)
            _logger.info("Artifact deleted", extra={"artifact_id": artifact_id})
        return deleted
