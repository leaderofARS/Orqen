"""Example 1: Persistent Session with multiple agent tasks and history retrieval."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from orqen import Agent, Runtime, SessionManager, SQLiteStorage
from orqen.models import MockModel


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "orqen.db"
        async with SQLiteStorage(database_path=db_path) as storage:
            sessions = SessionManager(
                storage.sessions,
                tasks=storage.tasks,
                events=storage.events,
            )
            session = await sessions.create(title="Architecture Review")

            model = MockModel(
                responses=[
                    {"text": "Step 1 complete: identified module boundaries."},
                    {"text": "Step 2 complete: verified SQLite WAL persistence."},
                ]
            )
            agent = Agent(name="reviewer", model=model, instructions="Be concise.")

            async with Runtime(storage=storage) as runtime:
                r1 = await runtime.run(agent, input="Audit modules", session=session)
                r2 = await runtime.run(agent, input="Verify persistence", session=session)
                print(f"Task 1 ({r1.task_id}): {r1.output}")
                print(f"Task 2 ({r2.task_id}): {r2.output}")

            tasks = await sessions.get_tasks(session.session_id)
            print(f"Session {session.session_id} recorded {len(tasks)} tasks.")


if __name__ == "__main__":
    asyncio.run(main())
