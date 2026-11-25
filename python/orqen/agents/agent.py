"""Agent definition and lifecycle management."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from orqen.agents.config import AgentConfig
from orqen.agents.lifecycle import AgentState, validate_agent_transition
from orqen.exceptions import LifecycleError, ValidationError
from orqen.tools.permissions import DefaultPermissionPolicy, PermissionPolicy
from orqen.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from orqen.models.base import Model
    from orqen.tools.types import Tool


class Agent:
    """Declarative AI agent definition with an explicit lifecycle state machine.

    An `Agent` is reusable sequentially across runs: after reaching `COMPLETED`,
    `FAILED`, or `CANCELLED`, it may be reset back to `READY` (automatically by
    `Runtime` before a new run, or explicitly via `agent.reset()`).

    Concurrent execution of the *same* `Agent` instance is not permitted and raises
    `LifecycleError`; create separate `Agent` instances for concurrent runs.
    """

    def __init__(
        self,
        *,
        name: str,
        model: Model,
        instructions: str = "",
        tools: Sequence[Tool] | ToolRegistry = (),
        config: AgentConfig | None = None,
        permission_policy: PermissionPolicy | None = None,
        metadata: dict[str, Any] | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._state = AgentState.CREATED
        if not name or not name.strip():
            raise ValidationError("Agent name must be a non-empty string.")

        self.agent_id: str = agent_id or f"agt_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.model = model
        self.instructions = instructions
        self.tools: ToolRegistry = tools if isinstance(tools, ToolRegistry) else ToolRegistry(tools)
        self.config: AgentConfig = config or AgentConfig()
        self.permission_policy: PermissionPolicy = permission_policy or DefaultPermissionPolicy()
        self.metadata: dict[str, Any] = dict(metadata) if metadata is not None else {}

        self.transition_to(AgentState.INITIALIZED)
        self.transition_to(AgentState.READY)

    @property
    def state(self) -> AgentState:
        """Current lifecycle state of this agent."""
        return self._state

    def transition_to(self, target: AgentState) -> AgentState:
        """Transition the agent to `target` after validating the transition."""
        self._state = validate_agent_transition(self._state, target)
        return self._state

    def reset(self) -> None:
        """Reset a finished (`COMPLETED`, `FAILED`, `CANCELLED`) agent to `READY`."""
        if self._state == AgentState.READY:
            return
        if self._state in (
            AgentState.COMPLETED,
            AgentState.FAILED,
            AgentState.CANCELLED,
        ):
            self.transition_to(AgentState.READY)
            return
        raise LifecycleError(
            f"Cannot reset agent '{self.name}' from state {self._state.value}.",
            code="INVALID_AGENT_RESET",
            details={"agent": self.name, "state": self._state.value},
        )

    def terminate(self) -> None:
        """Permanently transition the agent to `TERMINATED`."""
        if self._state != AgentState.TERMINATED:
            self.transition_to(AgentState.TERMINATED)
