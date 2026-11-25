"""Integration tests for optional session-backed Runtime execution."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from orqen import Agent, Runtime, SessionManager, SQLiteStorage, TaskManager, TaskState
from orqen.exceptions import ModelError, OrqenTimeoutError, SessionError, StorageError
from orqen.models import MockModel
from orqen.tools import tool

if TYPE_CHECKING:
    from pathlib import Path


@tool
async def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


@pytest.mark.asyncio
async def test_standalone_runtime_still_works_without_storage(tmp_path: Path) -> None:
    model = MockModel(responses=[{"text": "Standalone OK"}])
    agent = Agent(name="standalone", model=model)
    async with Runtime() as runtime:
        result = await runtime.run(agent, input="Hi")
        assert result.output == "Standalone OK"
        assert result.session_id is None
        assert result.task_id is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_session_required_without_storage_raises() -> None:
    model = MockModel(responses=[{"text": "Hi"}])
    agent = Agent(name="agent", model=model)
    async with Runtime() as runtime:
        with pytest.raises(StorageError) as exc_info:
            await runtime.run(agent, input="Hi", session="sess_123")
        assert exc_info.value.code == "PERSISTENCE_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_session_backed_run_and_stream_persist_tasks_and_events(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    async with SQLiteStorage(database_path=db_path) as storage:
        sm = SessionManager(
            storage.sessions,
            tasks=storage.tasks,
            events=storage.events,
            artifacts=storage.artifacts,
        )
        tm = TaskManager(storage.tasks, sessions=storage.sessions, events=storage.events)

        s1 = await sm.create(title="Session 1")
        s2 = await sm.create(title="Session 2")

        model = MockModel(
            responses=[
                {"tool_calls": [{"id": "c1", "name": "multiply", "arguments": {"a": 6, "b": 7}}]},
                {"text": "42"},
                {"text": "Streamed reply"},
            ]
        )
        agent = Agent(name="math", model=model, tools=[multiply])

        async with Runtime(storage=storage) as runtime:
            # Task 1 via run()
            r1 = await runtime.run(agent, input="6*7?", session=s1)
            assert r1.output == "42"
            assert r1.session_id == s1.session_id
            assert r1.task_id is not None

            # Task 2 via stream() in the same session
            stream = runtime.stream(agent, input="Stream please", session=s1)
            streamed_events = [evt async for evt in stream]
            r2 = stream.result
            assert r2.output == "Streamed reply"
            assert r2.session_id == s1.session_id
            assert r2.task_id is not None
            assert r2.task_id != r1.task_id
            assert any(e.event_type == "model.stream.chunk" for e in streamed_events)

            assert runtime.metrics.tasks_created_total == 2
            assert runtime.metrics.tasks_completed_total == 2
            assert runtime.metrics.events_persisted_total > 0

        # Verify persisted tasks & events
        tasks_s1 = await sm.get_tasks(s1.session_id)
        assert len(tasks_s1) == 2
        assert all(t.status == TaskState.COMPLETED for t in tasks_s1)
        assert tasks_s1[0].output == "42"
        assert tasks_s1[1].output == "Streamed reply"

        # Session 2 remains isolated
        assert await sm.get_tasks(s2.session_id) == ()

        # Events for Task 1 include tool execution and are strictly ordered
        t1_events = await tm.get_events(r1.task_id)
        t1_types = [e.event_type for e in t1_events]
        assert t1_types[0] == "run.started"
        assert "tool.call.requested" in t1_types
        assert "tool.execution.started" in t1_types
        assert "tool.execution.completed" in t1_types
        assert t1_types[-1] == "run.completed"
        assert [e.sequence_number for e in t1_events] == sorted(
            e.sequence_number for e in t1_events
        )


@pytest.mark.asyncio
async def test_session_backed_failure_cancellation_and_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "failures.db"
    async with SQLiteStorage(database_path=db_path) as storage:
        sm = SessionManager(storage.sessions, tasks=storage.tasks, events=storage.events)
        sess = await sm.create(title="Failures")

        async with Runtime(storage=storage) as runtime:
            # 1. Model failure
            fail_agent = Agent(
                name="failer",
                model=MockModel(responses=[RuntimeError("API key sk-secret1234567890 failed")]),
            )
            with pytest.raises(ModelError):
                await runtime.run(fail_agent, input="Fail", session=sess)

            # 2. Timeout
            slow_agent = Agent(
                name="slower",
                model=MockModel(responses=[{"text": "late", "delay_seconds": 1.0}]),
            )
            with pytest.raises(OrqenTimeoutError):
                await runtime.run(slow_agent, input="Timeout", session=sess, timeout_seconds=0.05)

            # 3. Deterministic cancellation via asyncio.Task.cancel()
            started = asyncio.Event()

            @tool
            async def blocking_tool() -> str:
                started.set()
                await asyncio.Future()  # waits until cancelled
                return "unreachable"

            cancel_agent = Agent(
                name="canceller",
                model=MockModel(
                    responses=[{"tool_calls": [{"id": "c1", "name": "blocking_tool"}]}]
                ),
                tools=[blocking_tool],
            )
            run_task = asyncio.create_task(
                runtime.run(cancel_agent, input="Cancel me", session=sess)
            )
            await started.wait()
            run_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run_task

        tasks = await sm.get_tasks(sess.session_id)
        assert [t.status for t in tasks] == [
            TaskState.FAILED,
            TaskState.TIMED_OUT,
            TaskState.CANCELLED,
        ]
        assert tasks[0].error is not None
        assert "sk-secret1234567890" not in str(tasks[0].error)


@pytest.mark.asyncio
async def test_closed_session_rejected_and_persistence_failure_surfaces(tmp_path: Path) -> None:
    db_path = tmp_path / "closed_err.db"
    async with SQLiteStorage(database_path=db_path) as storage:
        sm = SessionManager(storage.sessions)
        sess = await sm.create()
        await sm.close(sess.session_id)

        agent = Agent(name="a", model=MockModel(responses=[{"text": "ok"}]))
        async with Runtime(storage=storage) as runtime:
            with pytest.raises(SessionError) as exc_info:
                await runtime.run(agent, input="Hi", session=sess)
            assert exc_info.value.code == "SESSION_CLOSED"

        # Now test persistence failure when storage is closed before run
        open_sess = await sm.create()
        await storage.close()
        async with Runtime(storage=storage) as runtime:
            with pytest.raises(StorageError):
                await runtime.run(agent, input="Hi", session=open_sess)
