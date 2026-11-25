# ADR-0007: Repository Abstractions and Persistence Architecture

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

Domain managers (`SessionManager`, `TaskManager`, `StateManager`, `ArtifactStore`) and `Runtime` must not be tightly coupled to SQLite SQL statements, enabling future PostgreSQL or other backends without rewriting domain logic.

## Decision

Define explicit, typed abstract repository interfaces in `orqen.storage.repositories`:
- `SessionRepository`
- `TaskRepository`
- `StateRepository`
- `EventRepository`
- `ArtifactRepository`

Domain managers depend solely on these repository contracts and enforce lifecycle/validation rules in Python, while concrete SQLite implementations in `orqen.storage.sqlite` encapsulate parameterized SQL, transactions, and row mapping.

## Consequences

- Zero raw SQL in domain managers or `Runtime`.
- Concrete, strongly-typed repository methods instead of opaque generic CRUD abstractions.
