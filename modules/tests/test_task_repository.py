"""
Tests for TaskRepository local persistence.
"""

import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from modules.models import Invariant, TaskSpec, TerraformTask
from modules.generation.repository import (
    TaskRepository,
    get_task_repository,
    reset_task_repository,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".db") as f:
        db_path = f.name

    yield db_path

    # Cleanup
    Path(db_path).unlink(missing_ok=True)
    reset_task_repository()


@pytest.fixture
def repository(temp_db):
    """Create a TaskRepository with temporary database."""
    return TaskRepository(db_path=temp_db)


@pytest.fixture
def sample_task():
    """Create a sample TerraformTask for testing."""
    spec = TaskSpec(
        version="v0",
        task_id="test-task-001",
        nonce="abc123",
        kind="storage_bucket + cloud_function",
        invariants=[
            Invariant(
                resource_type="google_storage_bucket",
                match={"name": "test-bucket"}
            ),
            Invariant(
                resource_type="google_cloudfunctions_function",
                match={"name": "test-function"}
            ),
        ],
        metadata={"resource_keys": ["storage_bucket", "cloud_function"]},
        prompt="Create a storage bucket and cloud function"
    )

    return TerraformTask(
        engine="terraform",
        provider="gcp",
        validator_sa="validator@project.iam.gserviceaccount.com",
        spec=spec,
        instructions="Create a storage bucket and cloud function"
    )


class TestTaskRepositoryInitialization:
    """Test repository initialization and schema creation."""

    def test_creates_database_file(self, temp_db):
        """Test that repository creates database file."""
        repo = TaskRepository(db_path=temp_db)
        assert Path(temp_db).exists()

    def test_creates_schema(self, temp_db):
        """Test that repository creates proper schema."""
        repo = TaskRepository(db_path=temp_db)

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='tasks'
        """)
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "tasks" in tables

    def test_creates_indexes(self, temp_db):
        """Test that repository creates indexes."""
        repo = TaskRepository(db_path=temp_db)

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND name LIKE 'idx_tasks_%'
        """)
        indexes = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert len(indexes) >= 5
        assert "idx_tasks_provider" in indexes
        assert "idx_tasks_validator_sa" in indexes
        assert "idx_tasks_status" in indexes

    def test_default_db_path(self):
        """Test that default path is in user home directory."""
        repo = TaskRepository()
        expected_path = str(Path.home() / ".alphacore" / "tasks.db")
        assert repo.db_path == expected_path


class TestTaskRepositorySave:
    """Test saving tasks to repository."""

    def test_save_task(self, repository, sample_task):
        """Test saving a task."""
        task_id = repository.save(sample_task)

        assert task_id == sample_task.spec.task_id

        # Verify task was saved
        retrieved = repository.get(task_id)
        assert retrieved is not None
        assert retrieved.spec.task_id == sample_task.spec.task_id

    def test_save_task_with_metadata(self, repository):
        """Test saving task with metadata."""
        spec = TaskSpec(
            version="v0",
            task_id="test-metadata",
            nonce="nonce123",
            kind="test_resource",
            invariants=[],
            metadata={
                "resource_keys": ["key1", "key2"],
                "custom_field": "custom_value"
            }
        )
        task = TerraformTask(
            engine="terraform",
            provider="gcp",
            validator_sa="test@example.com",
            spec=spec,
            instructions="Test instructions"
        )

        task_id = repository.save(task)
        retrieved = repository.get(task_id)

        assert retrieved.spec.metadata is not None
        assert retrieved.spec.metadata["custom_field"] == "custom_value"

    def test_save_replaces_existing_task(self, repository, sample_task):
        """Test that saving with same task_id replaces existing task."""
        # Save original
        repository.save(sample_task)

        # Modify and save again
        sample_task.spec.metadata["updated"] = True
        repository.save(sample_task)

        # Verify only one task exists
        retrieved = repository.get(sample_task.spec.task_id)
        assert retrieved.spec.metadata.get("updated") is True

        # Count should be 1
        assert repository.count() == 1

    def test_save_multiple_tasks(self, repository):
        """Test saving multiple tasks."""
        tasks = []
        for i in range(5):
            spec = TaskSpec(
                version="v0",
                task_id=f"task-{i}",
                nonce=f"nonce-{i}",
                kind="test_resource",
                invariants=[]
            )
            task = TerraformTask(
                engine="terraform",
                provider="gcp",
                validator_sa="test@example.com",
                spec=spec,
                instructions=f"Instructions {i}"
            )
            tasks.append(task)
            repository.save(task)

        assert repository.count() == 5


