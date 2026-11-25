"""Example 3: Persisted runtime event history in deterministic sequence order."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from orqen import Agent, Runtime, SessionManager, SQLiteStorage
from orqen.models import MockModel
from orqen.tools import tool


@tool
async def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "events.db"
        async with SQLiteStorage(database_path=db_path) as storage:
            sessions = SessionManager(storage.sessions, events=storage.events)
            session = await sessions.create(title="Event History Demo")

            model = MockModel(
                responses=[
                    {"tool_calls": [{"id": "c1", "name": "add", "arguments": {"a": 19, "b": 23}}]},
                    {"text": "The sum is 42."},
                ]
            )
            agent = Agent(name="calculator", model=model, tools=[add])

            async with Runtime(storage=storage) as runtime:
                result = await runtime.run(agent, input="Compute 19 + 23", session=session)
                print(f"Output: {result.output}")

            events = await sessions.get_events(session.session_id)
            for evt in events:
                print(f"seq={evt.sequence_number} type={evt.event_type} task={evt.task_id}")


if __name__ == "__main__":
    asyncio.run(main())
