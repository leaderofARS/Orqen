"""Async-first single-agent execution runtime with bounded concurrency."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from orqen.agents.lifecycle import AgentState
from orqen.config.settings import OrqenConfig
from orqen.events.types import (
    ModelRequestCompleted,
    ModelRequestStarted,
    ModelStreamChunk,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    ToolCallRequested,
    ToolExecutionCompleted,
    ToolExecutionFailed,
    ToolExecutionStarted,
)
from orqen.exceptions import (
    ExecutionError,
    ExecutionLimitError,
    LifecycleError,
    ModelError,
    OrqenError,
    OrqenTimeoutError,
    SecurityError,
    StorageError,
    ToolError,
)
from orqen.models.types import (
    AssistantMessage,
    Message,
    ModelRequest,
    ModelResponse,
    SystemMessage,
    TokenUsage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from orqen.runtime.metrics import RuntimeMetrics
from orqen.runtime.result import RunResult
from orqen.runtime.stream import RunStream
from orqen.sessions.manager import SessionManager
from orqen.sessions.models import Session
from orqen.tasks.manager import TaskManager
from orqen.telemetry.logging import bind_correlation_id, get_logger
from orqen.tools.permissions import PermissionContext

if TYPE_CHECKING:
    from orqen.agents.agent import Agent
    from orqen.storage.repositories import EventRepository, SessionRepository, TaskRepository
    from orqen.storage.sqlite import SQLiteStorage
    from orqen.tasks.models import Task

_logger = get_logger("runtime")


class RuntimeState(StrEnum):
    """Lifecycle states for `Runtime`."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


