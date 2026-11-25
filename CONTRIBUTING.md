# Contributing to ORQEN

## Development Setup

1. Install Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).
2. Create the isolated environment and install dev dependencies:

```bash
uv sync --dev
```

## Engineering Standards

- **Typing:** All public and internal Python code must pass `mypy` in `strict` mode.
- **Formatting & Linting:** All code must pass `ruff check .` and `ruff format --check .`.
- **Testing:** Every new behavior and failure path requires automated tests under `tests/`.
- **Architecture Decisions:** Material architectural choices must be documented as an ADR under `docs/decisions/`.
- **No Placeholders:** Do not commit empty placeholder modules or fake implementations.

## Running Local Verification

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```
