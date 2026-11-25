"""Lightweight in-process runtime metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class RuntimeMetrics:
    """In-memory execution counters for observability within a `Runtime`."""

    executions_total: int = 0
    executions_succeeded: int = 0
    executions_failed: int = 0
    executions_cancelled: int = 0
    executions_timed_out: int = 0
    model_calls_total: int = 0
    tool_calls_total: int = 0
    tool_failures_total: int = 0
    active_runs: int = 0
    peak_concurrent_runs: int = 0
    total_duration_seconds: float = 0.0
    tasks_created_total: int = 0
    tasks_completed_total: int = 0
    tasks_failed_total: int = 0
    tasks_cancelled_total: int = 0
    tasks_timed_out_total: int = 0
    events_persisted_total: int = 0
    persistence_failures_total: int = 0

    def snapshot(self) -> dict[str, int | float]:
        """Return a copy of all metric counters."""
        return asdict(self)
