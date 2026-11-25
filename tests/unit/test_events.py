"""Unit tests for EventRepository persistence, ordering, filtering, and concurrency."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from orqen.events import (
    ModelRequestCompleted,
    ModelRequestStarted,
    RunCompleted,
    RunStarted,
)
from orqen.exceptions import RepositoryError
from orqen.models.types import TokenUsage
from orqen.sessions import SessionManager
from orqen.storage import SQLiteStorage
from orqen.tasks import TaskManager

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_event_append_ordering_filtering_and_pagination(tmp_path: Path) -> None:
    async with SQLiteStorage(database_path=tmp_path / "events.db") as storage:
        sm = SessionManager(storage.sessions, events=storage.events)
        tm = TaskManager(storage.tasks, sessions=storage.sessions, events=storage.events)
        sess = await sm.create()
        t1 = await tm.create(session_id=sess.session_id, input="One")
        t2 = await tm.create(session_id=sess.session_id, input="Two")

        fixed_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        e1 = RunStarted(
            run_id="run_1",
            agent_id="a1",
            agent_name="agent",
            timestamp=fixed_ts,
        )
        e2 = ModelRequestStarted(
            run_id="run_1",
            agent_id="a1",
            agent_name="agent",
            iteration=1,
            model_name="mock",
            provider="mock",
            timestamp=fixed_ts,
        )
        e3 = ModelRequestCompleted(
            run_id="run_1",
            agent_id="a1",
            agent_name="agent",
            iteration=1,
            request_id="req_1",
            finish_reason="stop",
            usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
            timestamp=fixed_ts,
        )
        e4 = RunCompleted(
            run_id="run_2",
            agent_id="a1",
            agent_name="agent",
            output="done",
            model_calls=1,
            tool_calls=0,
            usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            timestamp=fixed_ts,
        )

        batch = await storage.events.append_batch(
            session_id=sess.session_id,
            task_id=t1.task_id,
            events=[e1, e2, e3],
        )
        assert [p.sequence_number for p in batch] == [1, 2, 3]

        p4 = await storage.events.append(
            session_id=sess.session_id,
            task_id=t2.task_id,
            event=e4,
        )
        assert p4.sequence_number == 4

        # Filter by session, task, run, event_type, after_sequence, and paginate
        assert len(await sm.get_events(sess.session_id)) == 4
        assert [e.sequence_number for e in await tm.get_events(t1.task_id)] == [1, 2, 3]
        assert [
            e.sequence_number for e in await sm.get_events(sess.session_id, run_id="run_2")
        ] == [4]
        assert [
            e.sequence_number
            for e in await sm.get_events(sess.session_id, event_type="run.started")
        ] == [1]
        assert [
            e.sequence_number for e in await sm.get_events(sess.session_id, after_sequence=2)
        ] == [3, 4]
        assert [
            e.sequence_number for e in await sm.get_events(sess.session_id, limit=2, offset=1)
        ] == [2, 3]


@pytest.mark.asyncio
async def test_event_duplicate_rejected_and_concurrent_append_ordered(tmp_path: Path) -> None:
    async with SQLiteStorage(database_path=tmp_path / "events_conc.db") as storage:
        sm = SessionManager(storage.sessions)
        sess = await sm.create()

        dup = RunStarted(event_id="evt_fixed_dup", run_id="run_1", agent_id="a1", agent_name="a")
        await storage.events.append(session_id=sess.session_id, task_id=None, event=dup)

        with pytest.raises(RepositoryError) as exc_info:
            await storage.events.append(session_id=sess.session_id, task_id=None, event=dup)
        assert exc_info.value.code == "EVENT_PERSISTENCE_CONFLICT"

        # Concurrent appends produce contiguous unique sequences 2..11
        events = [RunStarted(run_id=f"run_{i}", agent_id="a1", agent_name="a") for i in range(10)]
        persisted = await asyncio.gather(
            *[
                storage.events.append(session_id=sess.session_id, task_id=None, event=evt)
                for evt in events
            ]
        )
        seqs = sorted(p.sequence_number for p in persisted)
        assert seqs == list(range(2, 12))
