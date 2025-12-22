#!/usr/bin/env python3
"""
Interactive Task Validation Script

This script validates Terraform tasks by:
1. Loading the task specification (validator.json)
2. Loading the Terraform state file
3. Running evaluation through the Evaluator
4. Displaying detailed scores and results

Usage:
    # Interactive mode
    python examples/validate_task.py

    # CLI mode - validate with task JSON and state file
    python examples/validate_task.py --task <task_json> --state <state_file>
    python examples/validate_task.py -t <task_json> -s <state_file>

    # CLI mode - validate task folder
    python examples/validate_task.py --folder <task_folder>
    python examples/validate_task.py -f <task_folder>
"""

import sys
import json
import os
import argparse
from pathlib import Path
from typing import Any, Dict, Optional

from modules.evaluation import Evaluator
from modules.evaluation.validation.task_validator import validate_task, validate_task_result


def write_json(path: str, payload: dict) -> None:
    """Write a JSON payload to a path, creating parent dirs."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def write_success(path: str, score: float, *, passed_invariants: int, total_invariants: int) -> None:
    write_json(
        path,
        {
            "status": "pass",
            "score": float(score),
            "passed_invariants": int(passed_invariants),
            "total_invariants": int(total_invariants),
        },
    )


def write_fail(path: str, msg: str, score: float, *, passed_invariants: int, total_invariants: int) -> None:
    write_json(
        path,
        {
            "status": "fail",
            "msg": msg,
            "score": float(score),
            "passed_invariants": int(passed_invariants),
            "total_invariants": int(total_invariants),
        },
    )


def write_error(path: str, msg: str) -> None:
    write_json(path, {"status": "fail", "msg": msg, "score": 0.0})


def print_header(text: str, width: int = 70):
    """Print a formatted header."""
    print("\n" + "=" * width)
    print(text)
    print("=" * width)


def print_section(text: str, width: int = 70):
    """Print a formatted section header."""
    print("\n" + "-" * width)
    print(text)
    print("-" * width)


def get_input(prompt: str, default: Optional[str] = None) -> str:
    """Get user input with optional default value."""
    if default:
        full_prompt = f"{prompt} [{default}]: "
    else:
        full_prompt = f"{prompt}: "

    value = input(full_prompt).strip()
    return value if value else (default or "")


def find_task_files(task_folder: str) -> tuple[Optional[str], Optional[str]]:
    """
    Auto-discover validator.json and terraform.tfstate in task folder.

    Returns:
        (validator_json_path, tfstate_path) tuple, or (None, None) if not found
    """
    task_path = Path(task_folder)

    if not task_path.exists() or not task_path.is_dir():
        return None, None

    validator_json = task_path / "validator.json"
    tfstate = task_path / "terraform.tfstate"

    validator_path = str(validator_json) if validator_json.exists() else None
    state_path = str(tfstate) if tfstate.exists() else None

    return validator_path, state_path


def load_task_data(validator_json_path: str) -> Optional[Dict[str, Any]]:
    """Load and parse validator.json file."""
    try:
        with open(validator_json_path, 'r') as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        print(f"❌ Error: File not found: {validator_json_path}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid JSON in {validator_json_path}: {e}")
        return None
    except Exception as e:
        print(f"❌ Error loading task data: {e}")
        return None


def validate_with_simple_api(task_data: Dict[str, Any], state_file: str) -> float:
    """Validate using the simple validate_task API."""
    print_section("Simple Validation (Pass/Fail)")

    try:
        # Extract just the task spec for validation
        task_spec = task_data.get("task", task_data)
        task_json = json.dumps(task_spec)

        result = validate_task(task_json, state_file)

        if result >= 1.0:
            print("✓ PASSED - All invariants match")
        else:
            print("✗ FAILED - One or more invariants do not match")

        print(f"\nValidation Score: {result}")
        return result

    except Exception as e:
        print(f"❌ Validation error: {e}")
        return 0.0


def evaluate_with_evaluator(task_data: Dict[str, Any], state_file: str, bundle_dir: str) -> None:
    """Evaluate using the full Evaluator API."""
    print_section("Comprehensive Evaluation")

    try:
        # Check if bundle_dir has Terraform files
        tf_files = list(Path(bundle_dir).glob("*.tf"))
        if not tf_files:
            print(f"⚠️  Warning: No .tf files found in {bundle_dir}")
            print(f"   Idempotency check will be skipped")

        # Check if terraform is initialized
        tf_dir = Path(bundle_dir) / ".terraform"
        if not tf_dir.exists():
            print(f"⚠️  Warning: Terraform not initialized in {bundle_dir}")
            print(f"   Idempotency check may fail")

        evaluator = Evaluator(strict_mode=False)

        # Get the actual state filename from the provided path
        state_filename = os.path.basename(state_file)

        # Prepare task dict for evaluator
        # Important: Update submit_requirements to match the actual state file provided
        task = {
            "task": task_data.get("task", task_data),
            "task_id": task_data.get("task", {}).get("task_id", "unknown"),
            "submit_requirements": {
                "code": ".",
                "state": state_filename,  # Use actual state filename, not default
                "package_format": "archive",
                "bundle_layout": {"state": state_filename}
            }
        }

        # Prepare response dict
        response = {
            "bundle_dir": bundle_dir,
            "task_id": task.get("task_id"),
            "result_summary": {}
        }

        print()  # Add spacing before evaluation output
        # Run evaluation
        score = evaluator.evaluate(task, response)

        # Display results
        print(f"\n📊 Evaluation Results:")
        print(f"  Task ID:         {score.task_id}")
        print(f"  Pass/Fail:       {'✓ PASS' if score.pass_fail == 1 else '✗ FAIL'} ({score.pass_fail})")
        print(f"  Quality Score:   {score.quality:.3f}")
        print(f"  Timeliness:      {score.timeliness:.3f}")
        print(f"  Policy:          {score.policy_adherence:.3f}")

        print(f"\n💯 Overall Score: {score.pass_fail} (binary pass/fail)")
        print(f"   Quality Detail: {score.quality * 100:.1f}%")

        if score.pass_fail == 1:
            print("\n✓ Task evaluation PASSED")
            print("  - All invariants validated successfully")
        else:
            print("\n✗ Task evaluation FAILED")

            # Try to determine what failed
            print("\n  Failure Analysis:")

            # Check if it's likely idempotency
            # Pass/Fail requires: correctness >= 1.0 AND idempotency >= 0.8
            # If quality is high but pass_fail is 0, likely idempotency issue
            if score.quality > 0.5:
                print("  - ⚠️  Likely idempotency check failed")
                print("     (Resources in state may have been destroyed)")
                print("     Run 'terraform apply' to recreate resources")
            else:
                print("  - Correctness or quality checks failed")

            print("\n  Note: Comprehensive evaluation requires:")
            print("    1. All invariants match (correctness)")

        return score

    except Exception as e:
        print(f"❌ Evaluation error: {e}")
        import traceback
        traceback.print_exc()
        return None


def display_task_info(task_data: Dict[str, Any]):
    """Display task information."""
    print_section("Task Information")

    task_spec = task_data.get("task", task_data)

    print(f"Task ID:      {task_spec.get('task_id', 'N/A')}")
    print(f"Kind:         {task_spec.get('kind', 'N/A')}")
    print(f"Provider:     {task_data.get('provider', 'N/A')}")
    print(f"Engine:       {task_data.get('engine', 'N/A')}")

    invariants = task_spec.get('invariants', [])
    print(f"\nInvariants:   {len(invariants)} total")

    for i, inv in enumerate(invariants, 1):
        print(f"\n  Invariant {i}:")
        print(f"    Resource Type: {inv.get('resource_type', 'N/A')}")
        match = inv.get('match', {})
        print(f"    Expected Values:")
        for key, value in match.items():
            print(f"      {key}: {value}")


def validate_cli(task_json: str, state_file: str) -> tuple[int, float, str, int, int]:
    """Validate task via CLI mode, returning exit code, score, and message."""
    # Load task data
    if os.path.exists(task_json):
        with open(task_json, 'r') as f:
            task_data = json.load(f)
    else:
        try:
            task_data = json.loads(task_json)
        except json.JSONDecodeError:
            print(f"❌ Error: Invalid JSON or file not found: {task_json}")
            return 1, 0.0, "invalid task JSON or file not found", 0, 0

    if not os.path.exists(state_file):
        print(f"❌ Error: State file not found: {state_file}")
        return 1, 0.0, "state file not found", 0, 0

    task_spec = task_data.get("task", task_data)
    task_json_str = json.dumps(task_spec)

    validation = validate_task_result(task_json_str, state_file)
    total = int(getattr(validation, "total_invariants", 0) or 0)
    passed = int(getattr(validation, "passed_invariants", 0) or 0)
    score = float(passed) / float(total) if total > 0 else 0.0

    try:
        print_section("Invariant Results")
        invariants = getattr(validation, "invariants", None)
        if isinstance(invariants, list) and invariants:
            for idx, inv in enumerate(invariants, 1):
                resource_type = str(getattr(inv, "resource_type", "") or "unknown")
                inv_match = getattr(inv, "invariant_match", {}) or {}
                field_count = len(inv_match) if isinstance(inv_match, dict) else 0
                inv_passed = bool(getattr(inv, "passed", False))
                status = "PASS" if inv_passed else "FAIL"
                print(f"- {idx}/{len(invariants)} {status} {resource_type} fields={field_count}")
                if not inv_passed:
                    errors = getattr(inv, "errors", None)
                    if isinstance(errors, list) and errors:
                        for err in errors[:5]:
                            print(f"  - {err}")
        else:
            print("- (no invariants)")
    except Exception:
        pass

    if score >= 1.0:
        print("✓ PASSED - All invariants match")
        return 0, score, "validation passed", passed, total
    if total <= 0:
        msg = "no invariants provided"
    else:
        msg = f"{passed}/{total} invariants matched"
    print(f"✗ FAILED - {msg}")
    return 1, score, msg, passed, total


def validate_folder_cli(task_folder: str) -> tuple[int, float, str, int, int]:
    """Validate task from folder via CLI mode, returning exit code, score, and message."""
    validator_json_path, state_file_path = find_task_files(task_folder)

    if not validator_json_path or not state_file_path:
        print(f"❌ Error: Could not find validator.json and/or terraform.tfstate in {task_folder}")
        if not validator_json_path:
            print(f"  Missing: validator.json")
        if not state_file_path:
            print(f"  Missing: terraform.tfstate")
        return 1, 0.0, "validator.json or terraform.tfstate missing", 0, 0

    print(f"✓ Found validator.json: {validator_json_path}")
    print(f"✓ Found terraform.tfstate: {state_file_path}")

    # Load and validate
    task_data = load_task_data(validator_json_path)
    if not task_data:
        return 1, 0.0, "task data missing", 0, 0

    # Run validation
    task_spec = task_data.get("task", task_data)
    task_json_str = json.dumps(task_spec)
    validation = validate_task_result(task_json_str, state_file_path)
    total = int(getattr(validation, "total_invariants", 0) or 0)
    passed = int(getattr(validation, "passed_invariants", 0) or 0)
    score = float(passed) / float(total) if total > 0 else 0.0

    try:
        print_section("Invariant Results")
        invariants = getattr(validation, "invariants", None)
        if isinstance(invariants, list) and invariants:
            for idx, inv in enumerate(invariants, 1):
                resource_type = str(getattr(inv, "resource_type", "") or "unknown")
                inv_match = getattr(inv, "invariant_match", {}) or {}
                field_count = len(inv_match) if isinstance(inv_match, dict) else 0
                inv_passed = bool(getattr(inv, "passed", False))
                status = "PASS" if inv_passed else "FAIL"
                print(f"- {idx}/{len(invariants)} {status} {resource_type} fields={field_count}")
                if not inv_passed:
                    errors = getattr(inv, "errors", None)
                    if isinstance(errors, list) and errors:
                        for err in errors[:5]:
                            print(f"  - {err}")
        else:
            print("- (no invariants)")
    except Exception:
        pass

    if score >= 1.0:
        print("✓ SUCCESS - Task validation passed")
        return 0, score, "validation passed", passed, total
    if total <= 0:
        msg = "no invariants provided"
    else:
        msg = f"{passed}/{total} invariants matched"
    print(f"✗ FAILED - {msg}")
    return 1, score, msg, passed, total


def main():
    """Main function with CLI and interactive modes."""
    parser = argparse.ArgumentParser(
        description='Validate Terraform infrastructure tasks',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Validate with task JSON file and state file
  python examples/validate_task.py --task tasks/abc-123/validator.json --state tasks/abc-123/terraform.tfstate
  python examples/validate_task.py -t validator.json -s terraform.tfstate

  # Validate task folder (auto-discovers files)
  python examples/validate_task.py --folder tasks/abc-123
  python examples/validate_task.py -f tasks/abc-123

  # Interactive mode (default)
  python examples/validate_task.py
        '''
    )

    parser.add_argument(
        '-t', '--task',
        help='Path to task JSON file (validator.json) or JSON string'
    )

    parser.add_argument(
        '-s', '--state',
        help='Path to terraform state file (terraform.tfstate)'
    )

    parser.add_argument(
        '-f', '--folder',
        help='Path to task folder (auto-discovers validator.json and terraform.tfstate)'
    )
    parser.add_argument(
        '--success-json',
        help='Path to write success JSON result (status/pass, score)',
    )
    parser.add_argument(
        '--error-json',
        help='Path to write error JSON result (status/error, msg, score=0)',
    )

    args = parser.parse_args()

    results_dir = os.environ.get("RESULTS_DIR")
    success_path = args.success_json or (results_dir and os.path.join(results_dir, "success.json"))
    error_path = args.error_json or (results_dir and os.path.join(results_dir, "error.json"))

    # CLI mode: validate from folder
    if args.folder:
        exit_code, score, msg, passed, total = validate_folder_cli(args.folder)
        # In sandbox mode, only write success.json on true pass; otherwise write error.json.
        if exit_code == 0:
            if success_path:
                write_success(success_path, score, passed_invariants=passed, total_invariants=total)
        else:
            if error_path:
                write_fail(error_path, msg, score, passed_invariants=passed, total_invariants=total)
        return exit_code, score, msg

    # CLI mode: validate with task and state
    if args.task and args.state:
        exit_code, score, msg, passed, total = validate_cli(args.task, args.state)
        if exit_code == 0:
            if success_path:
                write_success(success_path, score, passed_invariants=passed, total_invariants=total)
        else:
            if error_path:
                write_fail(error_path, msg, score, passed_invariants=passed, total_invariants=total)
        return exit_code, score, msg

    # CLI mode: incomplete arguments
    if args.task or args.state:
        print("❌ Error: Both --task and --state are required when using CLI mode")
        parser.print_help()
        sys.exit(1)

    print("❌ Error: Interactive mode is disabled; provide --task/--state or --folder.")
    sys.exit(1)


if __name__ == "__main__":
    try:
        result = main()
        if isinstance(result, tuple):
            code = result[0]
        else:
            code = int(result) if result is not None else 0
        sys.exit(code)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
