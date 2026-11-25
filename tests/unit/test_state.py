"""Unit tests for persistent StateManager, scopes, and optimistic concurrency."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from orqen.exceptions import StateConflictError, StateError
from orqen.sessions import SessionManager
from orqen.state import StateManager
from orqen.storage import SQLiteStorage
from orqen.tasks import TaskManager

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_state_crud_scopes_and_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    async with SQLiteStorage(database_path=db_path) as storage:
        sm = SessionManager(storage.sessions)
        tm = TaskManager(storage.tasks, sessions=storage.sessions)
        state = StateManager(storage.state)

        s1 = await sm.create()
        s2 = await sm.create()
        t1 = await tm.create(session_id=s1.session_id, input="Task 1")

        # Session-scoped state
        e1 = await state.set(s1.session_id, "step", 1)
        assert e1.revision == 1
        assert e1.value == 1

        # Namespace isolation
        e_ns = await state.set(s1.session_id, "step", "other_ns", namespace="custom")
        assert e_ns.revision == 1
        assert await state.get_value(s1.session_id, "step") == 1
        assert await state.get_value(s1.session_id, "step", namespace="custom") == "other_ns"

        # Session isolation
        assert await state.get(s2.session_id, "step") is None

        # Task-scoped state isolated from session-scoped state
        e_task = await state.set(s1.session_id, "step", 99, task_id=t1.task_id)
        assert e_task.revision == 1
        assert await state.get_value(s1.session_id, "step") == 1
        assert await state.get_value(s1.session_id, "step", task_id=t1.task_id) == 99

        # List keys & get_scope
        await state.set(s1.session_id, "flag", True)
        assert await state.list_keys(s1.session_id) == ("flag", "step")
        assert await state.get_scope(s1.session_id) == {"flag": True, "step": 1}

        assert await state.delete(s1.session_id, "flag") is True
        assert await state.delete(s1.session_id, "flag") is False

    # Reopen and verify persistence
    async with SQLiteStorage(database_path=db_path) as storage2:
        state2 = StateManager(storage2.state)
        assert await state2.get_value(s1.session_id, "step") == 1
        assert await state2.get_value(s1.session_id, "step", task_id=t1.task_id) == 99


@pytest.mark.asyncio
async def test_state_rejects_non_json_values(tmp_path: Path) -> None:
    async with SQLiteStorage(database_path=tmp_path / "json_val.db") as storage:
        sm = SessionManager(storage.sessions)
        state = StateManager(storage.state)
        sess = await sm.create()

        class CustomObj:
            pass

        for invalid in (CustomObj(), {1, 2, 3}, float("nan"), float("inf")):
            with pytest.raises(StateError) as exc_info:
                await state.set(sess.session_id, "bad", invalid)
            assert exc_info.value.code == "INVALID_STATE_VALUE"


@pytest.mark.asyncio
async def test_state_optimistic_concurrency_and_conflicts(tmp_path: Path) -> None:
    async with SQLiteStorage(database_path=tmp_path / "conflict.db") as storage:
        sm = SessionManager(storage.sessions)
        state = StateManager(storage.state)
        sess = await sm.create()

        entry = await state.set(sess.session_id, "counter", 1)
        assert entry.revision == 1

        updated = await state.update(sess.session_id, "counter", 2, expected_revision=1)
        assert updated.revision == 2
        assert updated.value == 2

        # Stale revision fails
        with pytest.raises(StateConflictError) as exc_info:
            await state.update(sess.session_id, "counter", 999, expected_revision=1)
        assert exc_info.value.code == "STATE_REVISION_CONFLICT"
        assert await state.get_value(sess.session_id, "counter") == 2

        # Concurrent updates with same expected_revision=2: exactly 1 wins
        results = await asyncio.gather(
            *[
                state.update(sess.session_id, "counter", 10 + i, expected_revision=2)
                for i in range(5)
            ],
            return_exceptions=True,
        )
        successes = [r for r in results if not isinstance(r, BaseException)]
        conflicts = [r for r in results if isinstance(r, StateConflictError)]
        assert len(successes) == 1
        assert len(conflicts) == 4
