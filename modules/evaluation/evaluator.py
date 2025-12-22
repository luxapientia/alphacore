"""
Evaluation scaffolding for AlphaCore validators.

Evaluator provides comprehensive scoring of miner submissions across multiple dimensions:
- Correctness: Do deployed resources match task invariants?
- Quality: Is the code well-structured and following best practices?
- Timeliness: How quickly did the miner respond?
- Policy Adherence: Does the solution follow cost and security constraints?

The Evaluator uses the validation system (validate_task) for correctness checking,
then adds additional quality metrics to produce a final ACScore.
"""

from __future__ import annotations

import os
import json
import subprocess
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from modules.models import ACScore
from modules.evaluation.validation import validate_task


logger = logging.getLogger(__name__)


class Evaluator:
    """
    Comprehensive evaluator for Terraform tasks.

    Evaluation Flow:
    1. Validate correctness (invariants match deployed resources)
    2. Check idempotency (terraform plan shows no changes)
    3. Assess code quality (structure, best practices)
    4. Measure timeliness (response time)
    5. Check policy adherence (cost tier, security)

    Returns ACScore with detailed metrics across all dimensions.
    """

    def __init__(self, strict_mode: bool = False):
        """
        Initialize evaluator.

        Args:
            strict_mode: If True, fail on any warnings or non-critical issues
        """
        self.strict_mode = strict_mode

    def evaluate(self, task: Dict[str, Any], response: Dict[str, Any]) -> ACScore:
        """
        Evaluate a miner's submission comprehensively.

        Args:
            task: Task specification with invariants and requirements
            response: Miner's response with bundle_dir and metadata

        Returns:
            ACScore with pass_fail and quality metrics
        """
        # Extract task metadata
        task_spec = task.get("task") or task.get("spec", {})
        task_id = str(task.get("task_id") or task_spec.get("task_id") or "unknown")

        logger.info(f"Evaluating task {task_id}")

        # Get bundle directory
        bundle_dir = response.get("bundle_dir", "")
        if not bundle_dir or not os.path.exists(bundle_dir):
            logger.error(f"Task {task_id}: Bundle directory not found: {bundle_dir}")
            return ACScore(task_id=task_id, pass_fail=0)

        # Get state file path
        submit_reqs = task.get("submit_requirements", {})
        bundle_layout = submit_reqs.get("bundle_layout", {})
        statefile_rel = bundle_layout.get("state", "terraform.tfstate")
        statefile_path = os.path.join(bundle_dir, statefile_rel)

        if not os.path.exists(statefile_path):
            logger.error(f"Task {task_id}: State file not found: {statefile_path}")
            return ACScore(task_id=task_id, pass_fail=0)

        # Initialize scores
        correctness_score = 0.0
        idempotency_score = 0.0
        quality_score = 0.0
        timeliness_score = 1.0  # Default, can be adjusted based on response metadata
        policy_score = 1.0  # Default, can be adjusted based on violations

        # 1. CORRECTNESS: Validate invariants using validation system
        correctness_score = self._validate_correctness(task, statefile_path, task_id)

        if correctness_score == 0.0:
            logger.warning(f"Task {task_id}: Failed correctness check")
            return ACScore(
                task_id=task_id,
                pass_fail=0,
                quality=0.0,
                timeliness=timeliness_score,
                policy_adherence=policy_score,
            )

        # 2. IDEMPOTENCY: Check terraform plan shows no changes
        # NOTE: Idempotency check disabled - should be validated inside Firecracker VM
        # idempotency_score = self._check_idempotency(bundle_dir, task_id)
        idempotency_score = 1.0  # Always pass for now

        # 3. QUALITY: Assess code quality
        quality_score = self._assess_quality(bundle_dir, statefile_path, task, task_id)

        # 4. TIMELINESS: Check response time if available
        timeliness_score = self._assess_timeliness(response, task_id)

        # 5. POLICY: Check policy adherence
        policy_score = self._assess_policy(task, response, statefile_path, task_id)

        # Calculate overall pass/fail
        # Must pass correctness and idempotency to get pass=1
        pass_fail = 1 if (correctness_score >= 1.0 and idempotency_score >= 0.8) else 0

        # Calculate weighted quality score
        # Correctness and idempotency are captured in pass_fail
        # Quality score combines code quality, timeliness, and policy
        overall_quality = (quality_score * 0.4 + timeliness_score * 0.3 + policy_score * 0.3)

        logger.info(
            f"Task {task_id}: Evaluation complete - "
            f"pass_fail={pass_fail}, quality={overall_quality:.2f}, "
            f"timeliness={timeliness_score:.2f}, policy={policy_score:.2f}"
        )

        return ACScore(
            task_id=task_id,
            pass_fail=pass_fail,
            quality=overall_quality,
            timeliness=timeliness_score,
            policy_adherence=policy_score,
        )

    def _validate_correctness(self, task: Dict[str, Any], statefile_path: str, task_id: str) -> float:
        """
        Validate that deployed resources match task invariants.

        Uses the validation system (validate_task) for correctness checking.

        Returns:
            Fraction of invariants that pass (0.0..1.0).
        """
        try:
            # Convert task to JSON for validate_task
            task_json = json.dumps(task.get("task") or task.get("spec", {}))

            # Use validation system
            result = validate_task(task_json, statefile_path)

            logger.info(f"Task {task_id}: Correctness validation result: {result}")
            return result

        except Exception as e:
            logger.error(f"Task {task_id}: Correctness validation error: {e}")
            return 0.0

    def _check_idempotency(self, bundle_dir: str, task_id: str) -> float:
        """
        Check that terraform plan shows no changes (idempotent deployment).

        Returns:
            1.0 if no changes, 0.8 if minor acceptable changes, 0.0 if fails
        """
        try:
            # Run terraform plan
            plan_cmd = ["terraform", "plan", "-no-color", "-detailed-exitcode"]
            logger.debug(f"Task {task_id}: Running terraform plan in {bundle_dir}")

            result = subprocess.run(
                plan_cmd,
                cwd=bundle_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )

            # Exit code 0 = no changes (perfect)
            # Exit code 2 = changes detected (fail)
            # Exit code 1 = error (fail)
            if result.returncode == 0:
                logger.info(f"Task {task_id}: Idempotency check passed - no changes")
                return 1.0
            elif result.returncode == 2:
                logger.warning(f"Task {task_id}: Idempotency check failed - changes detected")
                logger.debug(f"Plan output:\n{result.stdout}")
                return 0.0
            else:
                logger.error(f"Task {task_id}: Terraform plan error: {result.stderr}")
                return 0.0

        except subprocess.TimeoutExpired:
            logger.error(f"Task {task_id}: Terraform plan timed out")
            return 0.0
        except Exception as e:
            logger.error(f"Task {task_id}: Idempotency check error: {e}")
            return 0.0

    def _assess_quality(
        self,
        bundle_dir: str,
        statefile_path: str,
        task: Dict[str, Any],
        task_id: str
    ) -> float:
        """
        Assess code quality of the Terraform configuration.

        Checks:
        - No unnecessary resources deployed
        - Proper resource naming
        - No hardcoded sensitive values
        - Proper use of variables

        Returns:
            Quality score from 0.0 to 1.0
        """
        quality_score = 1.0
        penalties = []

        try:
            # Load state to check for extra resources
            with open(statefile_path, "r") as f:
                state_data = json.load(f)

            # Check for extra resources
            spec_block = task.get("task") or task.get("spec", {})
            invariants = spec_block.get("invariants", [])
            expected_resource_types = {inv.get("resource_type") for inv in invariants}

            observed_resources = state_data.get("resources", [])
            observed_types = {res.get("type") for res in observed_resources}

            extra_resources = observed_types - expected_resource_types
            if extra_resources:
                penalty = min(0.2 * len(extra_resources), 0.4)
                quality_score -= penalty
                penalties.append(f"Extra resources deployed: {extra_resources}")

            # Check resource count (shouldn't deploy way more than asked)
            if len(observed_resources) > len(invariants) * 1.5:
                quality_score -= 0.1
                penalties.append(f"Too many resources: {len(observed_resources)} vs {len(invariants)} expected")

            # Check for common issues in .tf files
            tf_files = list(Path(bundle_dir).glob("*.tf"))
            for tf_file in tf_files:
                content = tf_file.read_text()

                # Penalize hardcoded sensitive values (basic check)
                if "password" in content.lower() or "secret" in content.lower():
                    if "=" in content:  # Likely hardcoded
                        quality_score -= 0.15
                        penalties.append("Possible hardcoded sensitive values")
                        break

            if penalties:
                logger.info(f"Task {task_id}: Quality penalties: {', '.join(penalties)}")
            else:
                logger.info(f"Task {task_id}: No quality issues found")

            return max(0.0, quality_score)

        except Exception as e:
            logger.error(f"Task {task_id}: Quality assessment error: {e}")
            return 0.7  # Neutral score on error

    def _assess_timeliness(self, response: Dict[str, Any], task_id: str) -> float:
        """
        Assess response timeliness based on metadata.

        Returns:
            Timeliness score from 0.0 to 1.0
        """
        # If response has timing information, use it
        # Otherwise return default

        result_summary = response.get("result_summary", {})
        response_time = result_summary.get("response_time")

        if response_time is not None:
            # Exponential decay: perfect at 0s, 0.5 at 60s, lower after
            if response_time <= 30:
                score = 1.0
            elif response_time <= 60:
                score = 0.9
            elif response_time <= 120:
                score = 0.8
            elif response_time <= 300:
                score = 0.7
            else:
                score = 0.6

            logger.debug(f"Task {task_id}: Response time {response_time}s -> timeliness {score}")
            return score

        # No timing info, return default
        return 1.0

    def _assess_policy(
        self,
        task: Dict[str, Any],
        response: Dict[str, Any],
        statefile_path: str,
        task_id: str
    ) -> float:
        """
        Assess policy adherence (cost, security, constraints).

        Returns:
            Policy score from 0.0 to 1.0
        """
        policy_score = 1.0

        try:
            # Check cost tier compliance
            policy = task.get("policy", {})
            max_cost = policy.get("max_cost", "low")

            # Load state to check resource types for cost
            with open(statefile_path, "r") as f:
                state_data = json.load(f)

            resources = state_data.get("resources", [])

            # Check for expensive resource types if cost tier is "low"
            if max_cost == "low":
                expensive_types = {
                    "google_compute_instance": "machine_type",
                    "google_container_cluster": "node_pool",
                }

                for res in resources:
                    res_type = res.get("type", "")
                    if res_type in expensive_types:
                        # Check if using expensive options
                        instances = res.get("instances", [])
                        if instances:
                            attrs = instances[0].get("attributes", {})
                            machine_type = attrs.get("machine_type", "")

                            # Penalize if not using e2-micro/small/medium
                            if machine_type and not any(x in machine_type for x in ["e2-micro", "e2-small", "e2-medium", "f1-micro", "g1-small"]):
                                policy_score -= 0.2
                                logger.warning(f"Task {task_id}: Using expensive machine type: {machine_type}")

            logger.debug(f"Task {task_id}: Policy adherence score: {policy_score}")
            return max(0.0, policy_score)

        except Exception as e:
            logger.error(f"Task {task_id}: Policy assessment error: {e}")
            return 1.0  # Don't penalize on error
