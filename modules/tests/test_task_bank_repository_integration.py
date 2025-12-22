"""
Integration tests for TaskRepository with GCPDynamicTaskBank.
"""

import tempfile
from pathlib import Path

import pytest

from modules.generation.repository import TaskRepository, get_task_repository, reset_task_repository
from modules.generation.terraform.providers.gcp.task_bank import GCPDynamicTaskBank


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".db") as f:
        db_path = f.name

    yield db_path

    # Cleanup
    Path(db_path).unlink(missing_ok=True)
    reset_task_repository()


class TestTaskBankWithPersistence:
    """Test GCPDynamicTaskBank with repository pattern."""

    def test_task_bank_persists_tasks(self, temp_db):
        """Test that task bank always persists tasks through repository."""
        # Create repository with temp database
        repo = TaskRepository(db_path=temp_db)

        # Create task bank - always uses repository
        bank = GCPDynamicTaskBank(
            min_resources=1,
            max_resources=2,
        )

        # Override the repository to use our temp database
        bank._repository = repo

        # Generate a task
        task = bank.build_task(validator_sa="test@example.com")

        # Verify task was persisted
        retrieved = repo.get(task.spec.task_id)
        assert retrieved is not None
        assert retrieved.spec.task_id == task.spec.task_id
        assert retrieved.provider == "gcp"

    def test_multiple_tasks_persisted(self, temp_db):
        """Test that multiple tasks are persisted correctly."""
        repo = TaskRepository(db_path=temp_db)

        bank = GCPDynamicTaskBank(
            min_resources=1,
            max_resources=2,
        )
        bank._repository = repo

        # Generate multiple tasks
        task_ids = []
        for i in range(5):
            task = bank.build_task(validator_sa=f"validator-{i}@example.com")
            task_ids.append(task.spec.task_id)

        # Verify all tasks were persisted
        assert repo.count() == 5

        # Verify we can retrieve all tasks
        for task_id in task_ids:
            retrieved = repo.get(task_id)
            assert retrieved is not None

    def test_persisted_task_has_correct_metadata(self, temp_db):
        """Test that persisted tasks have correct metadata."""
        repo = TaskRepository(db_path=temp_db)

        bank = GCPDynamicTaskBank(
            min_resources=2,
            max_resources=3,
        )
        bank._repository = repo

        # Generate a task
        task = bank.build_task(validator_sa="test@example.com")

        # Retrieve and verify metadata
        retrieved = repo.get(task.spec.task_id)
        assert retrieved.spec.metadata is not None
        assert "resource_keys" in retrieved.spec.metadata
        assert "resource_kinds" in retrieved.spec.metadata
        assert len(retrieved.spec.invariants) >= 2

    def test_find_persisted_tasks_by_validator(self, temp_db):
        """Test finding persisted tasks by validator."""
        repo = TaskRepository(db_path=temp_db)

        bank = GCPDynamicTaskBank(
            min_resources=1,
            max_resources=2,
        )
        bank._repository = repo

        validator_sa = "specific-validator@example.com"

        # Generate tasks for specific validator
        for _ in range(3):
            bank.build_task(validator_sa=validator_sa)

        # Generate tasks for other validators
        for i in range(2):
            bank.build_task(validator_sa=f"other-{i}@example.com")

        # Find tasks by specific validator
        validator_tasks = repo.find_by_validator(validator_sa)
        assert len(validator_tasks) == 3
        assert all(t.validator_sa == validator_sa for t in validator_tasks)

    def test_task_status_tracking(self, temp_db):
        """Test tracking task status through lifecycle."""
        repo = TaskRepository(db_path=temp_db)

        bank = GCPDynamicTaskBank(
            min_resources=1,
            max_resources=1,
        )
        bank._repository = repo

        # Generate task
        task = bank.build_task(validator_sa="test@example.com")
        task_id = task.spec.task_id

        # Verify initial status
        assert repo.count(status="created") == 1

        # Assign to miner
        repo.update_status(task_id, "assigned", assigned_to="miner-001")
        assert repo.count(status="assigned") == 1
        assert len(repo.find_by_miner("miner-001")) == 1

        # Complete task
        result = {"score": 0.95, "errors": []}
        repo.update_status(task_id, "completed", result=result)
        assert repo.count(status="completed") == 1

    def test_statistics_after_task_generation(self, temp_db):
        """Test repository statistics after generating tasks."""
        repo = TaskRepository(db_path=temp_db)

        bank = GCPDynamicTaskBank(
            min_resources=1,
            max_resources=2,
        )
        bank._repository = repo

        # Generate tasks
        for _ in range(10):
            bank.build_task(validator_sa="test@example.com")

        # Get statistics
        stats = repo.get_statistics()

        assert stats["total_tasks"] == 10
        assert "gcp" in stats["by_provider"]
        assert stats["by_provider"]["gcp"] == 10
        assert "created" in stats["by_status"]
        assert stats["by_status"]["created"] == 10


class TestGlobalRepositoryInstance:
    """Test global repository instance usage."""

    def test_get_task_repository_creates_instance(self):
        """Test that get_task_repository creates instance."""
        reset_task_repository()
        repo = get_task_repository()
        assert repo is not None

    def test_get_task_repository_returns_singleton(self):
        """Test that get_task_repository returns same instance."""
        reset_task_repository()
        repo1 = get_task_repository()
        repo2 = get_task_repository()
        assert repo1 is repo2

    def test_custom_db_path_creates_new_instance(self, temp_db):
        """Test that custom db_path creates new instance."""
        reset_task_repository()
        repo1 = get_task_repository()
        repo2 = get_task_repository(db_path=temp_db)
        assert repo1 is not repo2
        assert repo2.db_path == temp_db


class TestRepositoryPattern:
    """Test repository pattern behavior."""

    def test_task_bank_always_has_repository(self):
        """Test that task bank always initializes with repository."""
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=2)

        assert bank._repository is not None

    def test_generating_tasks_with_repository(self, temp_db):
        """Test generating tasks always uses repository."""
        repo = TaskRepository(db_path=temp_db)
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=2)
        bank._repository = repo

        # Generate task
        task = bank.build_task(validator_sa="test@example.com")

        assert task is not None
        assert task.spec.task_id is not None
        assert task.provider == "gcp"

        # Verify it was persisted
        retrieved = repo.get(task.spec.task_id)
        assert retrieved is not None
