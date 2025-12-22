"""Models for validation results."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class InvariantValidation:
    """Result of validating a single invariant."""

    resource_type: str
    """The Terraform resource type (e.g., google_compute_instance)."""

    invariant_match: Dict[str, Any]
    """The expected match values from the invariant."""

    passed: bool
    """Whether the invariant validation passed."""

    actual_values: Dict[str, Any] = field(default_factory=dict)
    """The actual values found in the state file."""

    errors: List[str] = field(default_factory=list)
    """List of validation error messages."""

    warnings: List[str] = field(default_factory=list)
    """List of validation warnings (non-fatal issues)."""


@dataclass
class ValidationResult:
    """Complete validation result for a task."""

    task_id: str
    """The task ID being validated."""

    passed: bool
    """Whether all invariants passed validation."""

    invariants: List[InvariantValidation]
    """Individual invariant validation results."""

    state_file: str
    """Path to the Terraform state file."""

    total_invariants: int = 0
    """Total number of invariants checked."""

    passed_invariants: int = 0
    """Number of invariants that passed."""

    failed_invariants: int = 0
    """Number of invariants that failed."""

    errors: List[str] = field(default_factory=list)
    """General validation errors (state parsing, file not found, etc.)."""

    def __post_init__(self):
        """Calculate summary statistics."""
        self.total_invariants = len(self.invariants)
        self.passed_invariants = sum(1 for inv in self.invariants if inv.passed)
        self.failed_invariants = self.total_invariants - self.passed_invariants
        self.passed = self.failed_invariants == 0 and len(self.errors) == 0

    def summary(self) -> str:
        """Generate a human-readable summary."""
        status = "✓ PASSED" if self.passed else "✗ FAILED"
        return (
            f"{status}\n"
            f"Task: {self.task_id}\n"
            f"Invariants: {self.passed_invariants}/{self.total_invariants} passed\n"
            f"State File: {self.state_file}"
        )
