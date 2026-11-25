"""Unit tests for ArtifactStore filesystem persistence, security, and integrity."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from orqen.artifacts import ArtifactStore
from orqen.config import ArtifactsConfig
from orqen.exceptions import ArtifactError, RepositoryError, ValidationError
from orqen.sessions import SessionManager
from orqen.storage import SQLiteStorage

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_artifact_store_read_stream_verify_and_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "artifacts.db"
    art_dir = tmp_path / "artifacts"

    async with SQLiteStorage(database_path=db_path) as storage:
        sm = SessionManager(storage.sessions, artifacts=storage.artifacts)
        store = ArtifactStore(storage.artifacts, directory=art_dir, sessions=storage.sessions)
        sess = await sm.create(title="Artifacts")

        payload = b"ORQEN-" * 30_000  # ~180 KiB
        meta = await store.store(
            session_id=sess.session_id,
            filename="report.bin",
            content=payload,
            content_type="application/octet-stream",
            metadata={"source": "test"},
        )
        assert meta.size_bytes == len(payload)
        assert meta.sha256 == hashlib.sha256(payload).hexdigest()
        assert await store.verify_integrity(meta.artifact_id) is True

        # Read & stream
        assert await store.read(meta.artifact_id) == payload
        chunks = [chunk async for chunk in store.stream(meta.artifact_id, chunk_size=16_384)]
        assert len(chunks) > 1
        assert b"".join(chunks) == payload

        # Filename collision produces distinct artifacts safely
        meta2 = await store.store(
            session_id=sess.session_id,
            filename="report.bin",
            content=b"second",
        )
        assert meta2.artifact_id != meta.artifact_id
        assert len(await sm.get_artifacts(sess.session_id)) == 2

        # Tampering detection
        disk_path = art_dir / meta.storage_key
        disk_path.write_bytes(b"corrupted")
        assert await store.verify_integrity(meta.artifact_id) is False
        with pytest.raises(ArtifactError) as exc_info:
            await store.read(meta.artifact_id, verify=True)
        assert exc_info.value.code == "ARTIFACT_CHECKSUM_MISMATCH"

        # Delete removes both metadata and file
        assert await store.delete(meta.artifact_id) is True
        assert await store.get_metadata(meta.artifact_id) is None
        assert not disk_path.exists()


@pytest.mark.asyncio
async def test_artifact_security_path_traversal_and_size_limit(tmp_path: Path) -> None:
    async with SQLiteStorage(database_path=tmp_path / "sec.db") as storage:
        sm = SessionManager(storage.sessions)
        cfg = ArtifactsConfig(directory=tmp_path / "art", max_artifact_bytes=16)
        store = ArtifactStore(storage.artifacts, config=cfg, sessions=storage.sessions)
        sess = await sm.create()

        for bad_name in ("../escape.txt", "sub/file.txt", "sub\\file.txt", "bad\x00.txt", ".."):
            with pytest.raises(ArtifactError):
                await store.store(session_id=sess.session_id, filename=bad_name, content=b"hi")

        with pytest.raises(ArtifactError) as size_err:
            await store.store(session_id=sess.session_id, filename="big.txt", content=b"x" * 17)
        assert size_err.value.code == "ARTIFACT_TOO_LARGE"


@pytest.mark.asyncio
async def test_artifact_db_failure_cleans_up_orphan_file(tmp_path: Path) -> None:
    art_dir = tmp_path / "art_fail"
    async with SQLiteStorage(database_path=tmp_path / "fail.db") as storage:
        sm = SessionManager(storage.sessions)
        store = ArtifactStore(storage.artifacts, directory=art_dir)
        sess = await sm.create()

        # Pass a non-JSON-serializable metadata dict to trigger DB repository validation failure
        # after file write -> compensating cleanup must remove the file on disk.
        with pytest.raises(ValidationError):
            await store.store(
                session_id=sess.session_id,
                filename="orphan.txt",
                content=b"should-not-remain",
                metadata={"bad": object()},
            )

        session_dir = art_dir / sess.session_id
        remaining = list(session_dir.iterdir()) if session_dir.exists() else []
        assert remaining == []

        # Also verify foreign key failure (nonexistent session without session check) cleans up
        with pytest.raises(RepositoryError):
            await store.store(
                session_id="sess_missing",
                filename="fk_fail.txt",
                content=b"cleanup-me",
            )
        missing_dir = art_dir / "sess_missing"
        assert not missing_dir.exists() or list(missing_dir.iterdir()) == []
