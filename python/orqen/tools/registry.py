"""Tool registry enforcing unique names and schema lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orqen.exceptions import ToolError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from orqen.models.types import ToolSchema
    from orqen.tools.types import Tool


class ToolRegistry:
    """In-memory registry of named tools with duplicate protection."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for item in tools:
            self.register(item)

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        """Register a tool. Raises `ToolError` on duplicate name unless `replace=True`."""
        if tool.name in self._tools and not replace:
            raise ToolError(
                f"Tool '{tool.name}' is already registered.",
                code="DUPLICATE_TOOL",
                details={"tool": tool.name},
            )
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> Tool:
        """Remove and return a registered tool."""
        if name not in self._tools:
            raise ToolError(
                f"Tool '{name}' is not registered.",
                code="TOOL_NOT_FOUND",
                details={"tool": name},
            )
        return self._tools.pop(name)

    def get(self, name: str) -> Tool:
        """Retrieve a registered tool by name."""
        if name not in self._tools:
            raise ToolError(
                f"Tool '{name}' is not registered.",
                code="TOOL_NOT_FOUND",
                details={"tool": name},
            )
        return self._tools[name]

    def has(self, name: str) -> bool:
        """Return True if a tool with `name` is registered."""
        return name in self._tools

    def list_tools(self) -> tuple[Tool, ...]:
        """Return all registered tools in insertion order."""
        return tuple(self._tools.values())

    def schemas(self) -> tuple[ToolSchema, ...]:
        """Return provider-neutral JSON schemas for all registered tools."""
        return tuple(tool.to_schema() for tool in self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools
