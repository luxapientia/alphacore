"""Task generation and instruction generation.

This package contains components that may require runtime configuration (e.g.
`ALPHACORE_CONFIG`) at import time. To keep imports lightweight and avoid
surprising side effects, public symbols are exposed via lazy attribute access.
"""

from __future__ import annotations

from typing import Any

__all__ = (
    "TaskGenerationPipeline",
    "TaskInstructionGenerator",
    "TaskGenerator",
    "generate_task",
    "FileTaskRepository",
    "get_file_task_repository",
)


def __getattr__(name: str) -> Any:  # pragma: no cover
    if name == "TaskGenerationPipeline":
        from .pipeline import TaskGenerationPipeline

        return TaskGenerationPipeline
    if name == "TaskInstructionGenerator":
        from .instructions import TaskInstructionGenerator

        return TaskInstructionGenerator
    if name == "TaskGenerator":
        from .generator import TaskGenerator

        return TaskGenerator
    if name == "generate_task":
        from .generator import generate_task

        return generate_task
    if name == "FileTaskRepository":
        from .file_repository import FileTaskRepository

        return FileTaskRepository
    if name == "get_file_task_repository":
        from .file_repository import get_file_task_repository

        return get_file_task_repository
    raise AttributeError(name)


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(list(globals().keys()) + list(__all__))
