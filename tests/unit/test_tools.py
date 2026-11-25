"""Unit tests for ORQEN tools, decorator, registry, validation, and permissions."""

import asyncio
import time

import pytest

from orqen.exceptions import OrqenTimeoutError, ToolError, ToolValidationError
from orqen.tools import (
    DefaultPermissionPolicy,
    PermissionContext,
    ToolRegistry,
    tool,
)


@tool
async def async_add(a: int, b: int = 1) -> int:
    """Add two integers."""
    return a + b


@tool(name="sync_upper", description="Uppercase text.")
def sync_upper(text: str) -> str:
    return text.upper()


@tool(dangerous=True, permissions=("fs:write",))
def write_file_tool(path: str) -> str:
    """Write to disk."""
    return f"wrote {path}"


@pytest.mark.asyncio
async def test_tool_decorator_and_invocation() -> None:
    assert async_add.name == "async_add"
    assert async_add.mode == "async"
    assert sync_upper.name == "sync_upper"
    assert sync_upper.mode == "sync"

    schema = async_add.to_schema()
    assert schema.name == "async_add"
    assert "a" in schema.parameters["properties"]

    assert await async_add.invoke({"a": 2}) == "3"
    assert await sync_upper.invoke({"text": "orqen"}) == "ORQEN"


@pytest.mark.asyncio
async def test_tool_validation_rejects_invalid_args() -> None:
    with pytest.raises(ToolValidationError, match="Invalid arguments"):
        await async_add.invoke({"a": "not-an-int"})

    with pytest.raises(ToolValidationError, match="Invalid arguments"):
        await async_add.invoke({"a": 1, "unknown": 2})


@pytest.mark.asyncio
async def test_sync_tool_does_not_block_event_loop() -> None:
    @tool
    def blocking_sleep(seconds: float) -> str:
        time.sleep(seconds)
        return "done"

    tick_count = 0

    async def ticker() -> None:
        nonlocal tick_count
        for _ in range(3):
            await asyncio.sleep(0.01)
            tick_count += 1

    res, _ = await asyncio.gather(
        blocking_sleep.invoke({"seconds": 0.05}),
        ticker(),
    )
    assert res == "done"
    assert tick_count >= 1


@pytest.mark.asyncio
async def test_tool_timeouts() -> None:
    @tool(timeout_seconds=0.05)
    async def hanging_async() -> str:
        await asyncio.sleep(5.0)
        return "never"

    @tool(timeout_seconds=0.05)
    def hanging_sync() -> str:
        time.sleep(0.2)
        return "never"

    with pytest.raises(OrqenTimeoutError, match="timed out"):
        await hanging_async.invoke({})

    with pytest.raises(OrqenTimeoutError, match="timed out"):
        await hanging_sync.invoke({})


def test_tool_registry_uniqueness() -> None:
    registry = ToolRegistry([async_add])
    assert registry.has("async_add")
    assert len(registry) == 1

    with pytest.raises(ToolError, match="already registered"):
        registry.register(async_add)

    registry.register(async_add, replace=True)
    removed = registry.unregister("async_add")
    assert removed is async_add
    assert len(registry) == 0


def test_default_permission_policy() -> None:
    policy = DefaultPermissionPolicy()
    safe_ctx = PermissionContext(
        agent_name="a", run_id="r1", tool_name="async_add", dangerous=False
    )
    assert policy.check(safe_ctx).allowed is True

    danger_ctx = PermissionContext(
        agent_name="a",
        run_id="r1",
        tool_name="write_file_tool",
        dangerous=True,
        permissions=("fs:write",),
    )
    assert policy.check(danger_ctx).allowed is False

    allowlisted = DefaultPermissionPolicy(allowed_tools={"write_file_tool"})
    assert allowlisted.check(danger_ctx).allowed is True