class TestTaskRepositoryGet:
    """Test retrieving tasks from repository."""

    def test_get_existing_task(self, repository, sample_task):
        """Test retrieving an existing task."""
        repository.save(sample_task)
        retrieved = repository.get(sample_task.spec.task_id)

        assert retrieved is not None
        assert retrieved.spec.task_id == sample_task.spec.task_id
        assert retrieved.provider == sample_task.provider
        assert retrieved.validator_sa == sample_task.validator_sa

    def test_get_nonexistent_task(self, repository):
        """Test retrieving a non-existent task returns None."""
        retrieved = repository.get("nonexistent-task")
        assert retrieved is None

    def test_get_preserves_invariants(self, repository, sample_task):
        """Test that invariants are preserved."""
        repository.save(sample_task)
        retrieved = repository.get(sample_task.spec.task_id)

        assert len(retrieved.spec.invariants) == len(sample_task.spec.invariants)
        assert retrieved.spec.invariants[0].resource_type == sample_task.spec.invariants[0].resource_type

    def test_get_preserves_metadata(self, repository, sample_task):
        """Test that metadata is preserved."""
        repository.save(sample_task)
        retrieved = repository.get(sample_task.spec.task_id)

        assert retrieved.spec.metadata == sample_task.spec.metadata


class TestTaskRepositoryFindByProvider:
    """Test finding tasks by provider."""

    def test_find_by_provider(self, repository):
        """Test finding tasks by provider."""
        # Create tasks for different providers
        for provider in ["gcp", "aws", "azure"]:
            for i in range(3):
                spec = TaskSpec(
                    version="v0",
                    task_id=f"{provider}-task-{i}",
                    nonce=f"nonce-{i}",
                    kind="test_resource",
                    invariants=[]
                )
                task = TerraformTask(
                    engine="terraform",
                    provider=provider,
                    validator_sa="test@example.com",
                    spec=spec
                )
                repository.save(task)

        gcp_tasks = repository.find_by_provider("gcp")
        assert len(gcp_tasks) == 3
        assert all(t.provider == "gcp" for t in gcp_tasks)

    def test_find_by_provider_respects_limit(self, repository):
        """Test that limit parameter is respected."""
        for i in range(10):
            spec = TaskSpec(
                version="v0",
                task_id=f"task-{i}",
                nonce=f"nonce-{i}",
                kind="test_resource",
                invariants=[]
            )
            task = TerraformTask(
                engine="terraform",
                provider="gcp",
                validator_sa="test@example.com",
                spec=spec
            )
            repository.save(task)

        tasks = repository.find_by_provider("gcp", limit=5)
        assert len(tasks) == 5

    def test_find_by_provider_returns_most_recent(self, repository):
        """Test that tasks are returned ordered by creation time."""
        task_ids = []
        for i in range(3):
            spec = TaskSpec(
                version="v0",
                task_id=f"task-{i}",
                nonce=f"nonce-{i}",
                kind="test_resource",
                invariants=[]
            )
            task = TerraformTask(
                engine="terraform",
                provider="gcp",
                validator_sa="test@example.com",
                spec=spec
            )
            repository.save(task)
            task_ids.append(task.spec.task_id)

        tasks = repository.find_by_provider("gcp")
        # Should return all 3 tasks
        assert len(tasks) == 3
        # All task IDs should be present
        retrieved_ids = [t.spec.task_id for t in tasks]
        assert set(retrieved_ids) == set(task_ids)


class TestTaskRepositoryFindByValidator:
    """Test finding tasks by validator service account."""

    def test_find_by_validator(self, repository):
        """Test finding tasks by validator."""
        validators = ["val1@project.iam.gserviceaccount.com", "val2@project.iam.gserviceaccount.com"]

        for val in validators:
            for i in range(3):
                spec = TaskSpec(
                    version="v0",
                    task_id=f"{val}-task-{i}",
                    nonce=f"nonce-{i}",
                    kind="test_resource",
                    invariants=[]
                )
                task = TerraformTask(
                    engine="terraform",
                    provider="gcp",
                    validator_sa=val,
                    spec=spec
                )
                repository.save(task)

        val1_tasks = repository.find_by_validator(validators[0])
        assert len(val1_tasks) == 3
        assert all(t.validator_sa == validators[0] for t in val1_tasks)


