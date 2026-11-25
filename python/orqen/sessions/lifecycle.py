"""Session lifecycle state machine and validated transitions."""

from __future__ import annotations

from enum import StrEnum

from orqen.exceptions import LifecycleError


class SessionState(StrEnum):
    """Explicit lifecycle states for a persistent `Session`."""

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    CLOSED = "CLOSED"


VALID_SESSION_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset({SessionState.ACTIVE, SessionState.CLOSED}),
    SessionState.ACTIVE: frozenset({SessionState.IDLE, SessionState.CLOSED}),
    SessionState.IDLE: frozenset({SessionState.ACTIVE, SessionState.CLOSED}),
    SessionState.CLOSED: frozenset(),
}


def validate_session_transition(current: SessionState, target: SessionState) -> SessionState:
    """Validate and return `target` if the session transition is permitted."""
    if target not in VALID_SESSION_TRANSITIONS[current]:
        raise LifecycleError(
            f"Invalid session state transition: {current.value} -> {target.value}.",
            code="INVALID_SESSION_TRANSITION",
            details={"current_state": current.value, "target_state": target.value},
        )
    return target
