"""Permission interfaces and conservative default policy for tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict


class PermissionContext(BaseModel):
    """Context evaluated when authorizing a tool invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_name: str
    run_id: str
    tool_name: str
    dangerous: bool = False
    permissions: tuple[str, ...] = ()
    arguments: dict[str, Any] = {}


class PermissionDecision(BaseModel):
    """Outcome of a permission check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    reason: str = ""


class PermissionPolicy(ABC):
    """Abstract authorization policy evaluated before every tool call."""

    @abstractmethod
    def check(self, context: PermissionContext) -> PermissionDecision:
        """Evaluate whether the requested tool invocation is permitted."""


class DefaultPermissionPolicy(PermissionPolicy):
    """Conservative default permission policy.

    - Explicitly denied tools are always rejected.
    - Dangerous tools are rejected unless explicitly listed in `allowed_tools`.
    - Tools requiring named permissions are rejected unless all required permissions
      are in `allowed_permissions` or the tool is in `allowed_tools`.
    - Safe tools (`dangerous=False`, `permissions=()`) are allowed when `allow_safe=True`.
    """

    def __init__(
        self,
        *,
        allow_safe: bool = True,
        allowed_tools: frozenset[str] | set[str] = frozenset(),
        denied_tools: frozenset[str] | set[str] = frozenset(),
        allowed_permissions: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        self.allow_safe = allow_safe
        self.allowed_tools = frozenset(allowed_tools)
        self.denied_tools = frozenset(denied_tools)
        self.allowed_permissions = frozenset(allowed_permissions)

    def check(self, context: PermissionContext) -> PermissionDecision:
        if context.tool_name in self.denied_tools:
            return PermissionDecision(
                allowed=False,
                reason=f"Tool '{context.tool_name}' is explicitly denied by policy.",
            )

        if context.tool_name in self.allowed_tools:
            return PermissionDecision(allowed=True, reason="Tool explicitly allowlisted.")

        if context.dangerous:
            return PermissionDecision(
                allowed=False,
                reason=f"Dangerous tool '{context.tool_name}' is not allowlisted.",
            )

        missing = [p for p in context.permissions if p not in self.allowed_permissions]
        if missing:
            return PermissionDecision(
                allowed=False,
                reason=(
                    f"Tool '{context.tool_name}' requires unapproved permissions: "
                    f"{', '.join(missing)}."
                ),
            )

        if not self.allow_safe:
            return PermissionDecision(
                allowed=False,
                reason=f"Tool '{context.tool_name}' is not in the strict allowlist.",
            )

        return PermissionDecision(allowed=True, reason="Safe tool permitted by default policy.")
