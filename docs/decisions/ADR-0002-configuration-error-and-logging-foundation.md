# ADR-0002: Configuration, Error Model, and Logging Foundation

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

Every subsequent ORQEN subsystem (runtime, models, tools, sessions, persistence, security) depends on consistent configuration validation, structured error reporting, and correlation-aware logging with secret redaction.

## Decision

1. **Configuration:** Use frozen Pydantic v2 models (`OrqenConfig`) with `extra="forbid"`, loaded via `load_config()` with explicit precedence (defaults < TOML < `ORQEN_*` env vars < explicit overrides).
2. **Error Model:** Root all framework exceptions at `OrqenError` with a stable string `code`, `message`, `details` dict, explicit `cause` chaining, and `to_dict()` serialization.
3. **Logging:** Build on Python's standard `logging` module with `contextvars` correlation IDs, `text`/`json` formatters, and a `RedactingFilter` enabled by default.

## Alternatives Considered

- **Ad-hoc dicts / `os.getenv` scattered across modules:** Leads to silent misconfiguration and hidden global state.
- **External logging frameworks (e.g., `structlog`):** Adds an unnecessary dependency in Phase 0 when stdlib `logging` + `contextvars` meets all requirements.

## Consequences

- Minimal runtime dependency footprint in Phase 0 (`pydantic` only).
- Consistent error payloads and redacted logs across all future subsystems.
