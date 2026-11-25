"""Bounded async event stream for single-agent executions."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from orqen.exceptions import LifecycleError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from orqen.events.types import RuntimeEvent
    from orqen.runtime.result import RunResult

_STREAM_SENTINEL = object()


class RunStream:
    """Async iterator of `RuntimeEvent`s backed by a bounded queue.

    Usage:
        ```python
        stream = runtime.stream(agent, input="Hello")
        async for event in stream:
            ...
        result = stream.result
        ```
    """

    def __init__(self, *, run_id: str, max_queue_size: int = 256) -> None:
        self.run_id = run_id
        self._queue: asyncio.Queue[RuntimeEvent | object] = asyncio.Queue(maxsize=max_queue_size)
        self._task: asyncio.Task[RunResult] | None = None
        self._result: RunResult | None = None
        self._error: BaseException | None = None
        self._finished = False

    def _attach_task(self, task: asyncio.Task[RunResult]) -> None:
        self._task = task

    async def _emit(self, event: RuntimeEvent) -> None:
        await self._queue.put(event)

    async def _complete(self, result: RunResult) -> None:
        self._result = result
        self._finished = True
        await self._queue.put(_STREAM_SENTINEL)

    async def _fail(self, exc: BaseException) -> None:
        self._error = exc
        self._finished = True
        await self._queue.put(_STREAM_SENTINEL)

    @property
    def result(self) -> RunResult:
        """Return the final `RunResult` once streaming has completed.

        Raises `LifecycleError` if the stream has not finished yet, or re-raises
        the terminal execution error if the run failed or was cancelled.
        """
        if not self._finished:
            raise LifecycleError(
                "RunStream has not completed yet; exhaust the stream before reading result.",
                code="STREAM_NOT_FINISHED",
                details={"run_id": self.run_id},
            )
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result

    async def aclose(self) -> None:
        """Cancel the underlying run task if still active."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task

    def __aiter__(self) -> AsyncIterator[RuntimeEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[RuntimeEvent]:
        try:
            while True:
                item = await self._queue.get()
                if item is _STREAM_SENTINEL:
                    break
                assert not isinstance(item, object) or hasattr(item, "event_type")
                yield item  # type: ignore[misc]
            if self._error is not None:
                if self._task is not None and self._task.done() and not self._task.cancelled():
                    self._task.exception()
                raise self._error
        except asyncio.CancelledError:
            await self.aclose()
            raise
