"""
Validation module for Terraform state against task invariants.

This module provides tools to:
1. Parse Terraform state files
2. Validate invariants against actual deployed resources
3. Generate validation reports
4. Validate tasks with per-invariant detail

Recommended API:
    from modules.evaluation.validation import validate_task

    result = validate_task(task_json_string, state_file_path)
    # Returns: fraction of invariants that pass (0.0..1.0)
"""

from modules.evaluation.validation.task_validator import validate_task

__all__ = [
    "validate_task",  # Primary API - returns fraction of invariants that pass (0.0..1.0)
]
