# ADR-0005: Runtime Execution Loop, Bounded Concurrency, and Streaming

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

Phase 1 requires an async-first `Runtime` that executes single-agent loops with bounded concurrency, predictable overload behavior, cancellation/deadline propagation, and typed streaming events.

## Decision

1. **Runtime Lifecycle:**
   - `Runtime` supports `async with Runtime(...) as runtime:` (and explicit `initialize()`/`close()`).
   - Calling `run()` or `stream()` before initialization or after `close()` raises `LifecycleError`.
   - Closing a `Runtime` with active runs cancels all tracked tasks and awaits their cleanup before returning.
2. **Bounded Concurrency:**
   - `Runtime` enforces `max_concurrent_runs` (defaulting from `OrqenConfig.runtime.max_concurrent_tasks`) using an `asyncio.Semaphore` acquired inside a `try/finally` block, guaranteeing release on success, failure, and `CancelledError`.
   - Overload behavior is configurable via `acquire_timeout_seconds` (wait up to timeout, then raise `OrqenTimeoutError` if capacity is unavailable; `0` = fail fast).
3. **Execution Loop & Tool-Call Ordering:**
   - Loop enforces `max_iterations`, `max_tool_calls`, `run_timeout_seconds` (deadline), and cancellation at every step boundary.
   - Multiple tool calls within a single assistant turn are executed **sequentially in original call order** in Phase 1 to guarantee deterministic ordering and simple failure semantics. Each tool failure is converted into an error `ToolResultMessage` (allowing the model to recover) unless `fail_on_tool_error=True` or a `SecurityError`/cancellation occurs.
4. **Streaming:**
   - `runtime.stream(agent, input=...)` returns a `RunStream` async iterator yielding typed `RuntimeEvent` instances (`RunStarted`, `ModelRequestStarted`, `ModelStreamChunk`, `ModelRequestCompleted`, `ToolCallRequested`, `ToolExecutionStarted`, `ToolExecutionCompleted`, `ToolExecutionFailed`, `RunCompleted`, `RunFailed`, `RunCancelled`).
   - Events are pushed through a bounded `asyncio.Queue` (`maxsize` configurable) to prevent unbounded memory accumulation, and `stream.result` provides the final `RunResult` after completion.

## Consequences

- Zero unbounded task creation or unbounded event buffering.
- Deterministic, debuggable tool execution order in Phase 1, leaving parallel tool-batch execution as an opt-in enhancement in later phases.
