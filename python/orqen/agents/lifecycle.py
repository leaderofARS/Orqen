"""Agent lifecycle state machine and validated transitions."""

from __future__ import annotations

from enum import StrEnum

from orqen.exceptions import LifecycleError


class AgentState(StrEnum):
    """Explicit lifecycle states for an `Agent` instance."""

    CREATED = "CREATED"
    INITIALIZED = "INITIALIZED"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TERMINATED = "TERMINATED"


VALID_AGENT_TRANSITIONS: dict[AgentState, frozenset[AgentState]] = {
    AgentState.CREATED: frozenset({AgentState.INITIALIZED, AgentState.TERMINATED}),
    AgentState.INITIALIZED: frozenset({AgentState.READY, AgentState.TERMINATED}),
    AgentState.READY: frozenset({AgentState.RUNNING, AgentState.TERMINATED}),
    AgentState.RUNNING: frozenset(
        {
            AgentState.COMPLETED,
            AgentState.FAILED,
            AgentState.CANCELLED,
            AgentState.TERMINATED,
        }
    ),
    AgentState.COMPLETED: frozenset({AgentState.READY, AgentState.TERMINATED}),
    AgentState.FAILED: frozenset({AgentState.READY, AgentState.TERMINATED}),
    AgentState.CANCELLED: frozenset({AgentState.READY, AgentState.TERMINATED}),
    AgentState.TERMINATED: frozenset(),
}


def validate_agent_transition(current: AgentState, target: AgentState) -> AgentState:
    """Validate and return `target` if the transition is permitted."""
    allowed = VALID_AGENT_TRANSITIONS[current]
    if target not in allowed:
        raise LifecycleError(
            f"Invalid agent state transition: {current.value} -> {target.value}.",
            code="INVALID_AGENT_TRANSITION",
            details={"current_state": current.value, "target_state": target.value},
        )
    return target
