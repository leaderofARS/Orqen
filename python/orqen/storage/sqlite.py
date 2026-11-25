"""SQLite persistence backend with WAL mode, thread-affine executor, and repositories."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from orqen.artifacts.models import ArtifactMetadata
from orqen.config.settings import StorageConfig
from orqen.events.models import PersistedEvent
from orqen.exceptions import (
    DatabaseError,
    OrqenError,
    RepositoryError,
    SessionError,
    StateConflictError,
    TaskError,
    ValidationError,
)
from orqen.sessions.lifecycle import SessionState
from orqen.sessions.models import Session
from orqen.state.models import StateEntry, validate_json_value
from orqen.storage.migrations import apply_migrations, check_integrity, get_current_version
from orqen.storage.repositories import (
    ArtifactRepository,
    EventRepository,
    SessionRepository,
    StateRepository,
    TaskRepository,
)
from orqen.tasks.lifecycle import TaskState
from orqen.tasks.models import Task
from orqen.telemetry.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from orqen.events.types import RuntimeEvent

_T = TypeVar("_T")
_logger = get_logger("storage.sqlite")

_ALLOWED_PRAGMAS: frozenset[str] = frozenset(
    {"journal_mode", "foreign_keys", "busy_timeout", "synchronous"}
)


def _utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC).isoformat()
    return dt.astimezone(UTC).isoformat()


def _parse_dt(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _dump_json(value: Any, *, field_name: str) -> str:
    try:
        return json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"Field '{field_name}' is not strictly JSON-serializable: {exc}",
            code="INVALID_JSON_PAYLOAD",
            details={"field": field_name},
            cause=exc,
        ) from exc


class SQLiteDatabase:
    """Async-safe SQLite database connection manager backed by a dedicated worker thread."""

    def __init__(
        self,
        config: StorageConfig | None = None,
        *,
        database_path: Path | str | None = None,
    ) -> None:
        base_config = config or StorageConfig()
        if database_path is not None:
            base_config = base_config.model_copy(update={"database_path": Path(database_path)})
        self.config = base_config
        self._executor: ThreadPoolExecutor | None = None
        self._conn: sqlite3.Connection | None = None
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """Whether the database connection is open and initialized."""
        return self._initialized

    def _connect_and_init_sync(self) -> sqlite3.Connection:
        db_path_str = str(self.config.database_path)
        if db_path_str != ":memory:" and not db_path_str.startswith("file:"):
            Path(db_path_str).parent.mkdir(parents=True, exist_ok=True)

        try:
            conn = sqlite3.connect(
                db_path_str,
                timeout=self.config.connection_timeout_seconds,
                isolation_level=None,
                check_same_thread=True,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(f"PRAGMA busy_timeout = {self.config.busy_timeout_ms}")
            conn.execute(f"PRAGMA journal_mode = {self.config.journal_mode}")
            conn.execute(f"PRAGMA synchronous = {self.config.synchronous}")
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Failed to initialize SQLite database: {exc}",
                code="DATABASE_INIT_FAILED",
                cause=exc,
            ) from exc

        if self.config.auto_migrate:
            apply_migrations(conn)
        check_integrity(conn)
        return conn

    async def initialize(self) -> SQLiteDatabase:
        """Open the SQLite connection, configure pragmas, and apply migrations."""
        if self._initialized:
            return self
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orqen-sqlite")
        loop = asyncio.get_running_loop()
        try:
            self._conn = await loop.run_in_executor(self._executor, self._connect_and_init_sync)
        except BaseException:
            self._executor.shutdown(wait=True)
            self._executor = None
            raise
        self._initialized = True
        _logger.info(
            "SQLite database initialized",
            extra={
                "database_path": str(self.config.database_path),
                "journal_mode": self.config.journal_mode,
            },
        )
        return self

    async def close(self) -> None:
        """Close the SQLite connection and shut down the worker thread."""
        if not self._initialized:
            return
        executor = self._executor
        conn = self._conn
        self._initialized = False
        self._conn = None
        self._executor = None

        if executor is not None:
            if conn is not None:
                loop = asyncio.get_running_loop()

                def _close_sync() -> None:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    conn.close()

                await loop.run_in_executor(executor, _close_sync)
            executor.shutdown(wait=True)
        _logger.info("SQLite database closed")

    async def __aenter__(self) -> SQLiteDatabase:
        return await self.initialize()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def _require_ready(self) -> tuple[ThreadPoolExecutor, sqlite3.Connection]:
        if not self._initialized or self._executor is None or self._conn is None:
            raise DatabaseError(
                "SQLiteDatabase is not initialized. Call 'await db.initialize()' first.",
                code="DATABASE_NOT_INITIALIZED",
            )
        return self._executor, self._conn

    async def execute(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        """Execute a callable on the database worker thread."""
        executor, conn = self._require_ready()
        loop = asyncio.get_running_loop()

        def _wrapper() -> _T:
            try:
                return fn(conn)
            except OrqenError:
                raise
            except sqlite3.Error as exc:
                raise DatabaseError(
                    f"SQLite operation failed: {exc}",
                    code="DATABASE_OPERATION_FAILED",
                    cause=exc,
                ) from exc

        return await loop.run_in_executor(executor, _wrapper)

    async def transaction(
        self,
        fn: Callable[[sqlite3.Connection], _T],
        *,
        immediate: bool = True,
    ) -> _T:
        """Execute `fn(conn)` inside an explicit SQLite transaction."""
        begin_stmt = "BEGIN IMMEDIATE" if immediate else "BEGIN"

        def _tx(conn: sqlite3.Connection) -> _T:
            conn.execute(begin_stmt)
            try:
                result = fn(conn)
                conn.execute("COMMIT")
                return result
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

        return await self.execute(_tx)

    async def schema_version(self) -> int:
        """Return the current applied schema migration version."""
        return await self.execute(get_current_version)

    async def integrity_check(self) -> None:
        """Run `PRAGMA integrity_check` on the database."""
        await self.execute(check_integrity)

    async def pragma_value(self, name: str) -> Any:
        """Return the current value of an allowlisted SQLite PRAGMA."""
        if name not in _ALLOWED_PRAGMAS:
            raise ValidationError(
                f"Unsupported PRAGMA name: {name!r}",
                code="INVALID_PRAGMA",
            )

        def _read_pragma(conn: sqlite3.Connection) -> Any:
            row = conn.execute(f"PRAGMA {name}").fetchone()
            return row[0] if row is not None else None

        return await self.execute(_read_pragma)


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        session_id=str(row["session_id"]),
        title=row["title"],
        agent_id=row["agent_id"],
        status=SessionState(row["status"]),
        metadata=json.loads(row["metadata_json"]),
        revision=int(row["revision"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _row_to_task(row: sqlite3.Row) -> Task:
    error_raw = row["error_json"]
    return Task(
        task_id=str(row["task_id"]),
        session_id=str(row["session_id"]),
        run_id=row["run_id"],
        agent_id=row["agent_id"],
        agent_name=str(row["agent_name"]),
        status=TaskState(row["status"]),
        input=str(row["input"]),
        output=row["output"],
        error=json.loads(error_raw) if error_raw is not None else None,
        metadata=json.loads(row["metadata_json"]),
        created_at=_parse_dt(row["created_at"]),
        started_at=_parse_dt(row["started_at"]) if row["started_at"] is not None else None,
        completed_at=_parse_dt(row["completed_at"]) if row["completed_at"] is not None else None,
        updated_at=_parse_dt(row["updated_at"]),
        duration_seconds=(
            float(row["duration_seconds"]) if row["duration_seconds"] is not None else None
        ),
        revision=int(row["revision"]),
    )


def _row_to_state(row: sqlite3.Row) -> StateEntry:
    return StateEntry(
        namespace=str(row["namespace"]),
        session_id=str(row["session_id"]),
        task_id=row["task_id"],
        key=str(row["key"]),
        value=json.loads(row["value_json"]),
        revision=int(row["revision"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _row_to_event(row: sqlite3.Row) -> PersistedEvent:
    return PersistedEvent(
        event_id=str(row["event_id"]),
        session_id=str(row["session_id"]),
        task_id=row["task_id"],
        run_id=str(row["run_id"]),
        event_type=str(row["event_type"]),
        timestamp=_parse_dt(row["timestamp"]),
        sequence_number=int(row["sequence_number"]),
        payload=json.loads(row["payload_json"]),
        schema_version=int(row["schema_version"]),
    )


def _row_to_artifact(row: sqlite3.Row) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=str(row["artifact_id"]),
        session_id=str(row["session_id"]),
        task_id=row["task_id"],
        filename=str(row["filename"]),
        content_type=str(row["content_type"]),
        size_bytes=int(row["size_bytes"]),
        sha256=str(row["sha256"]),
        storage_key=str(row["storage_key"]),
        created_at=_parse_dt(row["created_at"]),
        metadata=json.loads(row["metadata_json"]),
    )


class SQLiteSessionRepository(SessionRepository):
    """SQLite implementation of `SessionRepository`."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def create(self, session: Session) -> Session:
        meta_json = _dump_json(session.metadata, field_name="metadata")

        def _op(conn: sqlite3.Connection) -> Session:
            try:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        session_id, title, agent_id, status, metadata_json,
                        revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.session_id,
                        session.title,
                        session.agent_id,
                        session.status.value,
                        meta_json,
                        session.revision,
                        _utc_iso(session.created_at),
                        _utc_iso(session.updated_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RepositoryError(
                    f"Session '{session.session_id}' already exists.",
                    code="SESSION_ALREADY_EXISTS",
                    details={"session_id": session.session_id},
                    cause=exc,
                ) from exc
            return session

        return await self._db.transaction(_op)

    async def get(self, session_id: str) -> Session | None:
        def _op(conn: sqlite3.Connection) -> Session | None:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return _row_to_session(row) if row is not None else None

        return await self._db.execute(_op)

    async def update(self, session: Session, *, expected_revision: int) -> Session:
        meta_json = _dump_json(session.metadata, field_name="metadata")

        def _op(conn: sqlite3.Connection) -> Session:
            cursor = conn.execute(
                """
                UPDATE sessions
                SET title = ?,
                    agent_id = ?,
                    status = ?,
                    metadata_json = ?,
                    revision = ?,
                    updated_at = ?
                WHERE session_id = ? AND revision = ?
                """,
                (
                    session.title,
                    session.agent_id,
                    session.status.value,
                    meta_json,
                    session.revision,
                    _utc_iso(session.updated_at),
                    session.session_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount == 0:
                exists = conn.execute(
                    "SELECT revision FROM sessions WHERE session_id = ?",
                    (session.session_id,),
                ).fetchone()
                if exists is None:
                    raise SessionError(
                        f"Session '{session.session_id}' not found.",
                        code="SESSION_NOT_FOUND",
                        details={"session_id": session.session_id},
                    )
                raise RepositoryError(
                    f"Session '{session.session_id}' revision conflict "
                    f"(expected {expected_revision}, stored {exists['revision']}).",
                    code="SESSION_REVISION_CONFLICT",
                    details={
                        "session_id": session.session_id,
                        "expected_revision": expected_revision,
                        "actual_revision": int(exists["revision"]),
                    },
                )
            return session

        return await self._db.transaction(_op)

    async def delete(self, session_id: str) -> bool:
        def _op(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            return cursor.rowcount > 0

        return await self._db.transaction(_op)

    async def list(
        self,
        *,
        status: SessionState | None = None,
        agent_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM sessions {where} "
            "ORDER BY created_at DESC, session_id ASC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        def _op(conn: sqlite3.Connection) -> tuple[Session, ...]:
            rows = conn.execute(sql, params).fetchall()
            return tuple(_row_to_session(row) for row in rows)

        return await self._db.execute(_op)


class SQLiteTaskRepository(TaskRepository):
    """SQLite implementation of `TaskRepository`."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def create(self, task: Task) -> Task:
        meta_json = _dump_json(task.metadata, field_name="metadata")
        error_json = _dump_json(task.error, field_name="error") if task.error is not None else None

        def _op(conn: sqlite3.Connection) -> Task:
            try:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        task_id, session_id, run_id, agent_id, agent_name, status,
                        input, output, error_json, metadata_json,
                        created_at, started_at, completed_at, updated_at,
                        duration_seconds, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.session_id,
                        task.run_id,
                        task.agent_id,
                        task.agent_name,
                        task.status.value,
                        task.input,
                        task.output,
                        error_json,
                        meta_json,
                        _utc_iso(task.created_at),
                        _utc_iso(task.started_at) if task.started_at is not None else None,
                        _utc_iso(task.completed_at) if task.completed_at is not None else None,
                        _utc_iso(task.updated_at),
                        task.duration_seconds,
                        task.revision,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RepositoryError(
                    f"Failed to create task '{task.task_id}' for session "
                    f"'{task.session_id}': {exc}",
                    code="TASK_INTEGRITY_ERROR",
                    details={"task_id": task.task_id, "session_id": task.session_id},
                    cause=exc,
                ) from exc
            return task

        return await self._db.transaction(_op)

    async def get(self, task_id: str) -> Task | None:
        def _op(conn: sqlite3.Connection) -> Task | None:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            return _row_to_task(row) if row is not None else None

        return await self._db.execute(_op)

    async def update(self, task: Task, *, expected_revision: int) -> Task:
        meta_json = _dump_json(task.metadata, field_name="metadata")
        error_json = _dump_json(task.error, field_name="error") if task.error is not None else None

        def _op(conn: sqlite3.Connection) -> Task:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET run_id = ?,
                    status = ?,
                    output = ?,
                    error_json = ?,
                    metadata_json = ?,
                    started_at = ?,
                    completed_at = ?,
                    updated_at = ?,
                    duration_seconds = ?,
                    revision = ?
                WHERE task_id = ? AND revision = ?
                """,
                (
                    task.run_id,
                    task.status.value,
                    task.output,
                    error_json,
                    meta_json,
                    _utc_iso(task.started_at) if task.started_at is not None else None,
                    _utc_iso(task.completed_at) if task.completed_at is not None else None,
                    _utc_iso(task.updated_at),
                    task.duration_seconds,
                    task.revision,
                    task.task_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount == 0:
                exists = conn.execute(
                    "SELECT revision FROM tasks WHERE task_id = ?",
                    (task.task_id,),
                ).fetchone()
                if exists is None:
                    raise TaskError(
                        f"Task '{task.task_id}' not found.",
                        code="TASK_NOT_FOUND",
                        details={"task_id": task.task_id},
                    )
                raise RepositoryError(
                    f"Task '{task.task_id}' revision conflict "
                    f"(expected {expected_revision}, stored {exists['revision']}).",
                    code="TASK_REVISION_CONFLICT",
                    details={
                        "task_id": task.task_id,
                        "expected_revision": expected_revision,
                        "actual_revision": int(exists["revision"]),
                    },
                )
            return task

        return await self._db.transaction(_op)

    async def list(
        self,
        *,
        session_id: str | None = None,
        status: TaskState | None = None,
        agent_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if agent_name is not None:
            clauses.append("agent_name = ?")
            params.append(agent_name)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM tasks {where} ORDER BY created_at ASC, task_id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        def _op(conn: sqlite3.Connection) -> tuple[Task, ...]:
            rows = conn.execute(sql, params).fetchall()
            return tuple(_row_to_task(row) for row in rows)

        return await self._db.execute(_op)

    async def count(
        self,
        *,
        session_id: str | None = None,
        status: TaskState | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT COUNT(*) FROM tasks {where}"

        def _op(conn: sqlite3.Connection) -> int:
            row = conn.execute(sql, params).fetchone()
            return int(row[0]) if row is not None else 0

        return await self._db.execute(_op)


class SQLiteStateRepository(StateRepository):
    """SQLite implementation of `StateRepository` with optimistic revision concurrency."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    @staticmethod
    def _scope_clause(task_id: str | None) -> tuple[str, tuple[Any, ...]]:
        if task_id is None:
            return "task_id IS NULL", ()
        return "task_id = ?", (task_id,)

    async def get(
        self,
        *,
        namespace: str,
        session_id: str,
        key: str,
        task_id: str | None = None,
    ) -> StateEntry | None:
        task_sql, task_params = self._scope_clause(task_id)

        def _op(conn: sqlite3.Connection) -> StateEntry | None:
            row = conn.execute(
                f"""
                SELECT * FROM state_entries
                WHERE namespace = ? AND session_id = ? AND key = ? AND {task_sql}
                """,
                (namespace, session_id, key, *task_params),
            ).fetchone()
            return _row_to_state(row) if row is not None else None

        return await self._db.execute(_op)

    async def set(
        self,
        *,
        namespace: str,
        session_id: str,
        key: str,
        value: Any,
        task_id: str | None = None,
        expected_revision: int | None = None,
    ) -> StateEntry:
        value_json = validate_json_value(value)
        task_sql, task_params = self._scope_clause(task_id)
        now = datetime.now(UTC)
        now_iso = _utc_iso(now)

        def _op(conn: sqlite3.Connection) -> StateEntry:
            existing = conn.execute(
                f"""
                SELECT * FROM state_entries
                WHERE namespace = ? AND session_id = ? AND key = ? AND {task_sql}
                """,
                (namespace, session_id, key, *task_params),
            ).fetchone()

            if existing is None:
                if expected_revision is not None and expected_revision != 0:
                    raise StateConflictError(
                        f"State key '{key}' does not exist "
                        f"(expected revision {expected_revision}).",
                        code="STATE_KEY_MISSING_CONFLICT",
                        details={
                            "namespace": namespace,
                            "session_id": session_id,
                            "task_id": task_id,
                            "key": key,
                            "expected_revision": expected_revision,
                        },
                    )
                try:
                    conn.execute(
                        """
                        INSERT INTO state_entries (
                            namespace, session_id, task_id, key, value_json,
                            revision, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (namespace, session_id, task_id, key, value_json, now_iso, now_iso),
                    )
                except sqlite3.IntegrityError as exc:
                    raise RepositoryError(
                        f"Failed to persist state key '{key}': {exc}",
                        code="STATE_INTEGRITY_ERROR",
                        details={"session_id": session_id, "task_id": task_id, "key": key},
                        cause=exc,
                    ) from exc
                return StateEntry(
                    namespace=namespace,
                    session_id=session_id,
                    task_id=task_id,
                    key=key,
                    value=json.loads(value_json),
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )

            current_rev = int(existing["revision"])
            if expected_revision is not None and current_rev != expected_revision:
                raise StateConflictError(
                    f"State conflict for key '{key}': expected revision {expected_revision}, "
                    f"actual revision {current_rev}.",
                    code="STATE_REVISION_CONFLICT",
                    details={
                        "namespace": namespace,
                        "session_id": session_id,
                        "task_id": task_id,
                        "key": key,
                        "expected_revision": expected_revision,
                        "actual_revision": current_rev,
                    },
                )

            new_rev = current_rev + 1
            conn.execute(
                f"""
                UPDATE state_entries
                SET value_json = ?, revision = ?, updated_at = ?
                WHERE namespace = ? AND session_id = ? AND key = ? AND {task_sql}
                """,
                (value_json, new_rev, now_iso, namespace, session_id, key, *task_params),
            )
            return StateEntry(
                namespace=namespace,
                session_id=session_id,
                task_id=task_id,
                key=key,
                value=json.loads(value_json),
                revision=new_rev,
                created_at=_parse_dt(existing["created_at"]),
                updated_at=now,
            )

        return await self._db.transaction(_op)

    async def delete(
        self,
        *,
        namespace: str,
        session_id: str,
        key: str,
        task_id: str | None = None,
    ) -> bool:
        task_sql, task_params = self._scope_clause(task_id)

        def _op(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                f"""
                DELETE FROM state_entries
                WHERE namespace = ? AND session_id = ? AND key = ? AND {task_sql}
                """,
                (namespace, session_id, key, *task_params),
            )
            return cursor.rowcount > 0

        return await self._db.transaction(_op)

    async def list_keys(
        self,
        *,
        namespace: str,
        session_id: str,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[str, ...]:
        task_sql, task_params = self._scope_clause(task_id)

        def _op(conn: sqlite3.Connection) -> tuple[str, ...]:
            rows = conn.execute(
                f"""
                SELECT key FROM state_entries
                WHERE namespace = ? AND session_id = ? AND {task_sql}
                ORDER BY key ASC LIMIT ? OFFSET ?
                """,
                (namespace, session_id, *task_params, limit, offset),
            ).fetchall()
            return tuple(str(row["key"]) for row in rows)

        return await self._db.execute(_op)

    async def get_scope(
        self,
        *,
        namespace: str,
        session_id: str,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[StateEntry, ...]:
        task_sql, task_params = self._scope_clause(task_id)

        def _op(conn: sqlite3.Connection) -> tuple[StateEntry, ...]:
            rows = conn.execute(
                f"""
                SELECT * FROM state_entries
                WHERE namespace = ? AND session_id = ? AND {task_sql}
                ORDER BY key ASC LIMIT ? OFFSET ?
                """,
                (namespace, session_id, *task_params, limit, offset),
            ).fetchall()
            return tuple(_row_to_state(row) for row in rows)

        return await self._db.execute(_op)


class SQLiteEventRepository(EventRepository):
    """SQLite implementation of `EventRepository` with monotonic session sequence numbers."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def append(
        self,
        *,
        session_id: str,
        task_id: str | None,
        event: RuntimeEvent,
    ) -> PersistedEvent:
        records = await self.append_batch(session_id=session_id, task_id=task_id, events=(event,))
        return records[0]

    async def append_batch(
        self,
        *,
        session_id: str,
        task_id: str | None,
        events: Sequence[RuntimeEvent],
    ) -> tuple[PersistedEvent, ...]:
        if not events:
            return ()

        serialized: list[tuple[RuntimeEvent, str]] = [
            (evt, evt.model_dump_json()) for evt in events
        ]

        def _op(conn: sqlite3.Connection) -> tuple[PersistedEvent, ...]:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence_number), 0) FROM events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            next_seq = (int(row[0]) if row is not None else 0) + 1

            persisted: list[PersistedEvent] = []
            for evt, payload_json in serialized:
                seq = next_seq
                next_seq += 1
                try:
                    conn.execute(
                        """
                        INSERT INTO events (
                            event_id, session_id, task_id, run_id, event_type,
                            timestamp, sequence_number, payload_json, schema_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            evt.event_id,
                            session_id,
                            task_id,
                            evt.run_id,
                            evt.event_type,
                            _utc_iso(evt.timestamp),
                            seq,
                            payload_json,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise RepositoryError(
                        f"Failed to persist event '{evt.event_id}': {exc}",
                        code="EVENT_PERSISTENCE_CONFLICT",
                        details={"event_id": evt.event_id, "session_id": session_id},
                        cause=exc,
                    ) from exc
                persisted.append(
                    PersistedEvent(
                        event_id=evt.event_id,
                        session_id=session_id,
                        task_id=task_id,
                        run_id=evt.run_id,
                        event_type=evt.event_type,
                        timestamp=evt.timestamp,
                        sequence_number=seq,
                        payload=json.loads(payload_json),
                        schema_version=1,
                    )
                )
            return tuple(persisted)

        return await self._db.transaction(_op)

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
    ) -> tuple[PersistedEvent, ...]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if after_sequence is not None:
            clauses.append("sequence_number > ?")
            params.append(after_sequence)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM events {where} ORDER BY sequence_number ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        def _op(conn: sqlite3.Connection) -> tuple[PersistedEvent, ...]:
            rows = conn.execute(sql, params).fetchall()
            return tuple(_row_to_event(row) for row in rows)

        return await self._db.execute(_op)


class SQLiteArtifactRepository(ArtifactRepository):
    """SQLite implementation of `ArtifactRepository`."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def create(self, artifact: ArtifactMetadata) -> ArtifactMetadata:
        meta_json = _dump_json(artifact.metadata, field_name="metadata")

        def _op(conn: sqlite3.Connection) -> ArtifactMetadata:
            try:
                conn.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, session_id, task_id, filename, content_type,
                        size_bytes, sha256, storage_key, created_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact.artifact_id,
                        artifact.session_id,
                        artifact.task_id,
                        artifact.filename,
                        artifact.content_type,
                        artifact.size_bytes,
                        artifact.sha256,
                        artifact.storage_key,
                        _utc_iso(artifact.created_at),
                        meta_json,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RepositoryError(
                    f"Failed to persist artifact '{artifact.artifact_id}': {exc}",
                    code="ARTIFACT_INTEGRITY_ERROR",
                    details={
                        "artifact_id": artifact.artifact_id,
                        "session_id": artifact.session_id,
                    },
                    cause=exc,
                ) from exc
            return artifact

        return await self._db.transaction(_op)

    async def get(self, artifact_id: str) -> ArtifactMetadata | None:
        def _op(conn: sqlite3.Connection) -> ArtifactMetadata | None:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            return _row_to_artifact(row) if row is not None else None

        return await self._db.execute(_op)

    async def delete(self, artifact_id: str) -> bool:
        def _op(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "DELETE FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            )
            return cursor.rowcount > 0

        return await self._db.transaction(_op)

    async def list(
        self,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ArtifactMetadata, ...]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM artifacts {where} "
            "ORDER BY created_at ASC, artifact_id ASC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        def _op(conn: sqlite3.Connection) -> tuple[ArtifactMetadata, ...]:
            rows = conn.execute(sql, params).fetchall()
            return tuple(_row_to_artifact(row) for row in rows)

        return await self._db.execute(_op)


class SQLiteStorage:
    """Unified SQLite persistence facade exposing repositories and database lifecycle."""

    def __init__(
        self,
        config: StorageConfig | None = None,
        *,
        database_path: Path | str | None = None,
    ) -> None:
        self.db = SQLiteDatabase(config=config, database_path=database_path)
        self.sessions = SQLiteSessionRepository(self.db)
        self.tasks = SQLiteTaskRepository(self.db)
        self.state = SQLiteStateRepository(self.db)
        self.events = SQLiteEventRepository(self.db)
        self.artifacts = SQLiteArtifactRepository(self.db)

    async def initialize(self) -> SQLiteStorage:
        """Initialize the underlying SQLite database and apply migrations."""
        await self.db.initialize()
        return self

    async def close(self) -> None:
        """Close the underlying SQLite database."""
        await self.db.close()

    async def __aenter__(self) -> SQLiteStorage:
        return await self.initialize()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()


def new_correlation_id() -> str:
    """Generate a unique correlation identifier."""
    return f"corr_{uuid.uuid4().hex[:12]}"
