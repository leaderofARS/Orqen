"""Example 4: Storing and retrieving large outputs with ArtifactStore."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from orqen import ArtifactStore, SessionManager, SQLiteStorage


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        async with SQLiteStorage(database_path=root / "artifacts.db") as storage:
            sessions = SessionManager(storage.sessions, artifacts=storage.artifacts)
            artifacts = ArtifactStore(
                storage.artifacts,
                directory=root / "artifacts",
                sessions=storage.sessions,
            )
            session = await sessions.create(title="Artifact Demo")

            large_report = ("ORQEN Phase 2 execution trace line\n" * 2000).encode("utf-8")
            meta = await artifacts.store(
                session_id=session.session_id,
                filename="execution_trace.txt",
                content=large_report,
                content_type="text/plain",
                metadata={"format": "utf-8"},
            )
            print(
                f"Stored artifact {meta.artifact_id} ({meta.size_bytes} bytes, "
                f"sha256={meta.sha256[:12]}...)"
            )

            valid = await artifacts.verify_integrity(meta.artifact_id)
            loaded = await artifacts.read(meta.artifact_id)
            print(f"Integrity valid={valid}, bytes read={len(loaded)}")


if __name__ == "__main__":
    asyncio.run(main())