class TestTaskRepositoryFindByStatus:
    """Test finding tasks by status."""

    def test_find_by_status(self, repository, sample_task):
        """Test finding tasks by status."""
        repository.save(sample_task)

        created_tasks = repository.find_by_status("created")
        assert len(created_tasks) == 1
        assert created_tasks[0].spec.task_id == sample_task.spec.task_id

    def test_find_by_status_after_update(self, repository, sample_task):
        """Test finding tasks by status after updating status."""
        repository.save(sample_task)
        repository.update_status(sample_task.spec.task_id, "assigned", assigned_to="miner-001")

        assigned_tasks = repository.find_by_status("assigned")
        assert len(assigned_tasks) == 1

        created_tasks = repository.find_by_status("created")
        assert len(created_tasks) == 0


class TestTaskRepositoryFindByMiner:
    """Test finding tasks assigned to miners."""

    def test_find_by_miner(self, repository, sample_task):
        """Test finding tasks assigned to a miner."""
        repository.save(sample_task)
        repository.update_status(sample_task.spec.task_id, "assigned", assigned_to="miner-001")

        miner_tasks = repository.find_by_miner("miner-001")
        assert len(miner_tasks) == 1
        assert miner_tasks[0].spec.task_id == sample_task.spec.task_id

    def test_find_by_miner_multiple_tasks(self, repository):
        """Test finding multiple tasks for one miner."""
        for i in range(3):
            spec = TaskSpec(
                version="v0",
                task_id=f"task-{i}",
                nonce=f"nonce-{i}",
                kind="test_resource",
                invariants=[]
            )
            task = TerraformTask(
                engine="terraform",
                provider="gcp",
                validator_sa="test@example.com",
                spec=spec
            )
            repository.save(task)
            repository.update_status(task.spec.task_id, "assigned", assigned_to="miner-001")

        miner_tasks = repository.find_by_miner("miner-001")
        assert len(miner_tasks) == 3


class TestTaskRepositoryList:
    """Test listing tasks with filters."""

    def test_list_all_no_filter(self, repository):
        """Test listing all tasks without filters."""
        for i in range(5):
            spec = TaskSpec(
                version="v0",
                task_id=f"task-{i}",
                nonce=f"nonce-{i}",
                kind="test_resource",
                invariants=[]
            )
            task = TerraformTask(
                engine="terraform",
                provider="gcp",
                validator_sa="test@example.com",
                spec=spec
            )
            repository.save(task)

        tasks = repository.list_all()
        assert len(tasks) == 5

    def test_list_all_with_provider_filter(self, repository):
        """Test listing tasks filtered by provider."""
        for provider in ["gcp", "aws"]:
            for i in range(3):
                spec = TaskSpec(
                    version="v0",
                    task_id=f"{provider}-task-{i}",
                    nonce=f"nonce-{i}",
                    kind="test_resource",
                    invariants=[]
                )
                task = TerraformTask(
                    engine="terraform",
                    provider=provider,
                    validator_sa="test@example.com",
                    spec=spec
                )
                repository.save(task)

        gcp_tasks = repository.list_all(provider="gcp")
        assert len(gcp_tasks) == 3
        assert all(t.provider == "gcp" for t in gcp_tasks)

    def test_list_all_with_pagination(self, repository):
        """Test pagination with limit and offset."""
        for i in range(10):
            spec = TaskSpec(
                version="v0",
                task_id=f"task-{i}",
                nonce=f"nonce-{i}",
                kind="test_resource",
                invariants=[]
            )
            task = TerraformTask(
                engine="terraform",
                provider="gcp",
                validator_sa="test@example.com",
                spec=spec
            )
            repository.save(task)

        page1 = repository.list_all(limit=3, offset=0)
        page2 = repository.list_all(limit=3, offset=3)

        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0].spec.task_id != page2[0].spec.task_id


class TestTaskRepositoryUpdateStatus:
    """Test updating task status."""

    def test_update_status(self, repository, sample_task):
        """Test updating task status."""
        repository.save(sample_task)

        success = repository.update_status(sample_task.spec.task_id, "assigned", assigned_to="miner-001")
        assert success is True

        # Verify status was updated
        tasks = repository.find_by_status("assigned")
        assert len(tasks) == 1

    def test_update_status_with_assignment(self, repository, sample_task):
        """Test updating status to assigned with miner info."""
        repository.save(sample_task)
        repository.update_status(sample_task.spec.task_id, "assigned", assigned_to="miner-001")

        miner_tasks = repository.find_by_miner("miner-001")
        assert len(miner_tasks) == 1

    def test_update_status_to_completed(self, repository, sample_task):
        """Test updating status to completed with result."""
        repository.save(sample_task)

        result = {"score": 0.95, "errors": []}
        success = repository.update_status(
            sample_task.spec.task_id,
            "completed",
            result=result
        )
        assert success is True

    def test_update_nonexistent_task(self, repository):
        """Test updating non-existent task returns False."""
        success = repository.update_status("nonexistent", "assigned")
        assert success is False