class Runtime:
    """Async execution engine for single-agent runs with bounded concurrency."""

    def __init__(
        self,
        config: OrqenConfig | None = None,
        *,
        max_concurrent_runs: int | None = None,
        acquire_timeout_seconds: float | None = None,
        stream_queue_size: int = 256,
        storage: SQLiteStorage | None = None,
        session_repository: SessionRepository | None = None,
        task_repository: TaskRepository | None = None,
        event_repository: EventRepository | None = None,
    ) -> None:
        self.config = config or OrqenConfig()
        resolved_max = (
            max_concurrent_runs
            if max_concurrent_runs is not None
            else self.config.runtime.max_concurrent_tasks
        )
        if resolved_max < 1:
            raise ValueError("max_concurrent_runs must be >= 1.")
        if acquire_timeout_seconds is not None and acquire_timeout_seconds < 0:
            raise ValueError("acquire_timeout_seconds must be >= 0.")
        if stream_queue_size < 1:
            raise ValueError("stream_queue_size must be >= 1.")

        self.max_concurrent_runs = resolved_max
        self.acquire_timeout_seconds = acquire_timeout_seconds
        self.stream_queue_size = stream_queue_size

        self._session_repo = session_repository or (
            storage.sessions if storage is not None else None
        )
        self._task_repo = task_repository or (storage.tasks if storage is not None else None)
        self._event_repo = event_repository or (storage.events if storage is not None else None)
        self._session_manager: SessionManager | None = None
        self._task_manager: TaskManager | None = None
        if self._session_repo is not None and self._task_repo is not None:
            self._session_manager = SessionManager(
                self._session_repo,
                tasks=self._task_repo,
                events=self._event_repo,
            )
            self._task_manager = TaskManager(
                self._task_repo,
                sessions=self._session_repo,
                events=self._event_repo,
            )

        self._state = RuntimeState.CREATED
        self._semaphore: asyncio.Semaphore | None = None
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self.metrics = RuntimeMetrics()

    @property
    def state(self) -> RuntimeState:
        """Current lifecycle state of the runtime."""
        return self._state

    async def initialize(self) -> Runtime:
        """Initialize runtime resources. Idempotent if already `RUNNING`."""
        if self._state == RuntimeState.RUNNING:
            return self
        if self._state in (RuntimeState.CLOSING, RuntimeState.CLOSED):
            raise LifecycleError(
                f"Cannot initialize Runtime from state {self._state.value}.",
                code="RUNTIME_CLOSED",
            )
        self._semaphore = asyncio.Semaphore(self.max_concurrent_runs)
        self._state = RuntimeState.RUNNING
        _logger.info(
            "Runtime initialized",
            extra={"max_concurrent_runs": self.max_concurrent_runs},
        )
        return self

    async def close(self) -> None:
        """Cancel any active runs and release runtime resources."""
        if self._state == RuntimeState.CLOSED:
            return
        if self._state == RuntimeState.CREATED:
            self._state = RuntimeState.CLOSED
            return

        self._state = RuntimeState.CLOSING
        pending = [t for t in self._active_tasks if not t.done()]
        if pending:
            _logger.info(
                "Cancelling active runs during runtime shutdown",
                extra={"active_runs": len(pending)},
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        self._active_tasks.clear()
        self._state = RuntimeState.CLOSED
        _logger.info("Runtime closed")

    async def __aenter__(self) -> Runtime:
        return await self.initialize()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def _ensure_ready(self) -> asyncio.Semaphore:
        if self._state != RuntimeState.RUNNING or self._semaphore is None:
            raise LifecycleError(
                f"Runtime is not running (current state: {self._state.value}). "
                "Use 'async with Runtime() as runtime:' or call 'await runtime.initialize()'.",
                code="RUNTIME_NOT_RUNNING",
                details={"state": self._state.value},
            )
        return self._semaphore

    def _prepare_agent(self, agent: Agent) -> None:
        if agent.state == AgentState.RUNNING:
            raise LifecycleError(
                f"Agent '{agent.name}' is already running; concurrent runs on the same "
                "Agent instance are not supported.",
                code="AGENT_ALREADY_RUNNING",
                details={"agent_id": agent.agent_id, "agent_name": agent.name},
            )
        if agent.state in (AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED):
            agent.reset()
        if agent.state != AgentState.READY:
            raise LifecycleError(
                f"Agent '{agent.name}' is not ready for execution (state: {agent.state.value}).",
                code="AGENT_NOT_READY",
                details={"agent_id": agent.agent_id, "state": agent.state.value},
            )

    async def _acquire_slot(self, semaphore: asyncio.Semaphore) -> None:
        if self.acquire_timeout_seconds is None:
            await semaphore.acquire()
            return
        if self.acquire_timeout_seconds == 0:
            if semaphore.locked():
                raise OrqenTimeoutError(
                    "Runtime concurrency limit reached; no execution slots available.",
                    code="CONCURRENCY_LIMIT_REACHED",
                    details={"max_concurrent_runs": self.max_concurrent_runs},
                )
            await semaphore.acquire()
            return
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=self.acquire_timeout_seconds)
        except TimeoutError as exc:
            raise OrqenTimeoutError(
                f"Timed out waiting {self.acquire_timeout_seconds}s for a runtime execution slot.",
                code="CONCURRENCY_LIMIT_REACHED",
                details={"max_concurrent_runs": self.max_concurrent_runs},
                cause=exc,
            ) from exc

    def _resolve_session_id(self, session: Session | str | None) -> str | None:
        if session is None:
            return None
        if self._session_manager is None or self._task_manager is None or self._event_repo is None:
            raise StorageError(
                "Session-backed execution requires Runtime(..., storage=...) or "
                "session/task/event repositories.",
                code="PERSISTENCE_NOT_CONFIGURED",
            )
        return session.session_id if isinstance(session, Session) else session

    async def run(
        self,
        agent: Agent,
        input: str,
        *,
        session: Session | str | None = None,
        timeout_seconds: float | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunResult:
        """Execute `agent` asynchronously and return a structured `RunResult`."""
        semaphore = self._ensure_ready()
        self._prepare_agent(agent)
        session_id = self._resolve_session_id(session)

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        corr_id = correlation_id or run_id
        current_task = asyncio.current_task()

        await self._acquire_slot(semaphore)
        if current_task is not None:
            self._active_tasks.add(current_task)
        try:
            return await self._execute_run(
                agent=agent,
                user_input=input,
                run_id=run_id,
                correlation_id=corr_id,
                session_id=session_id,
                timeout_seconds=timeout_seconds,
                metadata=metadata or {},
                event_stream=None,
                prefer_stream=False,
            )
        finally:
            if current_task is not None:
                self._active_tasks.discard(current_task)
            semaphore.release()

    def stream(
        self,
        agent: Agent,
        input: str,
        *,
        session: Session | str | None = None,
        timeout_seconds: float | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunStream:
        """Execute `agent` and return a `RunStream` yielding typed `RuntimeEvent`s."""
        semaphore = self._ensure_ready()
        self._prepare_agent(agent)
        session_id = self._resolve_session_id(session)

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        corr_id = correlation_id or run_id
        run_stream = RunStream(run_id=run_id, max_queue_size=self.stream_queue_size)

        async def _runner() -> RunResult:
            await self._acquire_slot(semaphore)
            try:
                result = await self._execute_run(
                    agent=agent,
                    user_input=input,
                    run_id=run_id,
                    correlation_id=corr_id,
                    session_id=session_id,
                    timeout_seconds=timeout_seconds,
                    metadata=metadata or {},
                    event_stream=run_stream,
                    prefer_stream=agent.model.capabilities.streaming,
                )
                await run_stream._complete(result)
                return result
            except BaseException as exc:
                await run_stream._fail(exc)
                raise
            finally:
                current = asyncio.current_task()
                if current is not None:
                    self._active_tasks.discard(current)
                semaphore.release()

        task = asyncio.create_task(_runner(), name=f"orqen-stream-{run_id}")
        self._active_tasks.add(task)
        run_stream._attach_task(task)
        return run_stream

    async def _emit(
        self,
        stream: RunStream | None,
        event: RuntimeEvent,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
        suppress_persistence_error: bool = False,
    ) -> None:
        if stream is not None:
            await stream._emit(event)
        if (
            self._event_repo is not None
            and session_id is not None
            and not isinstance(event, ModelStreamChunk)
        ):
            try:
                await self._event_repo.append(
                    session_id=session_id,
                    task_id=task_id,
                    event=event,
                )
                self.metrics.events_persisted_total += 1
            except Exception:
                self.metrics.persistence_failures_total += 1
                if not suppress_persistence_error:
                    raise
                _logger.exception(
                    "Failed to persist runtime event",
                    extra={"run_id": event.run_id, "event_type": event.event_type},
                )

    async def _execute_run(
        self,
        *,
        agent: Agent,
        user_input: str,
        run_id: str,
        correlation_id: str,
        session_id: str | None,
        timeout_seconds: float | None,
        metadata: dict[str, Any],
        event_stream: RunStream | None,
        prefer_stream: bool,
    ) -> RunResult:
        task_record: Task | None = None
        if session_id is not None:
            assert self._session_manager is not None
            assert self._task_manager is not None
            await self._session_manager.require_open(session_id)
            await self._session_manager.activate(session_id)
            task_record = await self._task_manager.create(
                session_id=session_id,
                input=user_input,
                agent_id=agent.agent_id,
                agent_name=agent.name,
                run_id=run_id,
                metadata=metadata,
            )
            self.metrics.tasks_created_total += 1
            task_record = await self._task_manager.start(task_record.task_id, run_id=run_id)

        task_id = task_record.task_id if task_record is not None else None
        started_at = datetime.now(UTC)
        start_mono = time.monotonic()
        effective_deadline = (
            timeout_seconds if timeout_seconds is not None else agent.config.run_timeout_seconds
        )

        self.metrics.executions_total += 1
        self.metrics.active_runs += 1
        if self.metrics.active_runs > self.metrics.peak_concurrent_runs:
            self.metrics.peak_concurrent_runs = self.metrics.active_runs

        model_calls = 0
        tool_calls_count = 0
        total_usage = TokenUsage()

        with bind_correlation_id(correlation_id):
            agent.transition_to(AgentState.RUNNING)
            _logger.info(
                "Agent run started",
                extra={
                    "run_id": run_id,
                    "agent_id": agent.agent_id,
                    "agent_name": agent.name,
                    "session_id": session_id,
                    "task_id": task_id,
                },
            )
            await self._emit(
                event_stream,
                RunStarted(
                    run_id=run_id,
                    agent_id=agent.agent_id,
                    agent_name=agent.name,
                    correlation_id=correlation_id,
                ),
                session_id=session_id,
                task_id=task_id,
            )

            async def _run_loop() -> str:
                nonlocal model_calls, tool_calls_count, total_usage

                messages: list[Message] = []
                if agent.instructions:
                    messages.append(SystemMessage(content=agent.instructions))
                messages.append(UserMessage(content=user_input))

                tool_schemas = agent.tools.schemas() if len(agent.tools) > 0 else ()

                for iteration in range(1, agent.config.max_iterations + 1):
                    use_stream = prefer_stream and agent.model.capabilities.streaming
                    request = ModelRequest(
                        messages=tuple(messages),
                        tools=tool_schemas,
                        stream=use_stream,
                        timeout_seconds=agent.config.model_timeout_seconds,
                    )

                    await self._emit(
                        event_stream,
                        ModelRequestStarted(
                            run_id=run_id,
                            agent_id=agent.agent_id,
                            agent_name=agent.name,
                            correlation_id=correlation_id,
                            iteration=iteration,
                            model_name=agent.model.model_name,
                            provider=agent.model.provider,
                        ),
                        session_id=session_id,
                        task_id=task_id,
                    )

                    model_calls += 1
                    self.metrics.model_calls_total += 1
                    response = await self._invoke_model(
                        agent=agent,
                        request=request,
                        iteration=iteration,
                        run_id=run_id,
                        correlation_id=correlation_id,
                        event_stream=event_stream,
                    )
                    total_usage = total_usage + response.usage

                    await self._emit(
                        event_stream,
                        ModelRequestCompleted(
                            run_id=run_id,
                            agent_id=agent.agent_id,
                            agent_name=agent.name,
                            correlation_id=correlation_id,
                            iteration=iteration,
                            request_id=response.request_id,
                            finish_reason=response.finish_reason,
                            usage=response.usage,
                            tool_call_count=len(response.tool_calls),
                        ),
                        session_id=session_id,
                        task_id=task_id,
                    )

                    if not response.tool_calls:
                        if response.text is None:
                            raise ModelError(
                                "Model returned neither text nor tool_calls.",
                                code="MALFORMED_MODEL_RESPONSE",
                                details={"request_id": response.request_id},
                            )
                        return response.text

                    messages.append(
                        AssistantMessage(
                            content=response.text,
                            tool_calls=response.tool_calls,
                        )
                    )

                    for call in response.tool_calls:
                        if tool_calls_count >= agent.config.max_tool_calls:
                            raise ExecutionLimitError(
                                f"Maximum tool calls ({agent.config.max_tool_calls}) exceeded.",
                                code="MAX_TOOL_CALLS_EXCEEDED",
                                details={"max_tool_calls": agent.config.max_tool_calls},
                            )
                        tool_calls_count += 1
                        self.metrics.tool_calls_total += 1
                        tool_msg = await self._execute_tool_call(
                            agent=agent,
                            call=call,
                            run_id=run_id,
                            correlation_id=correlation_id,
                            event_stream=event_stream,
                            session_id=session_id,
                            task_id=task_id,
                        )
                        messages.append(tool_msg)

                raise ExecutionLimitError(
                    f"Maximum iterations ({agent.config.max_iterations}) "
                    "reached without completion.",
                    code="MAX_ITERATIONS_EXCEEDED",
                    details={"max_iterations": agent.config.max_iterations},
                )

            try:
                if effective_deadline is not None:
                    final_output = await asyncio.wait_for(_run_loop(), timeout=effective_deadline)
                else:
                    final_output = await _run_loop()

                completed_at = datetime.now(UTC)
                duration = time.monotonic() - start_mono
                agent.transition_to(AgentState.COMPLETED)
                self.metrics.executions_succeeded += 1
                self.metrics.total_duration_seconds += duration

                if self._task_manager is not None and task_id is not None:
                    try:
                        await self._task_manager.complete(task_id, output=final_output)
                        self.metrics.tasks_completed_total += 1
                    except Exception:
                        self.metrics.persistence_failures_total += 1
                        raise

                await self._emit(
                    event_stream,
                    RunCompleted(
                        run_id=run_id,
                        agent_id=agent.agent_id,
                        agent_name=agent.name,
                        correlation_id=correlation_id,
                        output=final_output,
                        model_calls=model_calls,
                        tool_calls=tool_calls_count,
                        usage=total_usage,
                    ),
                    session_id=session_id,
                    task_id=task_id,
                )
                _logger.info(
                    "Agent run completed",
                    extra={
                        "run_id": run_id,
                        "agent_id": agent.agent_id,
                        "session_id": session_id,
                        "task_id": task_id,
                        "duration_seconds": round(duration, 6),
                        "model_calls": model_calls,
                        "tool_calls": tool_calls_count,
                    },
                )
                return RunResult(
                    run_id=run_id,
                    agent_id=agent.agent_id,
                    agent_name=agent.name,
                    session_id=session_id,
                    task_id=task_id,
                    output=final_output,
                    status="completed",
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                    model_calls=model_calls,
                    tool_calls=tool_calls_count,
                    usage=total_usage,
                    metadata=dict(metadata),
                )
            except asyncio.CancelledError:
                duration = time.monotonic() - start_mono
                agent.transition_to(AgentState.CANCELLED)
                self.metrics.executions_cancelled += 1
                self.metrics.total_duration_seconds += duration
                if self._task_manager is not None and task_id is not None:
                    try:
                        await self._task_manager.cancel(task_id, reason="Run cancelled")
                        self.metrics.tasks_cancelled_total += 1
                    except Exception:
                        self.metrics.persistence_failures_total += 1
                        _logger.exception(
                            "Failed to persist task cancellation",
                            extra={"run_id": run_id, "task_id": task_id},
                        )
                await self._emit(
                    event_stream,
                    RunCancelled(
                        run_id=run_id,
                        agent_id=agent.agent_id,
                        agent_name=agent.name,
                        correlation_id=correlation_id,
                    ),
                    session_id=session_id,
                    task_id=task_id,
                    suppress_persistence_error=True,
                )
                _logger.warning(
                    "Agent run cancelled",
                    extra={"run_id": run_id, "agent_id": agent.agent_id, "task_id": task_id},
                )
                raise
            except TimeoutError as exc:
                duration = time.monotonic() - start_mono
                timeout_err = OrqenTimeoutError(
                    f"Agent run '{run_id}' exceeded deadline of {effective_deadline}s.",
                    details={"run_id": run_id, "timeout_seconds": effective_deadline},
                    cause=exc,
                )
                agent.transition_to(AgentState.FAILED)
                self.metrics.executions_failed += 1
                self.metrics.executions_timed_out += 1
                self.metrics.total_duration_seconds += duration
                if self._task_manager is not None and task_id is not None:
                    try:
                        await self._task_manager.timeout(task_id, error=timeout_err)
                        self.metrics.tasks_timed_out_total += 1
                    except Exception:
                        self.metrics.persistence_failures_total += 1
                        raise
                await self._emit(
                    event_stream,
                    RunFailed(
                        run_id=run_id,
                        agent_id=agent.agent_id,
                        agent_name=agent.name,
                        correlation_id=correlation_id,
                        error_type=type(timeout_err).__name__,
                        error_code=timeout_err.code,
                        error_message=timeout_err.message,
                    ),
                    session_id=session_id,
                    task_id=task_id,
                )
                _logger.error(
                    "Agent run timed out",
                    extra={"run_id": run_id, "agent_id": agent.agent_id, "task_id": task_id},
                )
                raise timeout_err from exc
            except OrqenError as exc:
                duration = time.monotonic() - start_mono
                agent.transition_to(AgentState.FAILED)
                self.metrics.executions_failed += 1
                if isinstance(exc, OrqenTimeoutError):
                    self.metrics.executions_timed_out += 1
                self.metrics.total_duration_seconds += duration
                if self._task_manager is not None and task_id is not None:
                    try:
                        if isinstance(exc, OrqenTimeoutError):
                            await self._task_manager.timeout(task_id, error=exc)
                            self.metrics.tasks_timed_out_total += 1
                        else:
                            await self._task_manager.fail(task_id, error=exc)
                            self.metrics.tasks_failed_total += 1
                    except Exception:
                        self.metrics.persistence_failures_total += 1
                        raise
                await self._emit(
                    event_stream,
                    RunFailed(
                        run_id=run_id,
                        agent_id=agent.agent_id,
                        agent_name=agent.name,
                        correlation_id=correlation_id,
                        error_type=type(exc).__name__,
                        error_code=exc.code,
                        error_message=exc.message,
                    ),
                    session_id=session_id,
                    task_id=task_id,
                )
                _logger.error(
                    "Agent run failed",
                    extra={
                        "run_id": run_id,
                        "agent_id": agent.agent_id,
                        "task_id": task_id,
                        "error_code": exc.code,
                    },
                )
                raise
            except Exception as exc:
                duration = time.monotonic() - start_mono
                wrapped = ExecutionError(
                    f"Unexpected runtime failure: {exc}",
                    details={"run_id": run_id, "error_type": type(exc).__name__},
                    cause=exc,
                )
                agent.transition_to(AgentState.FAILED)
                self.metrics.executions_failed += 1
                self.metrics.total_duration_seconds += duration
                if self._task_manager is not None and task_id is not None:
                    try:
                        await self._task_manager.fail(task_id, error=wrapped)
                        self.metrics.tasks_failed_total += 1
                    except Exception:
                        self.metrics.persistence_failures_total += 1
                        raise
                await self._emit(
                    event_stream,
                    RunFailed(
                        run_id=run_id,
                        agent_id=agent.agent_id,
                        agent_name=agent.name,
                        correlation_id=correlation_id,
                        error_type=type(wrapped).__name__,
                        error_code=wrapped.code,
                        error_message=wrapped.message,
                    ),
                    session_id=session_id,
                    task_id=task_id,
                )
                raise wrapped from exc
            finally:
                self.metrics.active_runs -= 1

    async def _invoke_model(
        self,
        *,
        agent: Agent,
        request: ModelRequest,
        iteration: int,
        run_id: str,
        correlation_id: str,
        event_stream: RunStream | None,
    ) -> ModelResponse:
        timeout = agent.config.model_timeout_seconds
        try:
            if request.stream:

                async def _consume_stream() -> ModelResponse:
                    final_response: ModelResponse | None = None
                    accumulated: list[str] = []
                    last_request_id = ""
                    async for delta in agent.model.stream(request):
                        last_request_id = delta.request_id or last_request_id
                        if delta.text_delta:
                            accumulated.append(delta.text_delta)
                            await self._emit(
                                event_stream,
                                ModelStreamChunk(
                                    run_id=run_id,
                                    agent_id=agent.agent_id,
                                    agent_name=agent.name,
                                    correlation_id=correlation_id,
                                    iteration=iteration,
                                    request_id=last_request_id,
                                    text_delta=delta.text_delta,
                                ),
                            )
                        if delta.is_final and delta.response is not None:
                            final_response = delta.response
                    if final_response is None:
                        raise ModelError(
                            "Model stream ended without a final ModelResponse.",
                            code="INCOMPLETE_MODEL_STREAM",
                        )
                    return final_response

                return await asyncio.wait_for(_consume_stream(), timeout=timeout)

            return await asyncio.wait_for(agent.model.generate(request), timeout=timeout)
        except TimeoutError as exc:
            raise OrqenTimeoutError(
                f"Model request timed out after {timeout}s.",
                details={"timeout_seconds": timeout, "model": agent.model.model_name},
                cause=exc,
            ) from exc

    async def _execute_tool_call(
        self,
        *,
        agent: Agent,
        call: ToolCall,
        run_id: str,
        correlation_id: str,
        event_stream: RunStream | None,
        session_id: str | None = None,
        task_id: str | None = None,
    ) -> ToolResultMessage:
        await self._emit(
            event_stream,
            ToolCallRequested(
                run_id=run_id,
                agent_id=agent.agent_id,
                agent_name=agent.name,
                correlation_id=correlation_id,
                tool_call_id=call.id,
                tool_name=call.name,
            ),
            session_id=session_id,
            task_id=task_id,
        )

        # 1. Lookup
        try:
            tool_obj = agent.tools.get(call.name)
        except ToolError as exc:
            self.metrics.tool_failures_total += 1
            await self._emit(
                event_stream,
                ToolExecutionFailed(
                    run_id=run_id,
                    agent_id=agent.agent_id,
                    agent_name=agent.name,
                    correlation_id=correlation_id,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    error_type=type(exc).__name__,
                    error_message=exc.message,
                ),
                session_id=session_id,
                task_id=task_id,
            )
            if agent.config.fail_on_tool_error:
                raise
            return ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=exc.message,
                is_error=True,
            )

        # 2. Permission check (always enforced before execution; SecurityError aborts run)
        decision = agent.permission_policy.check(
            PermissionContext(
                agent_name=agent.name,
                run_id=run_id,
                tool_name=tool_obj.name,
                dangerous=tool_obj.dangerous,
                permissions=tool_obj.permissions,
                arguments=call.arguments,
            )
        )
        if not decision.allowed:
            self.metrics.tool_failures_total += 1
            sec_err = SecurityError(
                decision.reason or f"Permission denied for tool '{tool_obj.name}'.",
                code="TOOL_PERMISSION_DENIED",
                details={"tool": tool_obj.name, "tool_call_id": call.id},
            )
            await self._emit(
                event_stream,
                ToolExecutionFailed(
                    run_id=run_id,
                    agent_id=agent.agent_id,
                    agent_name=agent.name,
                    correlation_id=correlation_id,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    error_type=type(sec_err).__name__,
                    error_message=sec_err.message,
                ),
                session_id=session_id,
                task_id=task_id,
            )
            raise sec_err

        # 3. Execution with timeout
        await self._emit(
            event_stream,
            ToolExecutionStarted(
                run_id=run_id,
                agent_id=agent.agent_id,
                agent_name=agent.name,
                correlation_id=correlation_id,
                tool_call_id=call.id,
                tool_name=call.name,
            ),
            session_id=session_id,
            task_id=task_id,
        )
        t0 = time.monotonic()
        effective_timeout = (
            tool_obj.timeout_seconds
            if tool_obj.timeout_seconds is not None
            else agent.config.tool_timeout_seconds
        )
        try:
            output = await tool_obj.invoke(
                call.arguments,
                timeout_seconds=effective_timeout,
            )
            duration = time.monotonic() - t0
            await self._emit(
                event_stream,
                ToolExecutionCompleted(
                    run_id=run_id,
                    agent_id=agent.agent_id,
                    agent_name=agent.name,
                    correlation_id=correlation_id,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    duration_seconds=duration,
                ),
                session_id=session_id,
                task_id=task_id,
            )
            return ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=output,
                is_error=False,
            )
        except asyncio.CancelledError:
            raise
        except OrqenError as exc:
            self.metrics.tool_failures_total += 1
            await self._emit(
                event_stream,
                ToolExecutionFailed(
                    run_id=run_id,
                    agent_id=agent.agent_id,
                    agent_name=agent.name,
                    correlation_id=correlation_id,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    error_type=type(exc).__name__,
                    error_message=exc.message,
                ),
                session_id=session_id,
                task_id=task_id,
            )
            if agent.config.fail_on_tool_error or isinstance(exc, OrqenTimeoutError):
                raise
            return ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=exc.message,
                is_error=True,
            )
