"""
Tests for FileTaskRepository.
"""

import json
import tempfile
from pathlib import Path

import pytest

from modules.generation.file_repository import (
    FileTaskRepository,
    get_file_task_repository,
    reset_file_task_repository,
)
from modules.models import Invariant, TaskSpec, TerraformTask


@pytest.fixture
def temp_repo_dir():
    """Create a temporary directory for file repository testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir
        reset_file_task_repository()


@pytest.fixture
def sample_task():
    """Create a sample TerraformTask for testing."""
    invariants = [
        Invariant(
            resource_type="google_storage_bucket",
            match={"values.name": "test-bucket", "values.location": "US"}
        )
    ]

    spec = TaskSpec(
        version="v0",
        task_id="test-task-123",
        nonce="abc123",
        kind="google_storage_bucket",
        invariants=invariants,
        prompt="Create a storage bucket named test-bucket in US region.",
        metadata={"resource_keys": ["storage_bucket"], "hints": {}}
    )

    task = TerraformTask(
        engine="terraform",
        provider="gcp",
        validator_sa="validator@example.com",
        spec=spec,
        instructions="Create a storage bucket named test-bucket in US region."
    )

    return task


class TestFileTaskRepositoryBasics:
    """Test basic file repository operations."""

    def test_initialization(self, temp_repo_dir):
        """Test repository initialization."""
        repo = FileTaskRepository(temp_repo_dir)
        assert repo.base_path == Path(temp_repo_dir).resolve()

    def test_save_creates_directory_structure(self, temp_repo_dir, sample_task):
        """Test that save creates proper directory structure."""
        repo = FileTaskRepository(temp_repo_dir)
        task_id = repo.save(sample_task)

        task_dir = Path(temp_repo_dir) / task_id
        assert task_dir.exists()
        assert task_dir.is_dir()

        miner_file = task_dir / "miner.json"
        validator_file = task_dir / "validator.json"

        assert miner_file.exists()
        assert validator_file.exists()

    def test_miner_json_contains_only_prompt(self, temp_repo_dir, sample_task):
        """Test that miner.json contains only the prompt."""
        repo = FileTaskRepository(temp_repo_dir)
        task_id = repo.save(sample_task)

        miner_file = Path(temp_repo_dir) / task_id / "miner.json"
        with open(miner_file, 'r') as f:
            miner_data = json.load(f)

        assert "prompt" in miner_data
        assert "task_id" in miner_data
        assert "provider" in miner_data
        assert "engine" in miner_data
        assert "validator_sa" in miner_data

        # Should NOT contain invariants
        assert "invariants" not in miner_data
        assert "metadata" not in miner_data

    def test_validator_json_contains_full_task(self, temp_repo_dir, sample_task):
        """Test that validator.json contains full task with invariants."""
        repo = FileTaskRepository(temp_repo_dir)
        task_id = repo.save(sample_task)

        validator_file = Path(temp_repo_dir) / task_id / "validator.json"
        with open(validator_file, 'r') as f:
            validator_data = json.load(f)

        assert "engine" in validator_data
        assert "provider" in validator_data
        assert "validator_sa" in validator_data
        assert "task" in validator_data

        task_data = validator_data["task"]
        assert "invariants" in task_data
        assert "metadata" in task_data
        assert len(task_data["invariants"]) == 1

    def test_get_retrieves_task(self, temp_repo_dir, sample_task):
        """Test retrieving a task from repository."""
        repo = FileTaskRepository(temp_repo_dir)
        task_id = repo.save(sample_task)

        retrieved = repo.get(task_id)

        assert retrieved is not None
        assert retrieved.spec.task_id == sample_task.spec.task_id
        assert retrieved.provider == sample_task.provider
        assert retrieved.engine == sample_task.engine
        assert len(retrieved.spec.invariants) == len(sample_task.spec.invariants)

    def test_get_nonexistent_task_returns_none(self, temp_repo_dir):
        """Test getting a task that doesn't exist."""
        repo = FileTaskRepository(temp_repo_dir)
        retrieved = repo.get("nonexistent-task-id")

        assert retrieved is None

    def test_get_miner_view(self, temp_repo_dir, sample_task):
        """Test getting miner view of a task."""
        repo = FileTaskRepository(temp_repo_dir)
        task_id = repo.save(sample_task)

        miner_view = repo.get_miner_view(task_id)

        assert miner_view is not None
        assert miner_view["task_id"] == task_id
        assert "prompt" in miner_view
        assert "invariants" not in miner_view

    def test_delete_removes_task(self, temp_repo_dir, sample_task):
        """Test deleting a task."""
        repo = FileTaskRepository(temp_repo_dir)
        task_id = repo.save(sample_task)

        # Verify task exists
        assert repo.exists(task_id)

        # Delete task
        result = repo.delete(task_id)
        assert result is True

        # Verify task is gone
        assert not repo.exists(task_id)
        task_dir = Path(temp_repo_dir) / task_id
        assert not task_dir.exists()

    def test_delete_nonexistent_task(self, temp_repo_dir):
        """Test deleting a task that doesn't exist."""
        repo = FileTaskRepository(temp_repo_dir)
        result = repo.delete("nonexistent-task-id")

        assert result is False

    def test_exists_checks_task_presence(self, temp_repo_dir, sample_task):
        """Test checking if a task exists."""
        repo = FileTaskRepository(temp_repo_dir)

        assert not repo.exists(sample_task.spec.task_id)

        repo.save(sample_task)

        assert repo.exists(sample_task.spec.task_id)