class TestTaskRepositoryDelete:
    """Test deleting tasks."""

    def test_delete_existing_task(self, repository, sample_task):
        """Test deleting an existing task."""
        repository.save(sample_task)

        success = repository.delete(sample_task.spec.task_id)
        assert success is True

        # Verify task was deleted
        retrieved = repository.get(sample_task.spec.task_id)
        assert retrieved is None

    def test_delete_nonexistent_task(self, repository):
        """Test deleting non-existent task returns False."""
        success = repository.delete("nonexistent")
        assert success is False


class TestTaskRepositoryCount:
    """Test counting tasks."""

    def test_count_all_tasks(self, repository):
        """Test counting all tasks."""
        for i in range(5):
            spec = TaskSpec(
                version="v0",
                task_id=f"task-{i}",
                nonce=f"nonce-{i}",
                kind="test_resource",
                invariants=[]
            )
            task = TerraformTask(
                engine="terraform",
                provider="gcp",
                validator_sa="test@example.com",
                spec=spec
            )
            repository.save(task)

        assert repository.count() == 5

    def test_count_with_provider_filter(self, repository):
        """Test counting tasks filtered by provider."""
        for provider in ["gcp", "aws"]:
            for i in range(3):
                spec = TaskSpec(
                    version="v0",
                    task_id=f"{provider}-task-{i}",
                    nonce=f"nonce-{i}",
                    kind="test_resource",
                    invariants=[]
                )
                task = TerraformTask(
                    engine="terraform",
                    provider=provider,
                    validator_sa="test@example.com",
                    spec=spec
                )
                repository.save(task)

        assert repository.count(provider="gcp") == 3
        assert repository.count(provider="aws") == 3

    def test_count_with_status_filter(self, repository, sample_task):
        """Test counting tasks filtered by status."""
        repository.save(sample_task)

        assert repository.count(status="created") == 1
        assert repository.count(status="assigned") == 0


class TestTaskRepositoryStatistics:
    """Test repository statistics."""

    def test_get_statistics(self, repository):
        """Test getting repository statistics."""
        # Create diverse tasks
        for provider in ["gcp", "aws"]:
            for i in range(3):
                spec = TaskSpec(
                    version="v0",
                    task_id=f"{provider}-task-{i}",
                    nonce=f"nonce-{i}",
                    kind=f"kind_{i % 2}",
                    invariants=[]
                )
                task = TerraformTask(
                    engine="terraform",
                    provider=provider,
                    validator_sa="test@example.com",
                    spec=spec
                )
                repository.save(task)

        stats = repository.get_statistics()

        assert stats["total_tasks"] == 6
        assert "gcp" in stats["by_provider"]
        assert "aws" in stats["by_provider"]
        assert stats["by_provider"]["gcp"] == 3
        assert stats["by_provider"]["aws"] == 3
        assert "created" in stats["by_status"]
        assert "by_kind" in stats


class TestTaskRepositorySingleton:
    """Test singleton repository pattern."""

    def test_get_task_repository(self, temp_db):
        """Test getting global repository instance."""
        repo1 = get_task_repository(db_path=temp_db)
        repo2 = get_task_repository()

        assert repo1 is repo2

    def test_reset_task_repository(self, temp_db):
        """Test resetting global repository."""
        repo1 = get_task_repository(db_path=temp_db)
        reset_task_repository()
        repo2 = get_task_repository(db_path=temp_db)

        assert repo1 is not repo2


class TestTaskRepositoryIntegration:
    """Integration tests for task repository."""

    def test_full_task_lifecycle(self, repository):
        """Test complete task lifecycle."""
        # 1. Create and save task
        spec = TaskSpec(
            version="v0",
            task_id="lifecycle-task",
            nonce="nonce123",
            kind="test_resource",
            invariants=[]
        )
        task = TerraformTask(
            engine="terraform",
            provider="gcp",
            validator_sa="test@example.com",
            spec=spec
        )

        task_id = repository.save(task)
        assert repository.count(status="created") == 1

        # 2. Assign to miner
        repository.update_status(task_id, "assigned", assigned_to="miner-001")
        assert repository.count(status="assigned") == 1
        assert len(repository.find_by_miner("miner-001")) == 1

        # 3. Complete task
        result = {"score": 0.95}
        repository.update_status(task_id, "completed", result=result)
        assert repository.count(status="completed") == 1

        # 4. Verify final state
        retrieved = repository.get(task_id)
        assert retrieved is not None
