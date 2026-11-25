"""Task lifecycle management and execution history queries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from orqen.exceptions import OrqenError, SessionError, StorageError, TaskError, ValidationError
from orqen.sessions.lifecycle import SessionState
from orqen.tasks.lifecycle import TaskState, validate_task_transition
from orqen.tasks.models import Task
from orqen.telemetry.logging import _DEFAULT_REDACTED_KEYS, _redact_value, get_logger

if TYPE_CHECKING:
    from orqen.events.models import PersistedEvent
    from orqen.storage.repositories import EventRepository, SessionRepository, TaskRepository

_logger = get_logger("tasks")


def _sanitize_error(error: BaseException | dict[str, Any]) -> dict[str, Any]:
    """Convert an exception or error mapping into a secret-redacted dict."""
    if isinstance(error, OrqenError):
        raw = error.to_dict()
    elif isinstance(error, BaseException):
        raw = {
            "error_type": type(error).__name__,
            "code": "EXECUTION_ERROR",
            "message": str(error),
        }
    else:
        raw = dict(error)
    sanitized = _redact_value(raw, _DEFAULT_REDACTED_KEYS)
    return dict(sanitized)


def _compute_duration(started_at: datetime | None, completed_at: datetime) -> float | None:
    if started_at is None:
        return None
    delta = (completed_at - started_at).total_seconds()
    return max(0.0, delta)


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


class TaskManager:
    """Domain service for creating, transitioning, and querying durable `Task`s."""

    def __init__(
        self,
        tasks: TaskRepository,
        *,
        sessions: SessionRepository | None = None,
        events: EventRepository | None = None,
    ) -> None:
        self._tasks = tasks
        self._sessions = sessions
        self._events = events

    async def create(
        self,
        *,
        session_id: str,
        input: str,
        agent_id: str | None = None,
        agent_name: str = "",
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> Task:
        """Create and persist a new `Task` in `PENDING` state."""
        if self._sessions is not None:
            session = await self._sessions.get(session_id)
            if session is None:
                raise SessionError(
                    f"Session '{session_id}' not found.",
                    code="SESSION_NOT_FOUND",
                    details={"session_id": session_id},
                )
            if session.status == SessionState.CLOSED:
                raise SessionError(
                    f"Cannot create task in closed session '{session_id}'.",
                    code="SESSION_CLOSED",
                    details={"session_id": session_id},
                )

        kwargs: dict[str, Any] = {
            "session_id": session_id,
            "input": input,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "run_id": run_id,
            "metadata": dict(metadata) if metadata is not None else {},
        }
        if task_id is not None:
            kwargs["task_id"] = task_id

        task = Task(**kwargs)
        created = await self._tasks.create(task)
        _logger.info(
            "Task created",
            extra={"task_id": created.task_id, "session_id": session_id},
        )
        return created

    async def get(self, task_id: str) -> Task | None:
        """Retrieve a `Task` by ID, or `None` if not found."""
        return await self._tasks.get(task_id)

    async def require(self, task_id: str) -> Task:
        """Retrieve a `Task` by ID or raise `TaskError` if missing."""
        task = await self._tasks.get(task_id)
        if task is None:
            raise TaskError(
                f"Task '{task_id}' not found.",
                code="TASK_NOT_FOUND",
                details={"task_id": task_id},
            )
        return task

    async def start(self, task_id: str, *, run_id: str | None = None) -> Task:
        """Transition a `PENDING` task to `RUNNING`."""
        current = await self.require(task_id)
        next_state = validate_task_transition(current.status, TaskState.RUNNING)
        now = datetime.now(UTC)
        updated = current.model_copy(
            update={
                "status": next_state,
                "run_id": run_id if run_id is not None else current.run_id,
                "started_at": now,
                "updated_at": now,
                "revision": current.revision + 1,
            }
        )
        return await self._tasks.update(updated, expected_revision=current.revision)

    async def complete(
        self,
        task_id: str,
        *,
        output: str,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Transition a `RUNNING` task to `COMPLETED` and persist its output."""
        current = await self.require(task_id)
        next_state = validate_task_transition(current.status, TaskState.COMPLETED)
        now = datetime.now(UTC)
        new_meta = {**current.metadata, **metadata} if metadata is not None else current.metadata
        updated = current.model_copy(
            update={
                "status": next_state,
                "output": output,
                "metadata": new_meta,
                "completed_at": now,
                "updated_at": now,
                "duration_seconds": _compute_duration(current.started_at, now),
                "revision": current.revision + 1,
            }
        )
        return await self._tasks.update(updated, expected_revision=current.revision)

    async def fail(
        self,
        task_id: str,
        *,
        error: BaseException | dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Transition a `RUNNING` task to `FAILED` with sanitized error details."""
        current = await self.require(task_id)
        next_state = validate_task_transition(current.status, TaskState.FAILED)
        now = datetime.now(UTC)
        new_meta = {**current.metadata, **metadata} if metadata is not None else current.metadata
        updated = current.model_copy(
            update={
                "status": next_state,
                "error": _sanitize_error(error),
                "metadata": new_meta,
                "completed_at": now,
                "updated_at": now,
                "duration_seconds": _compute_duration(current.started_at, now),
                "revision": current.revision + 1,
            }
        )
        return await self._tasks.update(updated, expected_revision=current.revision)

    async def cancel(self, task_id: str, *, reason: str | None = None) -> Task:
        """Transition a `PENDING` or `RUNNING` task to `CANCELLED`."""
        current = await self.require(task_id)
        next_state = validate_task_transition(current.status, TaskState.CANCELLED)
        now = datetime.now(UTC)
        error_payload = (
            _sanitize_error({"error_type": "CancellationError", "message": reason})
            if reason
            else current.error
        )
        updated = current.model_copy(
            update={
                "status": next_state,
                "error": error_payload,
                "completed_at": now,
                "updated_at": now,
                "duration_seconds": _compute_duration(current.started_at, now),
                "revision": current.revision + 1,
            }
        )
        return await self._tasks.update(updated, expected_revision=current.revision)

    async def timeout(
        self,
        task_id: str,
        *,
        error: BaseException | dict[str, Any] | None = None,
    ) -> Task:
        """Transition a `RUNNING` task to `TIMED_OUT`."""
        current = await self.require(task_id)
        next_state = validate_task_transition(current.status, TaskState.TIMED_OUT)
        now = datetime.now(UTC)
        sanitized = (
            _sanitize_error(error)
            if error is not None
            else {
                "error_type": "OrqenTimeoutError",
                "code": "TIMEOUT_ERROR",
                "message": "Timed out",
            }
        )
        updated = current.model_copy(
            update={
                "status": next_state,
                "error": sanitized,
                "completed_at": now,
                "updated_at": now,
                "duration_seconds": _compute_duration(current.started_at, now),
                "revision": current.revision + 1,
            }
        )
        return await self._tasks.update(updated, expected_revision=current.revision)

    async def list(
        self,
        *,
        session_id: str | None = None,
        status: TaskState | None = None,
        agent_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        """List tasks with optional filtering and bounded pagination."""
        _validate_pagination(limit, offset)
        return await self._tasks.list(
            session_id=session_id,
            status=status,
            agent_name=agent_name,
            limit=limit,
            offset=offset,
        )

    async def count(
        self,
        *,
        session_id: str | None = None,
        status: TaskState | None = None,
    ) -> int:
        """Count tasks matching the optional filters."""
        return await self._tasks.count(session_id=session_id, status=status)

    async def get_events(
        self,
        task_id: str,
        *,
        after_sequence: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[PersistedEvent, ...]:
        """Retrieve ordered persisted events for `task_id`."""
        if self._events is None:
            raise StorageError(
                "EventRepository is not configured on TaskManager.",
                code="REPOSITORY_NOT_CONFIGURED",
            )
        _validate_pagination(limit, offset)
        await self.require(task_id)
        return await self._events.list(
            task_id=task_id,
            after_sequence=after_sequence,
            limit=limit,
            offset=offset,
        )