class TestFileTaskRepositorySingleton:
    """Test singleton repository access."""

    def test_get_file_task_repository_creates_instance(self, temp_repo_dir):
        """Test that get function creates instance."""
        reset_file_task_repository()
        repo = get_file_task_repository(temp_repo_dir)

        assert repo is not None
        assert isinstance(repo, FileTaskRepository)

    def test_get_file_task_repository_returns_singleton(self, temp_repo_dir):
        """Test that get function returns same instance."""
        reset_file_task_repository()
        repo1 = get_file_task_repository(temp_repo_dir)
        repo2 = get_file_task_repository()

        assert repo1 is repo2

    def test_custom_path_creates_new_instance(self, temp_repo_dir):
        """Test that custom path creates new instance."""
        reset_file_task_repository()

        with tempfile.TemporaryDirectory() as tmpdir2:
            repo1 = get_file_task_repository(temp_repo_dir)
            repo2 = get_file_task_repository(tmpdir2)

            assert repo1 is not repo2
            assert str(repo2.base_path) == str(Path(tmpdir2).resolve())


class TestFileTaskRepositoryMultipleTasks:
    """Test repository with multiple tasks."""

    def test_save_multiple_tasks(self, temp_repo_dir):
        """Test saving multiple tasks."""
        repo = FileTaskRepository(temp_repo_dir)

        tasks = []
        for i in range(3):
            spec = TaskSpec(
                version="v0",
                task_id=f"task-{i}",
                nonce=f"nonce-{i}",
                kind="test",
                invariants=[],
                prompt=f"Task {i} prompt"
            )
            task = TerraformTask(
                engine="terraform",
                provider="gcp",
                validator_sa="validator@example.com",
                spec=spec
            )
            tasks.append(task)
            repo.save(task)

        # Verify all tasks exist
        for task in tasks:
            assert repo.exists(task.spec.task_id)
            retrieved = repo.get(task.spec.task_id)
            assert retrieved is not None

    def test_directory_isolation(self, temp_repo_dir):
        """Test that tasks are isolated in separate directories."""
        repo = FileTaskRepository(temp_repo_dir)

        task1_spec = TaskSpec(
            version="v0",
            task_id="task-1",
            nonce="nonce-1",
            kind="test",
            invariants=[],
            prompt="Task 1"
        )
        task1 = TerraformTask(
            engine="terraform",
            provider="gcp",
            validator_sa="validator@example.com",
            spec=task1_spec
        )

        task2_spec = TaskSpec(
            version="v0",
            task_id="task-2",
            nonce="nonce-2",
            kind="test",
            invariants=[],
            prompt="Task 2"
        )
        task2 = TerraformTask(
            engine="terraform",
            provider="gcp",
            validator_sa="validator@example.com",
            spec=task2_spec
        )

        repo.save(task1)
        repo.save(task2)

        # Verify separate directories
        task1_dir = Path(temp_repo_dir) / "task-1"
        task2_dir = Path(temp_repo_dir) / "task-2"

        assert task1_dir.exists()
        assert task2_dir.exists()
        assert task1_dir != task2_dir
