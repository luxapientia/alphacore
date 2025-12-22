"""
File-based task repository for persisting and retrieving generated tasks.

Stores tasks in filesystem with separate files for miner and validator views:
- miner.json: Contains only the prompt (what miners see)
- validator.json: Contains full task with invariants (what validators use)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from modules.models import TerraformTask


class FileTaskRepository:
    """
    File-based repository for persisting Terraform tasks.

    Directory structure:
        <base_path>/
            <task_id_1>/
                miner.json      # Prompt only
                validator.json  # Full task with invariants
            <task_id_2>/
                miner.json
                validator.json
    """

    def __init__(self, base_path: str | Path = "."):
        """
        Initialize file-based task repository.

        Args:
            base_path: Root directory for storing tasks. Defaults to current directory.
        """
        self.base_path = Path(base_path).resolve()

    def save(self, task: TerraformTask) -> str:
        """
        Save a task to the filesystem.

        Creates a directory named after the task_id containing:
        - miner.json: Prompt only (what miners receive)
        - validator.json: Full task with invariants (what validators use)

        Args:
            task: TerraformTask instance to save

        Returns:
            task_id of the saved task
        """
        task_id = task.spec.task_id
        task_dir = self.base_path / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        # Miner view: only the prompt
        miner_data = {
            "task_id": task_id,
            "prompt": task.spec.prompt or task.instructions,
            "provider": task.provider,
            "engine": task.engine,
            "validator_sa": task.validator_sa,
        }

        miner_file = task_dir / "miner.json"
        with open(miner_file, 'w') as f:
            json.dump(miner_data, f, indent=2)

        # Validator view: full task with invariants
        validator_data = task.to_dict()

        validator_file = task_dir / "validator.json"
        with open(validator_file, 'w') as f:
            json.dump(validator_data, f, indent=2)

        return task_id

    def get(self, task_id: str) -> Optional[TerraformTask]:
        """
        Retrieve a task by ID from validator.json.

        Args:
            task_id: Unique task identifier

        Returns:
            TerraformTask instance if found, None otherwise
        """
        task_dir = self.base_path / task_id
        validator_file = task_dir / "validator.json"

        if not validator_file.exists():
            return None

        with open(validator_file, 'r') as f:
            data = json.load(f)

        # Reconstruct TerraformTask from validator.json
        from modules.models import TaskSpec, Invariant

        task_data = data.get('task', {})

        invariants = [
            Invariant(
                resource_type=inv['resource_type'],
                match=inv['match']
            )
            for inv in task_data.get('invariants', [])
        ]

        spec = TaskSpec(
            version=task_data.get('version', 'v0'),
            task_id=task_data['task_id'],
            nonce=task_data['nonce'],
            kind=task_data['kind'],
            invariants=invariants,
            prompt=task_data.get('prompt'),
            metadata=task_data.get('metadata')
        )

        task = TerraformTask(
            engine=data['engine'],
            provider=data['provider'],
            validator_sa=data['validator_sa'],
            spec=spec,
            instructions=task_data.get('prompt')
        )

        return task

    def get_miner_view(self, task_id: str) -> Optional[dict]:
        """
        Get the miner view (prompt only) for a task.

        Args:
            task_id: Unique task identifier

        Returns:
            Dictionary with miner data if found, None otherwise
        """
        task_dir = self.base_path / task_id
        miner_file = task_dir / "miner.json"

        if not miner_file.exists():
            return None

        with open(miner_file, 'r') as f:
            return json.load(f)

    def delete(self, task_id: str) -> bool:
        """
        Delete a task and its directory.

        Args:
            task_id: Task identifier

        Returns:
            True if task was deleted, False if not found
        """
        task_dir = self.base_path / task_id

        if not task_dir.exists():
            return False

        # Remove all files in the directory
        for file in task_dir.iterdir():
            file.unlink()

        # Remove the directory
        task_dir.rmdir()

        return True

    def exists(self, task_id: str) -> bool:
        """
        Check if a task exists.

        Args:
            task_id: Task identifier

        Returns:
            True if task directory exists with validator.json
        """
        task_dir = self.base_path / task_id
        validator_file = task_dir / "validator.json"
        return validator_file.exists()


# Singleton instance for global access
_file_repository: Optional[FileTaskRepository] = None


def get_file_task_repository(base_path: Optional[str | Path] = None) -> FileTaskRepository:
    """
    Get the global file task repository instance.

    Args:
        base_path: Optional custom base path for task storage

    Returns:
        FileTaskRepository instance
    """
    global _file_repository
    if _file_repository is None or base_path is not None:
        _file_repository = FileTaskRepository(base_path or ".")
    return _file_repository


def reset_file_task_repository() -> None:
    """Reset the global file task repository instance."""
    global _file_repository
    _file_repository = None
