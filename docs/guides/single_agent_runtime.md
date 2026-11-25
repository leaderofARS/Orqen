# Single-Agent Runtime Guide (`v0.1.0`)

## 1. Models & Capabilities

All models implement `orqen.models.Model` and declare `ModelCapabilities` (`streaming`, `tool_calling`, `structured_output`, `vision`, `audio`, `reasoning`). `Runtime` validates requests against capabilities before invocation.

For offline and credential-free testing, use `MockModel`:

```python
from orqen.models import MockModel, ToolCall

model = MockModel(
    responses=[
        {"tool_calls": [ToolCall(name="add", arguments={"a": 2, "b": 3})]},
        {"text": "2 + 3 = 5"},
    ]
)
```

## 2. Tools, Validation & Permissions

Decorate async or sync functions with `@tool`:

- **Async tools** run directly on the event loop.
- **Sync tools** run via `asyncio.to_thread` so they do not block concurrent async runs. Note: Python cannot forcibly terminate an arbitrary blocking OS/C thread on timeout or cancellation.
- **Validation:** Model-supplied arguments are validated against the inferred Pydantic schema; invalid arguments produce `ToolValidationError`.
- **Permissions:** `DefaultPermissionPolicy` allows safe tools (`dangerous=False`, `permissions=()`) and rejects dangerous or privileged tools unless explicitly allowlisted.

## 3. Agent Lifecycle

`Agent` instances follow a strict state machine (`AgentState`):
`CREATED -> INITIALIZED -> READY -> RUNNING -> (COMPLETED | FAILED | CANCELLED) -> READY | TERMINATED`.

- `Agent` instances are reusable **sequentially** (`Runtime` resets finished agents to `READY` before a new run).
- Concurrent execution of the **same** `Agent` instance raises `LifecycleError`.

## 4. Runtime Execution, Concurrency & Streaming

- Use `async with Runtime(max_concurrent_runs=10) as runtime:` to manage initialization and cleanup.
- **Execution Loop:** Enforces `max_iterations`, `max_tool_calls`, `model_timeout_seconds`, `tool_timeout_seconds`, and `run_timeout_seconds`. Multiple tool calls in a single turn execute sequentially in original call order.
- **Streaming:** `stream = runtime.stream(agent, input="...")` returns a `RunStream` async iterator backed by a bounded queue (`stream_queue_size`). After exhausting `async for event in stream:`, retrieve the final `RunResult` via `stream.result`.
- **Error Handling:** `SecurityError` (permission denial) aborts immediately. `ToolError` and `ToolValidationError` become error `ToolResultMessage`s when `AgentConfig.fail_on_tool_error=False` (default), or abort when `True`.
