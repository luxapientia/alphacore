"""
Pytest configuration and shared fixtures for all tests.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the repo root is on `sys.path` so top-level imports work.
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Ensure prompt-generation config is discoverable when tests are invoked from the
# repo root (common local workflow).
default_config = repo_root / "modules" / "task_config.yaml"
if not os.getenv("ALPHACORE_CONFIG") and default_config.exists():
    os.environ["ALPHACORE_CONFIG"] = str(default_config)

from modules.generation.repository import reset_task_repository


def pytest_addoption(parser):
    """Add custom pytest command-line options."""
    parser.addoption(
        "--run-miner-tests",
        action="store_true",
        default=False,
        help="Run miner tests that require full configuration",
    )
    parser.addoption(
        "--run-validator-tests",
        action="store_true",
        default=False,
        help="Run validator tests that require full configuration",
    )
    parser.addoption(
        "--run-integration-tests",
        action="store_true",
        default=False,
        help="Run integration tests that require full infrastructure",
    )
    parser.addoption(
        "--run-infrastructure-tests",
        action="store_true",
        default=False,
        help="Run all infrastructure-dependent tests (miner, validator, integration)",
    )


@pytest.fixture(autouse=True)
def cleanup_task_repository():
    """
    Automatically clean up task repository after each test.

    This ensures tests don't interfere with each other when using
    the global repository instance.
    """
    yield
    reset_task_repository()


@pytest.fixture
def temp_task_db(tmp_path):
    """
    Provide a temporary database path for tests that need persistence.

    Usage:
        def test_with_persistence(temp_task_db):
            repo = TaskRepository(db_path=temp_task_db)
            # ... test code ...
    """
    db_path = str(tmp_path / "test_tasks.db")
    yield db_path
    reset_task_repository()
