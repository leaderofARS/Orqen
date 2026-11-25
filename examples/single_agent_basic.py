"""Basic single-agent execution example using MockModel and @tool."""

from __future__ import annotations

import asyncio

from orqen import Agent, Runtime
from orqen.models import MockModel, ToolCall
from orqen.tools import tool


@tool
async def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


async def main() -> None:
    model = MockModel(
        responses=[
            {"tool_calls": [ToolCall(id="call_1", name="add", arguments={"a": 19, "b": 23})]},
            {"text": "The answer is 42."},
        ]
    )
    agent = Agent(
        name="assistant",
        model=model,
        instructions="Be precise.",
        tools=[add],
    )

    async with Runtime(max_concurrent_runs=10) as runtime:
        result = await runtime.run(agent, input="What is 19 + 23?")
        print(f"Status: {result.status}")
        print(f"Output: {result.output}")
        print(f"Calls: model={result.model_calls}, tool={result.tool_calls}")


if __name__ == "__main__":
    asyncio.run(main())
