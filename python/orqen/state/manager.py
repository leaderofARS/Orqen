"""Persistent, revision-aware scoped state management."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orqen.exceptions import StateError, ValidationError
from orqen.state.models import StateEntry, validate_json_value

if TYPE_CHECKING:
    from orqen.storage.repositories import StateRepository


def _validate_identifier(value: str, *, field_name: str) -> None:
    if not value or not value.strip():
        raise StateError(
            f"State '{field_name}' must be a non-empty string.",
            code="INVALID_STATE_SCOPE",
            details={"field": field_name},
        )


class StateManager:
    """Service for durable, namespaced, optimistic-concurrency key/value state."""

    def __init__(self, state_repo: StateRepository) -> None:
        self._repo = state_repo

    async def get(
        self,
        session_id: str,
        key: str,
        *,
        namespace: str = "default",
        task_id: str | None = None,
    ) -> StateEntry | None:
        """Retrieve a `StateEntry` by scope and key, or `None` if absent."""
        _validate_identifier(session_id, field_name="session_id")
        _validate_identifier(namespace, field_name="namespace")
        _validate_identifier(key, field_name="key")
        return await self._repo.get(
            namespace=namespace,
            session_id=session_id,
            key=key,
            task_id=task_id,
        )

    async def get_value(
        self,
        session_id: str,
        key: str,
        *,
        namespace: str = "default",
        task_id: str | None = None,
        default: Any = None,
    ) -> Any:
        """Retrieve only the deserialized value for `key`, or `default` if absent."""
        entry = await self.get(session_id, key, namespace=namespace, task_id=task_id)
        return entry.value if entry is not None else default

    async def set(
        self,
        session_id: str,
        key: str,
        value: Any,
        *,
        namespace: str = "default",
        task_id: str | None = None,
        expected_revision: int | None = None,
    ) -> StateEntry:
        """Create or update a state entry, optionally enforcing `expected_revision`."""
        _validate_identifier(session_id, field_name="session_id")
        _validate_identifier(namespace, field_name="namespace")
        _validate_identifier(key, field_name="key")
        validate_json_value(value)
        return await self._repo.set(
            namespace=namespace,
            session_id=session_id,
            key=key,
            value=value,
            task_id=task_id,
            expected_revision=expected_revision,
        )

    async def update(
        self,
        session_id: str,
        key: str,
        value: Any,
        *,
        expected_revision: int,
        namespace: str = "default",
        task_id: str | None = None,
    ) -> StateEntry:
        """Update an existing state entry only if its revision matches `expected_revision`."""
        if expected_revision < 1:
            raise StateError(
                f"expected_revision for update must be >= 1, got {expected_revision}.",
                code="INVALID_REVISION",
            )
        return await self.set(
            session_id,
            key,
            value,
            namespace=namespace,
            task_id=task_id,
            expected_revision=expected_revision,
        )

    async def delete(
        self,
        session_id: str,
        key: str,
        *,
        namespace: str = "default",
        task_id: str | None = None,
    ) -> bool:
        """Delete a state key within the specified scope."""
        _validate_identifier(session_id, field_name="session_id")
        _validate_identifier(namespace, field_name="namespace")
        _validate_identifier(key, field_name="key")
        return await self._repo.delete(
            namespace=namespace,
            session_id=session_id,
            key=key,
            task_id=task_id,
        )

    async def list_keys(
        self,
        session_id: str,
        *,
        namespace: str = "default",
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[str, ...]:
        """List keys in a scope with bounded pagination."""
        if limit < 1 or limit > 1000 or offset < 0:
            raise ValidationError("Invalid pagination parameters.", code="INVALID_PAGINATION")
        return await self._repo.list_keys(
            namespace=namespace,
            session_id=session_id,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )

    async def get_entries(
        self,
        session_id: str,
        *,
        namespace: str = "default",
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[StateEntry, ...]:
        """Retrieve `StateEntry` records for a scope."""
        if limit < 1 or limit > 1000 or offset < 0:
            raise ValidationError("Invalid pagination parameters.", code="INVALID_PAGINATION")
        return await self._repo.get_scope(
            namespace=namespace,
            session_id=session_id,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )

    async def get_scope(
        self,
        session_id: str,
        *,
        namespace: str = "default",
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Retrieve a key -> value dictionary for a scope."""
        entries = await self.get_entries(
            session_id,
            namespace=namespace,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )
        return {entry.key: entry.value for entry in entries}
