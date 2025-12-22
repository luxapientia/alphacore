"""
Task validation against Terraform state.

This module provides the primary validation API for checking if deployed
infrastructure matches task invariants.
"""
import json
import logging
from typing import List

from modules.models import Invariant
from modules.evaluation.validation.models import InvariantValidation, ValidationResult
from modules.evaluation.validation.state_parser import TerraformStateParser
from modules.evaluation.validation.resource_validators import get_validator

logger = logging.getLogger(__name__)


def validate_task(task_json: str, state_file_path: str) -> float:
    """
    Validate a task against its Terraform state.

    Args:
        task_json: JSON string containing the task definition with invariants
        state_file_path: Path to the terraform.tfstate file

    Returns:
        float: Fraction of invariants that pass (0.0..1.0).

    Example:
        >>> task = json.dumps({"invariants": [...]})
        >>> result = validate_task(task, "terraform.tfstate")
        >>> print(result)  # 0.0..1.0
    """
    try:
        result = validate_task_result(task_json, state_file_path)
        total = int(getattr(result, "total_invariants", 0) or 0)
        passed = int(getattr(result, "passed_invariants", 0) or 0)
        if total <= 0:
            return 0.0
        return float(passed) / float(total)
    except Exception as e:
        logger.error(f"Validation error: {e}")
        return 0.0

def validate_task_result(task_json: str, state_file_path: str) -> ValidationResult:
    """
    Validate a task and return a structured ValidationResult.

    This is the recommended API when you need per-invariant detail.
    """
    try:
        task_data = json.loads(task_json)
        task_id = task_data.get("task_id", "unknown")
        invariants_raw = task_data.get("invariants", [])
        invariants = [Invariant(**inv) for inv in (invariants_raw or [])]

        parser = TerraformStateParser(state_file_path)
        parser.parse()

        return _validate_invariants(
            task_id=str(task_id),
            invariants=invariants,
            parser=parser,
            state_file_path=state_file_path,
        )
    except Exception as e:
        logger.exception("Validation error")
        return ValidationResult(
            task_id="unknown",
            passed=False,
            invariants=[],
            state_file=state_file_path,
            errors=[str(e)],
        )


def _validate_invariants(
    task_id: str,
    invariants: List[Invariant],
    parser: TerraformStateParser,
    state_file_path: str,
) -> ValidationResult:
    """
    Internal: Validate all invariants for a task.

    Args:
        task_id: Task identifier
        invariants: List of invariants to validate
        parser: Parsed Terraform state
        state_file_path: Path to state file

    Returns:
        ValidationResult with all invariant validations
    """
    result = ValidationResult(
        task_id=task_id,
        passed=False,
        invariants=[],
        state_file=state_file_path,
    )

    logger.info(f"Task {task_id}: Validating {len(invariants)} invariants")

    # Validate each invariant
    for idx, invariant in enumerate(invariants, 1):
        logger.debug(f"Task {task_id}: Validating invariant {idx}/{len(invariants)} - {invariant.resource_type}")

        inv_result = _validate_single_invariant(invariant, parser, idx, len(invariants))
        result.invariants.append(inv_result)

        if inv_result.passed:
            logger.debug(f"Task {task_id}: Invariant {idx} PASSED")
        else:
            logger.debug(f"Task {task_id}: Invariant {idx} FAILED - {inv_result.errors}")

    # Summarize and determine overall pass/fail (fail-closed if no invariants).
    result.total_invariants = len(result.invariants)
    result.passed_invariants = sum(1 for inv in result.invariants if inv.passed)
    result.failed_invariants = result.total_invariants - result.passed_invariants
    if result.total_invariants <= 0:
        result.errors.append("No invariants provided.")
        result.passed = False
    else:
        result.passed = result.failed_invariants == 0 and len(result.errors) == 0

    logger.info(
        f"Task {task_id}: Validation complete - "
        f"{result.passed_invariants}/{result.total_invariants} passed"
    )

    return result


def _validate_single_invariant(
    invariant: Invariant,
    parser: TerraformStateParser,
    invariant_index: int = 1,
    total_invariants: int = 1,
) -> InvariantValidation:
    """
    Internal: Validate a single invariant against Terraform state.

    Args:
        invariant: The invariant to validate
        parser: Parsed Terraform state
        invariant_index: Index of this invariant (for logging)
        total_invariants: Total number of invariants (for logging)

    Returns:
        InvariantValidation result with detailed information
    """
    resource_type = invariant.resource_type
    match_fields = invariant.match

    logger.debug(
        f"Validating invariant {invariant_index}/{total_invariants}: "
        f"{resource_type} with {len(match_fields)} fields"
    )

    # Find resources of this type in state
    resources = parser.find_resource_by_type(resource_type)

    if not resources:
        error_msg = f"No resources of type '{resource_type}' found in state"
        logger.warning(error_msg)

        return InvariantValidation(
            resource_type=resource_type,
            invariant_match=match_fields,
            passed=False,
            actual_values={},
            errors=[error_msg],
        )

    logger.debug(f"Found {len(resources)} resource(s) of type '{resource_type}' in state")

    # Get resource-specific validator function
    validator_func = get_validator(resource_type)

    # Try to match invariant against each resource instance
    all_errors = []
    for res_idx, resource in enumerate(resources, 1):
        logger.debug(f"Checking resource instance {res_idx}/{len(resources)}")

        validation = validator_func(invariant, resource, parser)

        if validation.passed:
            logger.debug(f"Invariant matched resource instance {res_idx}")
            return validation
        else:
            all_errors.extend(validation.errors)

    # No resource matched - return failure with all errors
    error_msg = (
        f"None of the {len(resources)} '{resource_type}' resource(s) "
        f"matched all {len(match_fields)} invariant field(s)"
    )

    logger.warning(error_msg)

    return InvariantValidation(
        resource_type=resource_type,
        invariant_match=match_fields,
        passed=False,
        actual_values={},
        errors=[error_msg] + all_errors[:5],  # Limit error count
    )
