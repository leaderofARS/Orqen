# ORQEN

**The Infrastructure Engine for Intelligent Agents.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-0.2.0-indigo.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Typed](https://img.shields.io/badge/typing-strict%20mypy-informational.svg)](pyproject.toml)

ORQEN is an open-source, async-first infrastructure framework for building, executing, coordinating, persisting, observing, and scaling reliable AI agents and multi-agent systems.

```text
Build → Execute → Coordinate → Persist → Recover → Observe → Scale
```

Unlike prompt-template libraries or opaque LLM wrappers, ORQEN treats agent execution as systems infrastructure: explicit lifecycle state machines, bounded concurrency, transactional persistence, strict schema validation, and deterministic observability.

---

## Key Capabilities (`v0.2.0`)

| Subsystem | Module | Capabilities |
| :--- | :--- | :--- |
| **Runtime Engine** | `orqen.runtime` | Async execution loop, bounded concurrency (`asyncio.Semaphore`), deadlines, cancellation propagation, streaming (`RunStream`), and `RuntimeMetrics` |
| **Agents & Tools** | `orqen.agents`, `orqen.tools` | Validated `AgentState` lifecycle, `@tool` decorator (async & sync via `asyncio.to_thread`), Pydantic schema validation, and conservative `PermissionPolicy` |
| **Model Contracts** | `orqen.models` | Provider-neutral `Model` ABC, `ModelCapabilities`, discriminated message schemas, `TokenUsage`, and deterministic `MockModel` |
| **Sessions & Tasks** | `orqen.sessions`, `orqen.tasks` | Durable `Session` (`CREATED` → `ACTIVE` ↔ `IDLE` → `CLOSED`) and `Task` (`PENDING` → `RUNNING` → terminal) lifecycles with secret-redacted errors |
| **Persistent State** | `orqen.state` | Strictly JSON-validated, namespace/session/task-scoped key-value store with optimistic concurrency (`expected_revision` / `StateConflictError`) |
| **SQLite Backend** | `orqen.storage` | Repository abstractions (`orqen.storage.repositories`), thread-affine `SQLiteDatabase` (WAL mode, foreign keys, `BEGIN IMMEDIATE`), and versioned `schema_migrations` |
| **Event History** | `orqen.events` | Typed `RuntimeEvent`s and durable `PersistedEvent`s with monotonic per-session `sequence_number` ordering |
| **ArtifactStore** | `orqen.artifacts` | Split filesystem + SQLite metadata storage with atomic temp-write/replace, path-traversal protection, streaming reads, and SHA-256 verification |
| **Configuration & Telemetry** | `orqen.config`, `orqen.telemetry` | Frozen Pydantic v2 config (`defaults < TOML < ORQEN_* env < overrides`), structured text/JSON logging, `contextvars` correlation IDs, and secret redaction |

---

## Installation

ORQEN targets **Python 3.12+** and uses [`uv`](https://docs.astral.sh/uv/) for dependency and environment management.

```bash
git clone https://github.com/leaderofARS/Orqen.git
cd Orqen
uv sync --dev
```

---

## Quick Start

### 1. Standalone Single-Agent Execution (Phase 1)

Persistence is completely optional. You can run agents in-memory with zero database setup:

```python
import asyncio
from orqen import Agent, Runtime
from orqen.models import MockModel
from orqen.tools import tool


@tool
async def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


async def main() -> None:
    model = MockModel(
        responses=[
            {"tool_calls": [{"id": "c1", "name": "add", "arguments": {"a": 19, "b": 23}}]},
            {"text": "The answer is 42."},
        ]
    )
    agent = Agent(name="assistant", model=model, instructions="Be precise.", tools=[add])

    async with Runtime(max_concurrent_runs=10) as runtime:
        result = await runtime.run(agent, input="What is 19 + 23?")
        print(result.output)


asyncio.run(main())
```

### 2. Persistent Sessions, Tasks & Events (Phase 2)

Attach `SQLiteStorage` to `Runtime` and pass a `Session` to durably persist tasks, outputs, and ordered execution events:

```python
import asyncio
from orqen import Agent, Runtime, SessionManager, SQLiteStorage
from orqen.models import MockModel


async def main() -> None:
    async with SQLiteStorage(database_path="./orqen.db") as storage:
        sessions = SessionManager(
            storage.sessions,
            tasks=storage.tasks,
            events=storage.events,
        )
        session = await sessions.create(title="Architecture Review")

        model = MockModel(responses=[{"text": "Session task completed."}])
        agent = Agent(name="reviewer", model=model)

        async with Runtime(storage=storage) as runtime:
            result = await runtime.run(agent, input="Review persistence", session=session)
            print(f"Task {result.task_id}: {result.output}")

        events = await sessions.get_events(session.session_id)
        print(f"Persisted {len(events)} ordered runtime events.")


asyncio.run(main())
```

More runnable examples—including optimistic state concurrency and `ArtifactStore`—are in [examples/](examples/).

---

## Verification & Quality Gates

ORQEN enforces strict static typing (`mypy --strict`), linting/formatting (`ruff`), and deterministic unit/integration testing (`pytest`):

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -v
uv run python -c "import orqen; print(orqen.__version__)"
```

---

## Documentation

- [Architecture Overview](ARCHITECTURE.md)
- [Product Roadmap](ROADMAP.md)
- [Persistence & Sessions Guide](docs/guides/persistence_and_sessions.md)
- [Single-Agent Runtime Guide](docs/guides/single_agent_runtime.md)
- [Architecture Decision Records (ADRs)](docs/decisions/)
- [Changelog](CHANGELOG.md)
- [Contributing Guide](CONTRIBUTING.md)
- [Security Policy](SECURITY.md)

---

## License

Licensed under the [MIT License](LICENSE).
