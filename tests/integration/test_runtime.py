"""Integration tests for the ORQEN single-agent Runtime."""

import asyncio

import pytest

from orqen import (
    Agent,
    AgentConfig,
    AgentState,
    ExecutionLimitError,
    LifecycleError,
    OrqenTimeoutError,
    ProviderError,
    Runtime,
    RuntimeState,
    SecurityError,
    ToolError,
)
from orqen.events import (
    ModelRequestCompleted,
    ModelRequestStarted,
    ModelStreamChunk,
    RunCompleted,
    RunFailed,
    RunStarted,
    ToolCallRequested,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from orqen.models import MockModel, MockResponseSpec, ToolCall
from orqen.tools import DefaultPermissionPolicy, tool


@tool
async def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


@tool
async def failing_tool(x: str) -> str:
    """Always raises."""
    raise RuntimeError(f"boom: {x}")


@tool(dangerous=True)
async def delete_all() -> str:
    """Dangerous tool."""
    return "deleted"


@pytest.mark.asyncio
async def test_simple_text_run() -> None:
    model = MockModel(responses=[{"text": "The answer is 42."}])
    agent = Agent(
        name="assistant",
        model=model,
        instructions="Be precise.",
        tools=[add],
    )

    async with Runtime(max_concurrent_runs=5) as runtime:
        assert runtime.state == RuntimeState.RUNNING
        result = await runtime.run(agent, input="What is the answer?")

    assert runtime.state == RuntimeState.CLOSED
    assert result.status == "completed"
    assert result.output == "The answer is 42."
    assert result.model_calls == 1
    assert result.tool_calls == 0
    assert agent.state == AgentState.COMPLETED
    assert runtime.metrics.executions_succeeded == 1


@pytest.mark.asyncio
async def test_multi_step_and_multiple_tool_calls_ordered() -> None:
    call_order: list[str] = []

    @tool
    async def step_a(v: int) -> int:
        call_order.append("a")
        return v + 1

    @tool
    async def step_b(v: int) -> int:
        call_order.append("b")
        return v * 2

    model = MockModel(
        responses=[
            {
                "tool_calls": [
                    ToolCall(id="c1", name="step_a", arguments={"v": 2}),
                    ToolCall(id="c2", name="step_b", arguments={"v": 3}),
                ]
            },
            {"tool_calls": [ToolCall(id="c3", name="add", arguments={"a": 3, "b": 6})]},
            {"text": "Final: 9"},
        ]
    )
    agent = Agent(name="calc", model=model, tools=[step_a, step_b, add])

    async with Runtime() as runtime:
        result = await runtime.run(agent, input="Compute")

    assert result.output == "Final: 9"
    assert result.model_calls == 3
    assert result.tool_calls == 3
    assert call_order == ["a", "b"]


@pytest.mark.asyncio
async def test_tool_error_recovery_and_abort_modes() -> None:
    # Default fail_on_tool_error=False returns error ToolResultMessage to model
    model_recover = MockModel(
        responses=[
            {"tool_calls": [ToolCall(id="c1", name="failing_tool", arguments={"x": "1"})]},
            {"text": "Recovered from tool error."},
        ]
    )
    agent_recover = Agent(name="recover", model=model_recover, tools=[failing_tool])

    async with Runtime() as runtime:
        result = await runtime.run(agent_recover, input="Try")
    assert result.output == "Recovered from tool error."
    last_req = model_recover.recorded_requests[-1]
    assert last_req.messages[-1].role == "tool"
    assert last_req.messages[-1].is_error is True

    # fail_on_tool_error=True aborts immediately
    model_abort = MockModel(
        responses=[
            {"tool_calls": [ToolCall(id="c1", name="failing_tool", arguments={"x": "1"})]},
        ]
    )
    agent_abort = Agent(
        name="abort",
        model=model_abort,
        tools=[failing_tool],
        config=AgentConfig(fail_on_tool_error=True),
    )
    async with Runtime() as runtime:
        with pytest.raises(ToolError, match="boom: 1"):
            await runtime.run(agent_abort, input="Try")
    assert agent_abort.state == AgentState.FAILED


@pytest.mark.asyncio
async def test_permission_denial_blocks_tool_execution() -> None:
    model = MockModel(
        responses=[
            {"tool_calls": [ToolCall(id="c1", name="delete_all", arguments={})]},
        ]
    )
    agent = Agent(
        name="guarded",
        model=model,
        tools=[delete_all],
        permission_policy=DefaultPermissionPolicy(),
    )

    async with Runtime() as runtime:
        with pytest.raises(SecurityError, match="not allowlisted"):
            await runtime.run(agent, input="Delete everything")
    assert agent.state == AgentState.FAILED


@pytest.mark.asyncio
async def test_malformed_tool_arguments_handled_safely() -> None:
    model = MockModel(
        responses=[
            {"tool_calls": [ToolCall(id="c1", name="add", arguments={"a": "bad", "b": 2})]},
            {"text": "Corrected."},
        ]
    )
    agent = Agent(name="safe", model=model, tools=[add])

    async with Runtime() as runtime:
        result = await runtime.run(agent, input="Add")

    assert result.output == "Corrected."
    tool_msg = model.recorded_requests[-1].messages[-1]
    assert tool_msg.role == "tool"
    assert tool_msg.is_error is True
    assert "Invalid arguments" in tool_msg.content


@pytest.mark.asyncio
async def test_execution_limits_and_timeouts() -> None:
    # Max iterations exceeded
    model_loop = MockModel(
        responses=[{"tool_calls": [ToolCall(name="add", arguments={"a": 1, "b": 1})]}],
        cycle=True,
    )
    agent_iter = Agent(
        name="looper",
        model=model_loop,
        tools=[add],
        config=AgentConfig(max_iterations=2, max_tool_calls=10),
    )
    async with Runtime() as runtime:
        with pytest.raises(ExecutionLimitError, match="Maximum iterations"):
            await runtime.run(agent_iter, input="Loop")

    # Max tool calls exceeded
    agent_tc = Agent(
        name="tc-limit",
        model=model_loop,
        tools=[add],
        config=AgentConfig(max_iterations=10, max_tool_calls=1),
    )
    async with Runtime() as runtime:
        with pytest.raises(ExecutionLimitError, match="Maximum tool calls"):
            await runtime.run(agent_tc, input="Loop")

    # Model timeout
    slow_model = MockModel(responses=[MockResponseSpec(text="late", delay_seconds=1.0)])
    agent_timeout = Agent(
        name="slow",
        model=slow_model,
        config=AgentConfig(model_timeout_seconds=0.05),
    )
    async with Runtime() as runtime:
        with pytest.raises(OrqenTimeoutError, match="Model request timed out"):
            await runtime.run(agent_timeout, input="Wait")
    assert runtime.metrics.executions_timed_out == 1


@pytest.mark.asyncio
async def test_model_failure_propagates() -> None:
    model = MockModel(responses=[RuntimeError("500 internal")])
    agent = Agent(name="broken", model=model)
    async with Runtime() as runtime:
        with pytest.raises(ProviderError, match="500 internal"):
            await runtime.run(agent, input="Hi")
    assert agent.state == AgentState.FAILED


@pytest.mark.asyncio
async def test_streaming_completion_and_failure() -> None:
    model = MockModel(
        responses=[
            MockResponseSpec(
                tool_calls=(ToolCall(id="c1", name="multiply", arguments={"a": 3, "b": 4}),)
            ),
            MockResponseSpec(text="Result is 12", chunks=("Result ", "is 12")),
        ]
    )
    agent = Agent(name="streamer", model=model, tools=[multiply])

    async with Runtime() as runtime:
        stream = runtime.stream(agent, input="3*4?")
        events = [event async for event in stream]
        result = stream.result

    assert result.output == "Result is 12"
    event_types = [type(e) for e in events]
    assert event_types[0] is RunStarted
    assert ModelRequestStarted in event_types
    assert ToolCallRequested in event_types
    assert ToolExecutionStarted in event_types
    assert ToolExecutionCompleted in event_types
    assert ModelStreamChunk in event_types
    assert ModelRequestCompleted in event_types
    assert event_types[-1] is RunCompleted

    # Streaming failure emits RunFailed and re-raises on iteration
    failing_agent = Agent(
        name="stream-fail",
        model=MockModel(responses=[RuntimeError("stream broke")]),
    )
    async with Runtime() as runtime:
        bad_stream = runtime.stream(failing_agent, input="fail")
        collected = []
        with pytest.raises(ProviderError, match="stream broke"):
            async for ev in bad_stream:
                collected.append(ev)
        assert any(isinstance(e, RunFailed) for e in collected)


@pytest.mark.asyncio
async def test_bounded_concurrency_and_failure_isolation() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class GatedModel(MockModel):
        async def generate(self, request):
            entered.set()
            await release.wait()
            return await super().generate(request)

    a1 = Agent(name="a1", model=GatedModel(responses=["done-1"]))
    a2 = Agent(name="a2", model=MockModel(responses=["done-2"]))

    # max_concurrent_runs=1 with fail-fast acquire_timeout_seconds=0
    async with Runtime(max_concurrent_runs=1, acquire_timeout_seconds=0) as runtime:
        t1 = asyncio.create_task(runtime.run(a1, input="1"))
        await entered.wait()

        with pytest.raises(OrqenTimeoutError, match="concurrency limit reached"):
            await runtime.run(a2, input="2")

        release.set()
        r1 = await t1
        assert r1.output == "done-1"

        # Now slot is free, a2 succeeds
        r2 = await runtime.run(a2, input="2")
        assert r2.output == "done-2"
        assert runtime.metrics.peak_concurrent_runs == 1


@pytest.mark.asyncio
async def test_concurrent_same_agent_rejected_and_cancellation_cleanup() -> None:
    started = asyncio.Event()
    gate = asyncio.Event()

    class BlockingModel(MockModel):
        async def generate(self, request):
            started.set()
            await gate.wait()
            return await super().generate(request)

    agent = Agent(name="shared", model=BlockingModel(responses=["ok"]))

    async with Runtime(max_concurrent_runs=4) as runtime:
        t1 = asyncio.create_task(runtime.run(agent, input="run-1"))
        await started.wait()

        with pytest.raises(LifecycleError, match="already running"):
            await runtime.run(agent, input="run-2")

        t1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t1

    assert agent.state == AgentState.CANCELLED


@pytest.mark.asyncio
async def test_runtime_lifecycle_errors_and_shutdown_cancellation() -> None:
    agent = Agent(name="idle", model=MockModel(responses=["ok"]))
    uninit = Runtime()
    with pytest.raises(LifecycleError, match="not running"):
        await uninit.run(agent, input="hi")

    started = asyncio.Event()
    never = asyncio.Event()

    class HangModel(MockModel):
        async def generate(self, request):
            started.set()
            await never.wait()
            return await super().generate(request)

    hanging_agent = Agent(name="hanger", model=HangModel(responses=["ok"]))
    rt = Runtime()
    await rt.initialize()
    task = asyncio.create_task(rt.run(hanging_agent, input="hang"))
    await started.wait()
    await rt.close()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert rt.state == RuntimeState.CLOSED
    assert hanging_agent.state == AgentState.CANCELLED

    with pytest.raises(LifecycleError, match="not running"):
        await rt.run(agent, input="after close")
