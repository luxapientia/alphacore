"""
Task repository for persisting and retrieving generated tasks.

Provides a simple interface for storing tasks locally with support
for querying by various criteria.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from contextlib import contextmanager

from modules.models import TerraformTask, TaskSpec, Invariant


class TaskRepository:
    """
    Repository for persisting and retrieving Terraform tasks.

    Uses SQLite for local storage with support for:
    - Task CRUD operations
    - Querying by task_id, provider, validator_sa
    - Task history and audit trail
    - Status tracking
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize task repository.

        Args:
            db_path: Path to SQLite database file. If None, uses default location
                    in ~/.alphacore/tasks.db
        """
        if db_path is None:
            db_path = str(Path.home() / ".alphacore" / "tasks.db")

        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    nonce TEXT NOT NULL,
                    version TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    validator_sa TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    invariants TEXT NOT NULL,
                    metadata TEXT,
                    instructions TEXT,
                    status TEXT DEFAULT 'created',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    assigned_to TEXT,
                    assigned_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    result TEXT
                )
            """)

            # Create indexes for common queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_provider
                ON tasks(provider)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_validator_sa
                ON tasks(validator_sa)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_status
                ON tasks(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to
                ON tasks(assigned_to)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_created_at
                ON tasks(created_at)
            """)

            conn.commit()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def save(self, task: TerraformTask) -> str:
        """
        Save a task to the repository.

        Args:
            task: TerraformTask instance to save

        Returns:
            task_id of the saved task
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO tasks (
                    task_id, nonce, version, engine, provider, validator_sa,
                    kind, invariants, metadata, instructions, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                task.spec.task_id,
                task.spec.nonce,
                task.spec.version,
                task.engine,
                task.provider,
                task.validator_sa,
                task.spec.kind,
                json.dumps([{
                    "resource_type": inv.resource_type,
                    "match": inv.match
                } for inv in task.spec.invariants]),
                json.dumps(task.spec.metadata) if task.spec.metadata else None,
                task.spec.prompt or task.instructions,
            ))
            conn.commit()

        return task.spec.task_id

    def get(self, task_id: str) -> Optional[TerraformTask]:
        """
        Retrieve a task by ID.

        Args:
            task_id: Unique task identifier

        Returns:
            TerraformTask instance if found, None otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM tasks WHERE task_id = ?
            """, (task_id,))
            row = cursor.fetchone()

            if row is None:
                return None

            return self._row_to_task(row)

    def find_by_provider(self, provider: str, limit: int = 100) -> List[TerraformTask]:
        """
        Find tasks by provider.

        Args:
            provider: Provider name (e.g., "gcp", "aws", "azure")
            limit: Maximum number of tasks to return

        Returns:
            List of matching tasks
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM tasks
                WHERE provider = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (provider, limit))

            return [self._row_to_task(row) for row in cursor.fetchall()]

    def find_by_validator(self, validator_sa: str, limit: int = 100) -> List[TerraformTask]:
        """
        Find tasks by validator service account.

        Args:
            validator_sa: Validator service account email
            limit: Maximum number of tasks to return

        Returns:
            List of matching tasks
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM tasks
                WHERE validator_sa = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (validator_sa, limit))

            return [self._row_to_task(row) for row in cursor.fetchall()]

    def find_by_status(self, status: str, limit: int = 100) -> List[TerraformTask]:
        """
        Find tasks by status.

        Args:
            status: Task status (e.g., "created", "assigned", "completed", "failed")
            limit: Maximum number of tasks to return

        Returns:
            List of matching tasks
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM tasks
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (status, limit))

            return [self._row_to_task(row) for row in cursor.fetchall()]

    def find_by_miner(self, miner_uid: str, limit: int = 100) -> List[TerraformTask]:
        """
        Find tasks assigned to a specific miner.

        Args:
            miner_uid: Miner unique identifier
            limit: Maximum number of tasks to return

        Returns:
            List of matching tasks
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM tasks
                WHERE assigned_to = ?
                ORDER BY assigned_at DESC
                LIMIT ?
            """, (miner_uid, limit))

            return [self._row_to_task(row) for row in cursor.fetchall()]

    def list_all(
        self,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[TerraformTask]:
        """
        List tasks with optional filtering.

        Args:
            provider: Optional provider filter
            status: Optional status filter
            limit: Maximum number of tasks to return
            offset: Number of tasks to skip

        Returns:
            List of matching tasks
        """
        query = "SELECT * FROM tasks WHERE 1=1"
        params: List[Any] = []

        if provider:
            query += " AND provider = ?"
            params.append(provider)

        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_task(row) for row in cursor.fetchall()]

    def update_status(
        self,
        task_id: str,
        status: str,
        assigned_to: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Update task status and related fields.

        Args:
            task_id: Task identifier
            status: New status value
            assigned_to: Optional miner UID (for "assigned" status)
            result: Optional task result data (for "completed" status)

        Returns:
            True if task was updated, False if not found
        """
        with self._get_connection() as conn:
            # Build dynamic query based on provided fields
            updates = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
            params = [status]

            if status == "assigned" and assigned_to:
                updates.append("assigned_to = ?")
                updates.append("assigned_at = CURRENT_TIMESTAMP")
                params.append(assigned_to)

            if status == "completed":
                updates.append("completed_at = CURRENT_TIMESTAMP")
                if result:
                    updates.append("result = ?")
                    params.append(json.dumps(result))

            params.append(task_id)

            cursor = conn.execute(f"""
                UPDATE tasks
                SET {", ".join(updates)}
                WHERE task_id = ?
            """, params)

            conn.commit()
            return cursor.rowcount > 0

    def delete(self, task_id: str) -> bool:
        """
        Delete a task from the repository.

        Args:
            task_id: Task identifier

        Returns:
            True if task was deleted, False if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                DELETE FROM tasks WHERE task_id = ?
            """, (task_id,))
            conn.commit()
            return cursor.rowcount > 0

    def count(
        self,
        provider: Optional[str] = None,
        status: Optional[str] = None
    ) -> int:
        """
        Count tasks with optional filtering.

        Args:
            provider: Optional provider filter
            status: Optional status filter

        Returns:
            Number of matching tasks
        """
        query = "SELECT COUNT(*) as count FROM tasks WHERE 1=1"
        params: List[Any] = []

        if provider:
            query += " AND provider = ?"
            params.append(provider)

        if status:
            query += " AND status = ?"
            params.append(status)

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            return row["count"] if row else 0

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get repository statistics.

        Returns:
            Dictionary with statistics about stored tasks
        """
        with self._get_connection() as conn:
            stats = {
                "total_tasks": 0,
                "by_provider": {},
                "by_status": {},
                "by_kind": {},
            }

            # Total tasks
            cursor = conn.execute("SELECT COUNT(*) as count FROM tasks")
            row = cursor.fetchone()
            stats["total_tasks"] = row["count"] if row else 0

            # By provider
            cursor = conn.execute("""
                SELECT provider, COUNT(*) as count
                FROM tasks
                GROUP BY provider
            """)
            stats["by_provider"] = {row["provider"]: row["count"] for row in cursor.fetchall()}

            # By status
            cursor = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM tasks
                GROUP BY status
            """)
            stats["by_status"] = {row["status"]: row["count"] for row in cursor.fetchall()}

            # By kind
            cursor = conn.execute("""
                SELECT kind, COUNT(*) as count
                FROM tasks
                GROUP BY kind
                ORDER BY count DESC
                LIMIT 10
            """)
            stats["by_kind"] = {row["kind"]: row["count"] for row in cursor.fetchall()}

            return stats

    def _row_to_task(self, row: sqlite3.Row) -> TerraformTask:
        """Convert database row to TerraformTask instance."""
        invariants_data = json.loads(row["invariants"])
        invariants = [
            Invariant(
                resource_type=inv["resource_type"],
                match=inv["match"]
            )
            for inv in invariants_data
        ]

        metadata = json.loads(row["metadata"]) if row["metadata"] else None

        spec = TaskSpec(
            version=row["version"],
            task_id=row["task_id"],
            nonce=row["nonce"],
            kind=row["kind"],
            invariants=invariants,
            prompt=row["instructions"],
            metadata=metadata
        )

        task = TerraformTask(
            engine=row["engine"],
            provider=row["provider"],
            validator_sa=row["validator_sa"],
            spec=spec,
            instructions=row["instructions"]
        )

        return task


# Singleton instance for global access
_repository: Optional[TaskRepository] = None


def get_task_repository(db_path: Optional[str] = None) -> TaskRepository:
    """
    Get the global task repository instance.

    Args:
        db_path: Optional custom database path

    Returns:
        TaskRepository instance
    """
    global _repository
    if _repository is None or db_path is not None:
        _repository = TaskRepository(db_path)
    return _repository


def reset_task_repository() -> None:
    """Reset the global task repository instance."""
    global _repository
    _repository = None
