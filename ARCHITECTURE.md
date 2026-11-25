9# ORQEN Architecture

## 1. Purpose

ORQEN provides the infrastructure layer for reliable AI agent applications and multi-agent systems:

```text
Build → Execute → Coordinate → Persist → Recover → Scale
```

## 2. Polyglot Boundaries

ORQEN separates developer ergonomics from native systems performance:

| Layer | Language | Responsibilities | Status |
| :--- | :--- | :--- | :--- |
| Public SDK & Control Plane | Python 3.12+ | Agent/workflow APIs, Pydantic v2 schemas, configuration, model/tool integrations, CLI | **Active (Phase 0)** |
| Native Infrastructure Core | Rust (via PyO3/Maturin) | Bounded scheduler, worker pools, event processing, state primitives | Planned (Phase 6) |
| Specialized Kernels | C++20 | Hardware-specific or profiling-justified kernels only | Optional / Deferred |
| Developer Studio | TypeScript / React | Observability & workflow inspection UI | Planned (Phase 9) |

No native Rust or C++ code is initialized in Phase 0; native components are introduced only when profiling justifies them.

## 3. Implemented Subsystems (`v0.2.0`)

### 3.1 Phase 0 Foundations

- **Configuration (`orqen.config`):** Frozen Pydantic v2 models (`OrqenConfig`, `RuntimeConfig`, `LoggingConfig`) with `extra="forbid"` and deterministic `load_config()` precedence (defaults < TOML < `ORQEN_*` env vars < overrides).
- **Exception Hierarchy (`orqen.exceptions`):** Rooted at `OrqenError` (`code`, `message`, `details`, `cause`, `to_dict()`), including `ConfigurationError`, `ValidationError`, `LifecycleError`, `ExecutionError`, `ExecutionLimitError`, `OrqenTimeoutError`, `CancellationError`, `ModelError`, `ProviderError`, `ToolError`, `ToolValidationError`, `SecurityError`, and `StorageError`.
- **Telemetry & Logging (`orqen.telemetry`):** Standard-library `logging` with `contextvars` correlation IDs, `text`/`json` formatters, and `RedactingFilter` enabled by default.

### 3.2 Phase 1 Single-Agent Runtime

- **Models (`orqen.models`):** Provider-neutral `Model` ABC, `ModelCapabilities`, discriminated message types (`SystemMessage`, `UserMessage`, `AssistantMessage`, `ToolResultMessage`), `ModelRequest`/`ModelResponse`/`TokenUsage`, and deterministic `MockModel`.
- **Tools (`orqen.tools`):** `@tool` decorator (async and sync via `asyncio.to_thread`), Pydantic schema inference/validation, `ToolRegistry` with duplicate protection, and `PermissionPolicy` (`DefaultPermissionPolicy`).
- **Agents (`orqen.agents`):** `Agent` definition, `AgentConfig`, and validated `AgentState` lifecycle state machine.
- **Runtime & Events (`orqen.runtime`, `orqen.events`):** Async `Runtime` with bounded concurrency (`asyncio.Semaphore`), execution loop, deadlines, cancellation propagation, `RunResult`, `RuntimeMetrics`, and `RunStream` yielding typed `RuntimeEvent`s.

### 3.3 Phase 2 Persistence Infrastructure

- **SQLite Backend & Migrations (`orqen.storage`):** Repository interfaces (`SessionRepository`, `TaskRepository`, `StateRepository`, `EventRepository`, `ArtifactRepository`), `SQLiteDatabase` using a dedicated single-worker `ThreadPoolExecutor`, WAL mode, foreign key enforcement, `BEGIN IMMEDIATE` transactions, and ordered `schema_migrations` (`0001_initial_schema`).
- **Sessions (`orqen.sessions`):** `Session` model and `SessionManager` with explicit lifecycle (`CREATED` → `ACTIVE` → `IDLE` → `CLOSED`), metadata/title updates, pagination, and task/event/artifact queries.
- **Tasks (`orqen.tasks`):** `Task` model and `TaskManager` with explicit lifecycle (`PENDING` → `RUNNING` → `COMPLETED` | `FAILED` | `CANCELLED` | `TIMED_OUT`), sanitized error persistence, and bounded queries.
- **State (`orqen.state`):** `StateManager` with strict JSON validation, namespace/session/task isolation, and optimistic concurrency via `expected_revision` (`StateConflictError`).
- **Events & Artifacts (`orqen.events`, `orqen.artifacts`):** `PersistedEvent` with monotonic per-session `sequence_number`s, and `ArtifactStore` storing payload bytes atomically on disk with SHA-256 verification and SQLite metadata.
- **Optional Runtime Integration:** `Runtime(..., storage=...)` with `session=...` on `run()` and `stream()`, preserving standalone Phase 1 behavior when omitted.

## 4. Planned Subsystems (Phases 2–10)

Planned subsystems—including the single-agent runtime, provider-neutral model/tool abstractions, SQLite persistence, multi-agent orchestration, durable execution/checkpointing, memory management, Rust native scheduler, distributed workers, and Studio—are tracked in [ROADMAP.md](ROADMAP.md).
