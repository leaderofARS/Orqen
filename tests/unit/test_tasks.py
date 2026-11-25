"""Unit tests for Task lifecycle, sanitization, and queries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from orqen.exceptions import ExecutionError, LifecycleError, SessionError
from orqen.sessions import SessionManager
from orqen.storage import SQLiteStorage
from orqen.tasks import TaskManager, TaskState

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_task_lifecycle_completion_failure_cancel_timeout(tmp_path: Path) -> None:
    async with SQLiteStorage(database_path=tmp_path / "tasks.db") as storage:
        sm = SessionManager(storage.sessions)
        tm = TaskManager(storage.tasks, sessions=storage.sessions, events=storage.events)
        sess = await sm.create(title="Task Host")

        # Complete flow
        t1 = await tm.create(session_id=sess.session_id, input="Q1", agent_name="agent_a")
        assert t1.status == TaskState.PENDING
        t1 = await tm.start(t1.task_id, run_id="run_1")
        assert t1.status == TaskState.RUNNING
        assert t1.started_at is not None
        t1 = await tm.complete(t1.task_id, output="A1")
        assert t1.status == TaskState.COMPLETED
        assert t1.output == "A1"
        assert t1.completed_at is not None
        assert t1.duration_seconds is not None
        assert t1.duration_seconds >= 0.0

        # Fail flow with secret sanitization
        t2 = await tm.create(session_id=sess.session_id, input="Q2", agent_name="agent_a")
        await tm.start(t2.task_id, run_id="run_2")
        err = ExecutionError(
            "Upstream failed with Bearer secret_token_123",
            details={"api_key": "sk-1234567890abcdef"},
        )
        t2_failed = await tm.fail(t2.task_id, error=err)
        assert t2_failed.status == TaskState.FAILED
        assert t2_failed.error is not None
        assert "secret_token_123" not in str(t2_failed.error)
        assert "sk-1234567890abcdef" not in str(t2_failed.error)
        assert "[REDACTED]" in str(t2_failed.error)

        # Cancel from PENDING and RUNNING
        t3 = await tm.create(session_id=sess.session_id, input="Q3", agent_name="agent_b")
        t3_cancelled = await tm.cancel(t3.task_id, reason="User cancelled")
        assert t3_cancelled.status == TaskState.CANCELLED

        # Timeout from RUNNING
        t4 = await tm.create(session_id=sess.session_id, input="Q4", agent_name="agent_b")
        await tm.start(t4.task_id, run_id="run_4")
        t4_timed = await tm.timeout(t4.task_id)
        assert t4_timed.status == TaskState.TIMED_OUT


@pytest.mark.asyncio
async def test_task_invalid_transitions_and_closed_session_rejected(tmp_path: Path) -> None:
    async with SQLiteStorage(database_path=tmp_path / "task_err.db") as storage:
        sm = SessionManager(storage.sessions)
        tm = TaskManager(storage.tasks, sessions=storage.sessions)
        sess = await sm.create()
        t = await tm.create(session_id=sess.session_id, input="Hello")

        with pytest.raises(LifecycleError):
            await tm.complete(t.task_id, output="Too early")

        await tm.start(t.task_id)
        await tm.complete(t.task_id, output="Done")

        # Terminal states cannot transition
        with pytest.raises(LifecycleError):
            await tm.start(t.task_id)
        with pytest.raises(LifecycleError):
            await tm.fail(t.task_id, error=RuntimeError("nope"))

        await sm.close(sess.session_id)
        with pytest.raises(SessionError) as exc_info:
            await tm.create(session_id=sess.session_id, input="Blocked")
        assert exc_info.value.code == "SESSION_CLOSED"


@pytest.mark.asyncio
async def test_task_listing_filtering_pagination_and_isolation(tmp_path: Path) -> None:
    async with SQLiteStorage(database_path=tmp_path / "task_list.db") as storage:
        sm = SessionManager(storage.sessions, tasks=storage.tasks)
        tm = TaskManager(storage.tasks, sessions=storage.sessions)
        s1 = await sm.create(title="S1")
        s2 = await sm.create(title="S2")

        t1 = await tm.create(session_id=s1.session_id, input="1", agent_name="alpha")
        t2 = await tm.create(session_id=s1.session_id, input="2", agent_name="beta")
        t3 = await tm.create(session_id=s2.session_id, input="3", agent_name="alpha")
        await tm.start(t1.task_id)
        await tm.complete(t1.task_id, output="ok")

        assert await tm.count(session_id=s1.session_id) == 2
        assert await tm.count(session_id=s1.session_id, status=TaskState.COMPLETED) == 1
        assert len(await sm.get_tasks(s1.session_id, limit=1, offset=0)) == 1
        assert len(await sm.get_tasks(s1.session_id, limit=1, offset=1)) == 1

        s2_tasks = await tm.list(session_id=s2.session_id)
        assert [t.task_id for t in s2_tasks] == [t3.task_id]
        assert t2.task_id not in {t.task_id for t in s2_tasks}
