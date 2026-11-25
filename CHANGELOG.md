# Changelog

All notable changes to ORQEN are documented in this file.

## [0.2.0] - Phase 2: Sessions, Tasks, State & Persistence

### Added
- **SQLite Storage & Migrations (`orqen.storage`):** Repository contracts (`SessionRepository`, `TaskRepository`, `StateRepository`, `EventRepository`, `ArtifactRepository`), `SQLiteDatabase` with dedicated single-worker `ThreadPoolExecutor`, WAL mode, foreign key enforcement, `BEGIN IMMEDIATE` transactions, `PRAGMA integrity_check`, and versioned `schema_migrations` (`0001_initial_schema`).
- **Sessions (`orqen.sessions`):** `Session` model, `SessionState` lifecycle (`CREATED` → `ACTIVE` → `IDLE` → `CLOSED`), and `SessionManager` with metadata/title updates, pagination, filtering, deletion, and associated task/event/artifact queries.
- **Tasks (`orqen.tasks`):** `Task` model, `TaskState` lifecycle (`PENDING` → `RUNNING` → `COMPLETED` | `FAILED` | `CANCELLED` | `TIMED_OUT`), `TaskManager` with secret-sanitized error persistence, duration tracking, filtering, and pagination.
- **Persistent State (`orqen.state`):** `StateEntry` and `StateManager` with strict JSON validation, namespace/session/task scope isolation, and optimistic concurrency control (`expected_revision` / `StateConflictError`).
- **Persisted Events (`orqen.events`):** `PersistedEvent` with monotonic per-session `sequence_number` ordering, batch appends, and paginated/filtered retrieval.
- **ArtifactStore (`orqen.artifacts`):** Filesystem-backed `ArtifactStore` with atomic temp-write + `os.replace`, path-traversal/symlink protection, SHA-256 integrity verification, streaming reads, and SQLite metadata.
- **Runtime Integration (`orqen.runtime`):** Optional `storage` on `Runtime` and `session` parameter on `Runtime.run()` / `Runtime.stream()` while preserving standalone Phase 1 execution.
- **Config, Exceptions, Docs & Examples:** `StorageConfig`, `ArtifactsConfig`, Phase 2 exception hierarchy (`DatabaseError`, `MigrationError`, `RepositoryError`, `ArtifactError`, `SessionError`, `TaskError`, `StateError`, `StateConflictError`), ADRs `ADR-0006`–`ADR-0011`, `docs/guides/persistence_and_sessions.md`, and runnable examples under `examples/`.

## [0.1.0] - Phase 1: Single-Agent Runtime

### Added
- **Models (`orqen.models`):** Provider-neutral `Model` ABC, `ModelCapabilities`, discriminated message types (`SystemMessage`, `UserMessage`, `AssistantMessage`, `ToolResultMessage`), `ModelRequest`, `ModelResponse`, `TokenUsage`, and deterministic `MockModel`.
- **Tools (`orqen.tools`):** `@tool` decorator (async & sync via `asyncio.to_thread`), Pydantic schema inference and argument validation, `ToolRegistry` with duplicate protection, `PermissionPolicy` & conservative `DefaultPermissionPolicy`, and tool timeouts.
- **Agents (`orqen.agents`):** `Agent` definition, `AgentConfig`, and validated `AgentState` lifecycle state machine (`CREATED` -> `INITIALIZED` -> `READY` -> `RUNNING` -> `COMPLETED`/`FAILED`/`CANCELLED` -> `READY`/`TERMINATED`).
- **Runtime & Streaming (`orqen.runtime`, `orqen.events`):** Async `Runtime` with bounded concurrency (`asyncio.Semaphore`), execution loop with iteration/tool-call limits, deadlines, cancellation propagation, `RunResult`, `RuntimeMetrics`, and `RunStream` yielding typed `RuntimeEvent`s.
- **Docs & Examples:** Single-agent runtime guide (`docs/guides/single_agent_runtime.md`), ADRs `ADR-0003`–`ADR-0005`, and runnable mock-based examples under `examples/`.

## [0.0.1] - Phase 0: Foundation

### Added
- Python package structure under `python/orqen/` with `hatchling` build configuration (`pyproject.toml`).
- Typed Pydantic v2 configuration system (`OrqenConfig`, `RuntimeConfig`, `LoggingConfig`, `load_config`).
- Structured exception hierarchy rooted at `OrqenError` with error codes, details, causes, and `to_dict()`.
- Structured logging foundation (`configure_logging`, `get_logger`, `bind_correlation_id`, `RedactingFilter`, text/JSON formatters).
- Unit and smoke test suite under `tests/unit/`.
- GitHub Actions CI workflow (`.github/workflows/ci.yml`) for `ruff`, `mypy`, and `pytest`.
- Initial architecture documentation (`ARCHITECTURE.md`, `ROADMAP.md`, `CONTRIBUTING.md`, `SECURITY.md`) and ADRs (`ADR-0001`, `ADR-0002`).
