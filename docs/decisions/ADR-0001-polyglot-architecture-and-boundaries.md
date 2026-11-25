# ADR-0001: Polyglot Architecture and Language Boundaries

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

ORQEN requires an ergonomic developer-facing API for defining agents, tools, workflows, and integrations, alongside predictable high-performance scheduling, bounded concurrency, and state management under load.

## Decision

1. **Python 3.12+ is the primary public interface** (`python/orqen/`), owning agent definitions, configuration, schemas (Pydantic v2), model/tool integrations, and developer APIs.
2. **Rust (via PyO3 and Maturin) is the default native infrastructure language**, reserved for profiling-justified components (bounded scheduler, worker pools, event processing, state primitives) starting in Phase 6.
3. **C++20 is strictly optional**, permitted only when profiling or hardware/ecosystem requirements cannot be met in Rust.
4. **Phase 0 uses pure Python packaging (`hatchling`)** rather than initializing empty Rust/C++ crates before native components are needed.

## Alternatives Considered

- **Pure Python everywhere:** Insufficient for predictable bounded scheduling and low-overhead concurrent dispatch at scale.
- **Rust-first with thin Python bindings from Phase 0:** Adds build complexity and slows iteration before core Python contracts stabilize.

## Consequences

- Phase 0–5 remain fast to iterate and test in pure Python with strict typing (`mypy --strict`).
- Introducing Rust in Phase 6 will migrate the build backend in `pyproject.toml` from `hatchling` to `maturin` without changing public Python import paths.
