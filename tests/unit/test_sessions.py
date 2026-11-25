"""Unit tests for Session lifecycle, operations, and persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from orqen.exceptions import LifecycleError, SessionError, ValidationError
from orqen.sessions import SessionManager, SessionState
from orqen.storage import SQLiteStorage

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_session_lifecycle_and_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    async with SQLiteStorage(database_path=db_path) as storage:
        mgr = SessionManager(
            storage.sessions,
            tasks=storage.tasks,
            events=storage.events,
            artifacts=storage.artifacts,
        )
        sess = await mgr.create(title="Research", agent_id="agent_1", metadata={"env": "test"})
        assert sess.status == SessionState.CREATED
        assert sess.revision == 1
        assert sess.created_at.tzinfo is not None

        active = await mgr.activate(sess.session_id)
        assert active.status == SessionState.ACTIVE
        assert active.revision == 2

        # Idempotent activate
        still_active = await mgr.activate(sess.session_id)
        assert still_active.revision == 2

        idle = await mgr.mark_idle(sess.session_id)
        assert idle.status == SessionState.IDLE

        reactivated = await mgr.activate(sess.session_id)
        assert reactivated.status == SessionState.ACTIVE

        renamed = await mgr.rename(sess.session_id, "Renamed Research")
        assert renamed.title == "Renamed Research"

        updated = await mgr.update_metadata(sess.session_id, {"step": 2})
        assert updated.metadata == {"env": "test", "step": 2}

        closed = await mgr.close(sess.session_id)
        assert closed.status == SessionState.CLOSED

    # Verify across reopen
    async with SQLiteStorage(database_path=db_path) as storage2:
        mgr2 = SessionManager(storage2.sessions)
        loaded = await mgr2.require(sess.session_id)
        assert loaded.status == SessionState.CLOSED
        assert loaded.title == "Renamed Research"
        assert loaded.metadata == {"env": "test", "step": 2}


@pytest.mark.asyncio
async def test_session_invalid_transitions_and_closed_immutability(tmp_path: Path) -> None:
    async with SQLiteStorage(database_path=tmp_path / "invalid.db") as storage:
        mgr = SessionManager(storage.sessions)
        sess = await mgr.create()

        # CREATED -> IDLE is invalid
        with pytest.raises(LifecycleError) as exc_info:
            await mgr.mark_idle(sess.session_id)
        assert exc_info.value.code == "INVALID_SESSION_TRANSITION"

        await mgr.close(sess.session_id)

        with pytest.raises(LifecycleError):
            await mgr.activate(sess.session_id)
        with pytest.raises(LifecycleError):
            await mgr.close(sess.session_id)
        with pytest.raises(SessionError) as closed_err:
            await mgr.update_metadata(sess.session_id, {"a": 1})
        assert closed_err.value.code == "SESSION_CLOSED"
        with pytest.raises(SessionError):
            await mgr.rename(sess.session_id, "New Title")


@pytest.mark.asyncio
async def test_session_list_filter_pagination_and_delete(tmp_path: Path) -> None:
    async with SQLiteStorage(database_path=tmp_path / "list.db") as storage:
        mgr = SessionManager(storage.sessions)
        s1 = await mgr.create(title="One", agent_id="a1")
        s2 = await mgr.create(title="Two", agent_id="a1")
        s3 = await mgr.create(title="Three", agent_id="a2")
        await mgr.activate(s2.session_id)

        all_sessions = await mgr.list(limit=10)
        assert len(all_sessions) == 3

        page1 = await mgr.list(limit=2, offset=0)
        page2 = await mgr.list(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 1
        assert {s.session_id for s in page1 + page2} == {
            s1.session_id,
            s2.session_id,
            s3.session_id,
        }

        active_only = await mgr.list(status=SessionState.ACTIVE)
        assert [s.session_id for s in active_only] == [s2.session_id]

        a1_only = await mgr.list(agent_id="a1")
        assert {s.session_id for s in a1_only} == {s1.session_id, s2.session_id}

        with pytest.raises(ValidationError):
            await mgr.list(limit=0)
        with pytest.raises(ValidationError):
            await mgr.list(offset=-1)

        assert await mgr.delete(s1.session_id) is True
        assert await mgr.delete(s1.session_id) is False
        assert await mgr.get(s1.session_id) is None
