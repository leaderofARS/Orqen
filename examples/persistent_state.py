"""Example 2: Revision-aware persistent state and optimistic concurrency conflict."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from orqen import SessionManager, SQLiteStorage, StateConflictError, StateManager


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        async with SQLiteStorage(database_path=db_path) as storage:
            sessions = SessionManager(storage.sessions)
            state = StateManager(storage.state)
            session = await sessions.create(title="State Demo")

            entry = await state.set(session.session_id, "step", {"phase": "init", "count": 1})
            print(f"Created key 'step' at revision {entry.revision}: {entry.value}")

            updated = await state.update(
                session.session_id,
                "step",
                {"phase": "running", "count": 2},
                expected_revision=entry.revision,
            )
            print(f"Updated key 'step' to revision {updated.revision}: {updated.value}")

            try:
                await state.update(
                    session.session_id,
                    "step",
                    {"phase": "stale", "count": 99},
                    expected_revision=1,
                )
            except StateConflictError as exc:
                print(f"Caught expected conflict [{exc.code}]: {exc.message}")


if __name__ == "__main__":
    asyncio.run(main())
