"""Task lifecycle state machine and validated transitions."""

from __future__ import annotations

from enum import StrEnum

from orqen.exceptions import LifecycleError


class TaskState(StrEnum):
    """Explicit lifecycle states for a persisted `Task`."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


TERMINAL_TASK_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.TIMED_OUT,
    }
)

VALID_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.TIMED_OUT,
        }
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
    TaskState.TIMED_OUT: frozenset(),
}


def validate_task_transition(current: TaskState, target: TaskState) -> TaskState:
    """Validate and return `target` if the task transition is permitted."""
    if target not in VALID_TASK_TRANSITIONS[current]:
        raise LifecycleError(
            f"Invalid task state transition: {current.value} -> {target.value}.",
            code="INVALID_TASK_TRANSITION",
            details={"current_state": current.value, "target_state": target.value},
        )
    return target
