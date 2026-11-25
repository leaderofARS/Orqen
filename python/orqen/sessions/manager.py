"""Session lifecycle management and inspection service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from orqen.exceptions import SessionError, StorageError, ValidationError
from orqen.sessions.lifecycle import SessionState, validate_session_transition
from orqen.sessions.models import Session
from orqen.telemetry.logging import get_logger

if TYPE_CHECKING:
    from orqen.artifacts.models import ArtifactMetadata
    from orqen.events.models import PersistedEvent
    from orqen.storage.repositories import (
        ArtifactRepository,
        EventRepository,
        SessionRepository,
        TaskRepository,
    )
    from orqen.tasks.lifecycle import TaskState
    from orqen.tasks.models import Task

_logger = get_logger("sessions")


def _validate_pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > 1000:
        raise ValidationError(
            f"Pagination limit must be between 1 and 1000, got {limit}.",
            code="INVALID_PAGINATION",
        )
    if offset < 0:
        raise ValidationError(
            f"Pagination offset must be >= 0, got {offset}.",
            code="INVALID_PAGINATION",
        )


class SessionManager:
    """Domain service for creating, transitioning, and querying durable `Session`s."""

    def __init__(
        self,
        sessions: SessionRepository,
        *,
        tasks: TaskRepository | None = None,
        events: EventRepository | None = None,
        artifacts: ArtifactRepository | None = None,
    ) -> None:
        self._sessions = sessions
        self._tasks = tasks
        self._events = events
        self._artifacts = artifacts

    async def create(
        self,
        *,
        title: str | None = None,
        agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        """Create and persist a new `Session` in `CREATED` state."""
        kwargs: dict[str, Any] = {
            "title": title,
            "agent_id": agent_id,
            "metadata": dict(metadata) if metadata is not None else {},
        }
        if session_id is not None:
            kwargs["session_id"] = session_id
        session = Session(**kwargs)
        created = await self._sessions.create(session)
        _logger.info("Session created", extra={"session_id": created.session_id})
        return created

    async def get(self, session_id: str) -> Session | None:
        """Retrieve a `Session` by ID, or `None` if not found."""
        return await self._sessions.get(session_id)

    async def require(self, session_id: str) -> Session:
        """Retrieve a `Session` by ID or raise `SessionError` if missing."""
        session = await self._sessions.get(session_id)
        if session is None:
            raise SessionError(
                f"Session '{session_id}' not found.",
                code="SESSION_NOT_FOUND",
                details={"session_id": session_id},
            )
        return session

    async def require_open(self, session_id: str) -> Session:
        """Retrieve a `Session` and verify it is not `CLOSED`."""
        session = await self.require(session_id)
        if session.status == SessionState.CLOSED:
            raise SessionError(
                f"Session '{session_id}' is closed and immutable.",
                code="SESSION_CLOSED",
                details={"session_id": session_id},
            )
        return session

    async def update_metadata(
        self,
        session_id: str,
        metadata: dict[str, Any],
        *,
        merge: bool = True,
    ) -> Session:
        """Update session metadata (merged by default) and increment revision."""
        current = await self.require_open(session_id)
        new_metadata = {**current.metadata, **metadata} if merge else dict(metadata)
        updated = current.model_copy(
            update={
                "metadata": new_metadata,
                "revision": current.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        return await self._sessions.update(updated, expected_revision=current.revision)

    async def rename(self, session_id: str, title: str | None) -> Session:
        """Update a session's title and increment revision."""
        current = await self.require_open(session_id)
        updated = current.model_copy(
            update={
                "title": title,
                "revision": current.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        return await self._sessions.update(updated, expected_revision=current.revision)

    async def activate(self, session_id: str) -> Session:
        """Transition a session to `ACTIVE` (idempotent if already `ACTIVE`)."""
        current = await self.require(session_id)
        if current.status == SessionState.ACTIVE:
            return current
        next_state = validate_session_transition(current.status, SessionState.ACTIVE)
        updated = current.model_copy(
            update={
                "status": next_state,
                "revision": current.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        return await self._sessions.update(updated, expected_revision=current.revision)

    async def mark_idle(self, session_id: str) -> Session:
        """Transition an `ACTIVE` session to `IDLE`."""
        current = await self.require(session_id)
        next_state = validate_session_transition(current.status, SessionState.IDLE)
        updated = current.model_copy(
            update={
                "status": next_state,
                "revision": current.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        return await self._sessions.update(updated, expected_revision=current.revision)

    async def close(self, session_id: str) -> Session:
        """Transition a session to terminal `CLOSED` state."""
        current = await self.require(session_id)
        next_state = validate_session_transition(current.status, SessionState.CLOSED)
        updated = current.model_copy(
            update={
                "status": next_state,
                "revision": current.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        closed = await self._sessions.update(updated, expected_revision=current.revision)
        _logger.info("Session closed", extra={"session_id": session_id})
        return closed

    async def delete(self, session_id: str) -> bool:
        """Delete a session and its cascaded records."""
        return await self._sessions.delete(session_id)

    async def list(
        self,
        *,
        status: SessionState | None = None,
        agent_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List sessions with optional filtering and bounded pagination."""
        _validate_pagination(limit, offset)
        return await self._sessions.list(
            status=status,
            agent_id=agent_id,
            limit=limit,
            offset=offset,
        )

    async def get_tasks(
        self,
        session_id: str,
        *,
        status: TaskState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        """Retrieve tasks associated with `session_id`."""
        if self._tasks is None:
            raise StorageError(
                "TaskRepository is not configured on SessionManager.",
                code="REPOSITORY_NOT_CONFIGURED",
            )
        _validate_pagination(limit, offset)
        await self.require(session_id)
        return await self._tasks.list(
            session_id=session_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def get_events(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[PersistedEvent, ...]:
        """Retrieve ordered persisted events for `session_id`."""
        if self._events is None:
            raise StorageError(
                "EventRepository is not configured on SessionManager.",
                code="REPOSITORY_NOT_CONFIGURED",
            )
        _validate_pagination(limit, offset)
        await self.require(session_id)
        return await self._events.list(
            session_id=session_id,
            task_id=task_id,
            run_id=run_id,
            event_type=event_type,
            after_sequence=after_sequence,
            limit=limit,
            offset=offset,
        )

    async def get_artifacts(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ArtifactMetadata, ...]:
        """Retrieve artifact metadata records associated with `session_id`."""
        if self._artifacts is None:
            raise StorageError(
                "ArtifactRepository is not configured on SessionManager.",
                code="REPOSITORY_NOT_CONFIGURED",
            )
        _validate_pagination(limit, offset)
        await self.require(session_id)
        return await self._artifacts.list(
            session_id=session_id,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )
