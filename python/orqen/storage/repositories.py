"""Abstract repository interfaces decoupling domain logic from storage backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from orqen.artifacts.models import ArtifactMetadata
    from orqen.events.models import PersistedEvent
    from orqen.events.types import RuntimeEvent
    from orqen.sessions.lifecycle import SessionState
    from orqen.sessions.models import Session
    from orqen.state.models import StateEntry
    from orqen.tasks.lifecycle import TaskState
    from orqen.tasks.models import Task


class SessionRepository(ABC):
    """Persistence contract for `Session` entities."""

    @abstractmethod
    async def create(self, session: Session) -> Session: ...

    @abstractmethod
    async def get(self, session_id: str) -> Session | None: ...

    @abstractmethod
    async def update(self, session: Session, *, expected_revision: int) -> Session: ...

    @abstractmethod
    async def delete(self, session_id: str) -> bool: ...

    @abstractmethod
    async def list(
        self,
        *,
        status: SessionState | None = None,
        agent_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Session, ...]: ...


class TaskRepository(ABC):
    """Persistence contract for `Task` entities."""

    @abstractmethod
    async def create(self, task: Task) -> Task: ...

    @abstractmethod
    async def get(self, task_id: str) -> Task | None: ...

    @abstractmethod
    async def update(self, task: Task, *, expected_revision: int) -> Task: ...

    @abstractmethod
    async def list(
        self,
        *,
        session_id: str | None = None,
        status: TaskState | None = None,
        agent_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Task, ...]: ...

    @abstractmethod
    async def count(
        self,
        *,
        session_id: str | None = None,
        status: TaskState | None = None,
    ) -> int: ...


class StateRepository(ABC):
    """Persistence contract for revisioned, scoped key/value state."""

    @abstractmethod
    async def get(
        self,
        *,
        namespace: str,
        session_id: str,
        key: str,
        task_id: str | None = None,
    ) -> StateEntry | None: ...

    @abstractmethod
    async def set(
        self,
        *,
        namespace: str,
        session_id: str,
        key: str,
        value: Any,
        task_id: str | None = None,
        expected_revision: int | None = None,
    ) -> StateEntry: ...

    @abstractmethod
    async def delete(
        self,
        *,
        namespace: str,
        session_id: str,
        key: str,
        task_id: str | None = None,
    ) -> bool: ...

    @abstractmethod
    async def list_keys(
        self,
        *,
        namespace: str,
        session_id: str,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[str, ...]: ...

    @abstractmethod
    async def get_scope(
        self,
        *,
        namespace: str,
        session_id: str,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[StateEntry, ...]: ...


class EventRepository(ABC):
    """Persistence contract for sequence-ordered runtime events."""

    @abstractmethod
    async def append(
        self,
        *,
        session_id: str,
        task_id: str | None,
        event: RuntimeEvent,
    ) -> PersistedEvent: ...

    @abstractmethod
    async def append_batch(
        self,
        *,
        session_id: str,
        task_id: str | None,
        events: Sequence[RuntimeEvent],
    ) -> tuple[PersistedEvent, ...]: ...

    @abstractmethod
    async def list(
        self,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[PersistedEvent, ...]: ...


class ArtifactRepository(ABC):
    """Persistence contract for artifact metadata."""

    @abstractmethod
    async def create(self, artifact: ArtifactMetadata) -> ArtifactMetadata: ...

    @abstractmethod
    async def get(self, artifact_id: str) -> ArtifactMetadata | None: ...

    @abstractmethod
    async def delete(self, artifact_id: str) -> bool: ...

    @abstractmethod
    async def list(
        self,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ArtifactMetadata, ...]: ...
