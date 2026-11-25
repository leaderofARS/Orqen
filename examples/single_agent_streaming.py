"""Streaming single-agent execution example using MockModel."""

from __future__ import annotations

import asyncio

from orqen import Agent, Runtime
from orqen.events import ModelStreamChunk
from orqen.models import MockModel, MockResponseSpec


async def main() -> None:
    model = MockModel(
        responses=[
            MockResponseSpec(
                text="ORQEN streams typed runtime events.",
                chunks=("ORQEN ", "streams ", "typed ", "runtime ", "events."),
            )
        ]
    )
    agent = Agent(name="streamer", model=model)

    async with Runtime() as runtime:
        stream = runtime.stream(agent, input="Describe streaming.")
        async for event in stream:
            if isinstance(event, ModelStreamChunk):
                print(event.text_delta, end="", flush=True)
        print()
        result = stream.result
        print(f"Completed run {result.run_id} in {result.duration_seconds:.4f}s")


if __name__ == "__main__":
    asyncio.run(main())
