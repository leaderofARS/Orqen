# ADR-0004: Model and Tool Contracts

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

ORQEN must remain provider-neutral and treat model outputs and tool arguments as untrusted inputs. Both async and sync developer tools must be supported without blocking the asyncio event loop.

## Decision

1. **Typed Model Messages & Capabilities:**
   - Conversations use discriminated Pydantic v2 message types: `SystemMessage`, `UserMessage`, `AssistantMessage`, `ToolResultMessage`.
   - Every `Model` exposes `ModelCapabilities` (`streaming`, `tool_calling`, `structured_output`, `vision`, `audio`, `reasoning`). `Runtime` validates requests against capabilities before invocation.
   - `ModelResponse` normalizes `text`, `tool_calls`, `finish_reason`, `usage` (`TokenUsage`), `request_id`, and `metadata`.
   - `MockModel` provides scripted, deterministic responses (text, single/multiple tool calls, delays, streaming chunks, exceptions, malformed responses) for credential-free testing.
2. **Tool System & Sync Execution:**
   - `@tool` wraps async or sync functions, infers Pydantic input schemas from type annotations, preserves function metadata, and normalizes outputs to strings/structured `ToolResult`.
   - Sync tools execute via `asyncio.to_thread` so they never block the event loop. Timeout enforcement uses `asyncio.wait_for`; we explicitly document that Python threads executing blocking sync C/OS calls cannot be forcibly killed on timeout.
3. **Tool Registry & Conservative Permissions:**
   - `ToolRegistry` enforces unique tool names and raises `ToolError` on duplicate registration unless explicitly replaced via `register(..., replace=True)` (default `False`).
   - Arguments from the model are validated against the tool's Pydantic schema before invocation; invalid arguments produce normalized `ToolValidationError` results returned to the model (or aborting per policy) without crashing the runtime.
   - `PermissionPolicy` is evaluated before every tool execution; the default policy (`DefaultPermissionPolicy`) permits only tools explicitly marked `requires_permission=False` (or allowlisted) and never trusts model-supplied permission claims.

## Consequences

- Models and tools are independently testable without network access or API keys.
- Blocking sync tools cannot starve concurrent async runs, though they remain subject to Python's cooperative thread-cancellation limits.
