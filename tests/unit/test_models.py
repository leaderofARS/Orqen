"""Unit tests for ORQEN model contracts, capabilities, and MockModel."""

import asyncio

import pytest

from orqen.exceptions import ModelError, ProviderError
from orqen.models import (
    MockModel,
    MockResponseSpec,
    ModelCapabilities,
    ModelRequest,
    ToolSchema,
    UserMessage,
)


@pytest.mark.asyncio
async def test_mock_model_text_and_usage() -> None:
    model = MockModel(responses=[{"text": "Hello world"}])
    req = ModelRequest(messages=(UserMessage(content="Hi"),))
    resp = await model.generate(req)

    assert resp.text == "Hello world"
    assert resp.finish_reason == "stop"
    assert resp.usage.total_tokens == 15
    assert len(model.recorded_requests) == 1


@pytest.mark.asyncio
async def test_capability_validation() -> None:
    limited = MockModel(
        responses=["ok"],
        capabilities=ModelCapabilities(streaming=False, tool_calling=False),
    )
    with pytest.raises(ModelError, match="does not support streaming"):
        await limited.generate(ModelRequest(messages=(UserMessage(content="hi"),), stream=True))

    with pytest.raises(ModelError, match="does not support tool calling"):
        await limited.generate(
            ModelRequest(
                messages=(UserMessage(content="hi"),),
                tools=(ToolSchema(name="t", description="d", parameters={}),),
            )
        )


@pytest.mark.asyncio
async def test_mock_model_streaming_and_errors() -> None:
    model = MockModel(
        responses=[
            MockResponseSpec(text="alpha beta", chunks=("alpha ", "beta")),
            MockResponseSpec(malformed=True),
            RuntimeError("upstream 503"),
        ]
    )
    req = ModelRequest(messages=(UserMessage(content="stream"),), stream=True)
    deltas = [d async for d in model.stream(req)]
    assert [d.text_delta for d in deltas[:-1]] == ["alpha ", "beta"]
    assert deltas[-1].is_final is True
    assert deltas[-1].response is not None
    assert deltas[-1].response.text == "alpha beta"

    with pytest.raises(ModelError, match="malformed"):
        await model.generate(ModelRequest(messages=(UserMessage(content="x"),)))

    with pytest.raises(ProviderError, match="upstream 503"):
        await model.generate(ModelRequest(messages=(UserMessage(content="y"),)))

    with pytest.raises(ModelError, match="exhausted"):
        await model.generate(ModelRequest(messages=(UserMessage(content="z"),)))


@pytest.mark.asyncio
async def test_mock_model_delay_cancellation() -> None:
    model = MockModel(responses=[MockResponseSpec(text="slow", delay_seconds=5.0)])
    task = asyncio.create_task(model.generate(ModelRequest(messages=(UserMessage(content="hi"),))))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
