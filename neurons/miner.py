"""
Starter AlphaCore miner neuron (reference implementation).

This implementation is intentionally minimal and safe: it does not execute
Terraform. It shows how to:
  - Read a prompt-only AlphaCore `TaskSynapse` (`task_id` + natural-language prompt).
  - Return a structured response in `result_summary`.
  - Attach a tiny ZIP artifact (example only).

Miners should implement their own pipeline inside `_handle_task()`:
  parse prompt → generate Terraform → apply → ensure terraform.tfstate → zip workspace
"""

import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import bittensor as bt
except ModuleNotFoundError:  # pragma: no cover
    bt = None  # type: ignore[assignment]

try:
    from subnet.base.miner import BaseMinerNeuron
    from subnet.bittensor_config import config as build_config
    from subnet.protocol import (
        StartRoundSynapse,
        TaskCleanupSynapse,
        TaskFeedbackSynapse,
        TaskSynapse,
    )
except ModuleNotFoundError:  # pragma: no cover
    BaseMinerNeuron = object  # type: ignore[misc,assignment]
    build_config = None  # type: ignore[assignment]
    StartRoundSynapse = object  # type: ignore[assignment]
    TaskCleanupSynapse = object  # type: ignore[assignment]
    TaskFeedbackSynapse = object  # type: ignore[assignment]
    TaskSynapse = object  # type: ignore[assignment]

# Models live under `modules/`, which is not necessarily on the
# default `sys.path` when running this neuron from different working directories.
try:
    from modules.models import ACResult, ACEvidence
except ModuleNotFoundError:  # pragma: no cover - allow import in thin dev envs
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    from modules.models import ACResult, ACEvidence

# Prompt parsing for Phase 1 implementation
try:
    from neurons.prompt_parser import PromptParser, PromptParseError
except ImportError:  # pragma: no cover - allow import in thin dev envs
    PromptParser = None  # type: ignore[assignment,misc]
    PromptParseError = Exception  # type: ignore[assignment,misc]

# Terraform generation for Phase 2 implementation
try:
    from neurons.terraform_generator import TerraformGenerator, TerraformGenerationError, TerraformWorkspace
except ImportError:  # pragma: no cover - allow import in thin dev envs
    TerraformGenerator = None  # type: ignore[assignment,misc]
    TerraformGenerationError = Exception  # type: ignore[assignment,misc]
    TerraformWorkspace = None  # type: ignore[assignment,misc]


@dataclass
class TerraformResult:
    """Result from terraform command execution."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    returncode: int = 0


class Miner(BaseMinerNeuron):
    """
    Minimal AlphaCore miner neuron.

    Implementers should replace `_handle_task()` with their own pipeline:
      parse prompt → generate terraform → apply → ensure terraform.tfstate → zip workspace
    """

    neuron_type: str = "AlphaCoreMinerStarter"

    def __init__(self, config: Optional["bt.Config"] = None) -> None:
        if bt is None or build_config is None:
            raise RuntimeError("Missing runtime dependencies: bittensor / subnet.")
        super().__init__(config=config or build_config(role="miner"))
        bt.logging.info(f"Initialized {self.neuron_type}")
        self._app_heartbeat_stop = threading.Event()
        self._app_heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._app_heartbeat_thread.start()

        # Initialize prompt parser (Phase 1)
        self._prompt_parser = None
        if PromptParser is not None:
            try:
                self._prompt_parser = PromptParser()
                bt.logging.info("Prompt parser initialized (Phase 1 enabled)")
            except Exception as exc:
                bt.logging.warning(f"Prompt parser initialization failed: {exc}. Prompt parsing will be skipped.")
                self._prompt_parser = None

        # Initialize Terraform generator (Phase 2)
        self._terraform_generator = None
        if TerraformGenerator is not None:
            try:
                self._terraform_generator = TerraformGenerator()
                bt.logging.info("Terraform generator initialized (Phase 2 enabled)")
            except Exception as exc:
                bt.logging.warning(f"Terraform generator initialization failed: {exc}. Terraform generation will be skipped.")
                self._terraform_generator = None

    # ------------------------------------------------------------------ #
    # Handshake / feedback / cleanup (required axon endpoints)
    # ------------------------------------------------------------------ #

    async def forward_start_round(self, synapse: StartRoundSynapse) -> StartRoundSynapse:
        self._log_incoming(
            "StartRoundSynapse",
            synapse,
            extra={
                "round_id": getattr(synapse, "round_id", ""),
                "timestamp": getattr(synapse, "timestamp", None),
                "validator_version": getattr(synapse, "validator_version", ""),
            },
        )
        synapse.miner_version = "starter"
        synapse.is_ready = True
        synapse.available_capacity = 1
        synapse.error_message = None
        self._log_outgoing(
            "StartRoundSynapse",
            synapse,
            extra={
                "is_ready": bool(getattr(synapse, "is_ready", False)),
                "available_capacity": int(getattr(synapse, "available_capacity", 0) or 0),
            },
        )
        return synapse

    async def forward_feedback(self, synapse: TaskFeedbackSynapse) -> TaskFeedbackSynapse:
        self._log_incoming(
            "TaskFeedbackSynapse",
            synapse,
            extra={
                "round_id": getattr(synapse, "round_id", ""),
                "task_id": getattr(synapse, "task_id", ""),
                "score": getattr(synapse, "score", None),
                "latency_seconds": getattr(synapse, "latency_seconds", None),
                "feedback_text": getattr(synapse, "feedback_text", None),
            },
        )
        synapse.acknowledged = True
        self._log_outgoing("TaskFeedbackSynapse", synapse, extra={"acknowledged": True})
        return synapse

    async def forward_cleanup(self, synapse: TaskCleanupSynapse) -> TaskCleanupSynapse:
        # Default: acknowledge; real miners should tear down any temporary files/resources.
        self._log_incoming(
            "TaskCleanupSynapse",
            synapse,
            extra={
                "task_id": getattr(synapse, "task_id", ""),
                "validation_keys": list(getattr(synapse, "validation_response", {}) or {})[:12],
            },
        )
        synapse.acknowledged = True
        synapse.cleanup_ok = True
        synapse.error_message = None
        self._log_outgoing(
            "TaskCleanupSynapse",
            synapse,
            extra={"acknowledged": True, "cleanup_ok": True, "error_message": None},
        )
        return synapse

    # ------------------------------------------------------------------ #
    # Task endpoint
    # ------------------------------------------------------------------ #

    async def forward(self, synapse: TaskSynapse) -> TaskSynapse:
        """
        Handle a single AlphaCore task.

        Validators score miners primarily from `workspace_zip_b64` (submission ZIP).
        This starter implementation returns a structured response and can optionally
        attach a tiny ZIP artifact (example transport only).
        """
        start = time.time()

        try:
            try:
                self._tasks_handled = int(getattr(self, "_tasks_handled", 0) or 0) + 1
                self._last_task_at = float(time.time())
                self._last_task_id = str(getattr(synapse, "task_id", "") or "")
            except Exception:
                pass

            prompt = self._extract_prompt(synapse)
            self._log_incoming("TaskSynapse", synapse, extra={"task_id": getattr(synapse, "task_id", "")})
            self._log_prompt(prompt)

            # Use retry wrapper for iterative error-feedback loop
            max_retries = self._get_max_retries()
            retry_enabled = self._env_flag("ALPHACORE_RETRY_ENABLED", default=True)

            if retry_enabled and max_retries > 0:
                result, evidence = await self._handle_task_with_retry(
                    task_id=synapse.task_id,
                    prompt=prompt,
                    max_retries=max_retries,
                )
            else:
                # Fallback to single attempt if retry disabled
                result, evidence = await self._handle_task(task_id=synapse.task_id, prompt=prompt)

            synapse.attach_result(result, evidence=evidence)

            # Attach ZIP if task succeeded (Phase 2+)
            if result.status == "success" and evidence:
                workspace_path = evidence.attachments.get("workspace_path")
                if workspace_path:
                    try:
                        zip_bytes = self._package_workspace_zip(Path(workspace_path))
                        if zip_bytes:
                            synapse.attach_workspace_zip_bytes(zip_bytes, filename=f"{synapse.task_id}.zip")
                    except Exception as exc:
                        if bt:
                            bt.logging.warning(f"Failed to package workspace ZIP: {exc}")
            elif self._env_flag("ALPHACORE_MINER_RETURN_EXAMPLE_ZIP", default=True):
                # Fallback: Attach a minimal ZIP artifact for end-to-end transport testing.
            # This is not a scored submission, but it is a concrete example of the expected ZIP transport.
                if self._env_flag("ALPHACORE_MINER_RETURN_DUMMY_ZIP"):
                    zip_bytes = self._build_dummy_zip(task_id=synapse.task_id, prompt=prompt)
                else:
                    zip_bytes = self._build_example_zip(task_id=synapse.task_id, prompt=prompt)
                synapse.attach_workspace_zip_bytes(zip_bytes, filename=f"{synapse.task_id}.zip")

            synapse.notes = f"handled_by={self.neuron_type} latency_s={time.time() - start:.3f}"
            self._log_outgoing(
                "TaskSynapse",
                synapse,
                extra={
                    "task_id": getattr(synapse, "task_id", ""),
                    "latency_s": float(time.time() - start),
                    "workspace_zip_sha256": getattr(synapse, "workspace_zip_sha256", None),
                    "workspace_zip_size_bytes": getattr(synapse, "workspace_zip_size_bytes", None),
                    "result_status": (getattr(synapse, "result_summary", {}) or {}).get("status"),
                },
            )
            return synapse
        except Exception as exc:
            bt.logging.error(f"Task failed: task_id={getattr(synapse, 'task_id', None)} err={exc}")
            synapse.attach_result(
                ACResult(
                    task_id=str(getattr(synapse, "task_id", "") or ""),
                    status="error",
                    notes=str(exc),
                )
            )
            synapse.notes = f"error latency_s={time.time() - start:.3f}"
            self._log_outgoing(
                "TaskSynapse",
                synapse,
                extra={
                    "task_id": getattr(synapse, "task_id", ""),
                    "latency_s": float(time.time() - start),
                    "error": str(exc),
                },
            )
            return synapse

    async def _handle_task(self, *, task_id: str, prompt: str) -> tuple[ACResult, Optional[ACEvidence]]:
        """
        Handle task execution pipeline:
        Phase 1: Parse prompt → extract structured requirements
        Phase 2-5: TODO - Generate Terraform, apply, package ZIP

        Currently implements Phase 1 (prompt parsing).
        """
        parsed_requirements = None

        # Phase 1: Parse prompt into structured requirements
        if self._prompt_parser is not None:
            try:
                parsed_requirements = self._prompt_parser.parse(prompt)
                if bt:
                    bt.logging.info(
                        f"Parsed prompt: {len(parsed_requirements.get('resources', []))} resources, "
                        f"{len(parsed_requirements.get('iam_grants', []))} IAM grants"
                    )
            except PromptParseError as e:
                if bt:
                    bt.logging.warning(f"Prompt parsing failed: {e}")
                # Continue with not_implemented status if parsing fails
            except Exception as e:
                if bt:
                    bt.logging.error(f"Unexpected error during prompt parsing: {e}")
                # Continue with not_implemented status on unexpected errors

        # TODO: Phase 2-5 (Terraform generation, apply, ZIP packaging)
        # For now, return not_implemented but include parsed requirements in evidence
        status = "not_implemented"
        notes = (
            "Phase 1 (prompt parsing) complete. "
            "Phases 2-5 (Terraform generation, apply, ZIP packaging) not yet implemented. "
            "This miner currently returns an example ZIP (see ALPHACORE_MINER_RETURN_EXAMPLE_ZIP)."
        )

        evidence_attachments = {"kind": "phase1_only", "phase": 1}
        if parsed_requirements:
            evidence_attachments["parsed_resources_count"] = len(parsed_requirements.get("resources", []))
            evidence_attachments["parsed_iam_grants_count"] = len(parsed_requirements.get("iam_grants", []))
            # Store parsed requirements in evidence (for debugging/verification)
            evidence_attachments["parsed_requirements"] = parsed_requirements

        return (
            ACResult(
                task_id=task_id,
                status=status,
                notes=notes,
            ),
            ACEvidence(task_id=task_id, attachments=evidence_attachments),
        )

    # ------------------------------------------------------------------ #
    # Iterative Error-Feedback Loop (Phase 2.5)
    # ------------------------------------------------------------------ #

    def _get_max_retries(self) -> int:
        """Get maximum retry attempts from environment variable."""
        try:
            return int(os.getenv("ALPHACORE_MAX_RETRIES", "5") or "5")
        except Exception:
            return 5

    async def _handle_task_with_retry(
        self, *, task_id: str, prompt: str, max_retries: int = 5
    ) -> tuple[ACResult, Optional[ACEvidence]]:
        """
        Wrapper that implements iterative error-feedback loop.

        Flow:
        1. Parse prompt → Generate Terraform → Try terraform apply
        2. If error → Feed error + original prompt to LLM → Generate fixed version → Retry
        3. Continue until success or max_retries
        """
        original_prompt = prompt
        error_history: list[dict[str, Any]] = []  # Track errors across retries

        for attempt in range(max_retries):
            try:
                # Attempt generation and execution
                result, evidence = await self._handle_task_single_attempt(
                    task_id=task_id,
                    prompt=prompt,
                    attempt=attempt,
                    error_history=error_history,
                )

                # If successful, return immediately
                if result.status == "success":
                    if attempt > 0 and bt:
                        bt.logging.info(f"Task {task_id} succeeded after {attempt} retries")
                    return result, evidence

                # If not successful, capture error for next iteration
                error_message = result.notes or "Unknown error"
                error_history.append(
                    {
                        "attempt": attempt,
                        "error": error_message,
                        "status": result.status,
                    }
                )

                # Generate improved prompt using error feedback (if not last attempt)
                if attempt < max_retries - 1:
                    fixed_prompt = await self._generate_fixed_prompt(
                        original_prompt=original_prompt,
                        error_history=error_history,
                        latest_error=error_message,
                    )
                    if bt:
                        bt.logging.info(
                            f"Task {task_id} attempt {attempt + 1} failed: {error_message[:200]}. Retrying with fixed prompt..."
                        )
                    prompt = fixed_prompt
                else:
                    if bt:
                        bt.logging.error(f"Task {task_id} failed after {max_retries} attempts")
                    return result, evidence

            except Exception as exc:
                error_message = str(exc)
                error_history.append(
                    {
                        "attempt": attempt,
                        "error": error_message,
                        "status": "exception",
                    }
                )

                if attempt < max_retries - 1:
                    fixed_prompt = await self._generate_fixed_prompt(
                        original_prompt=original_prompt,
                        error_history=error_history,
                        latest_error=error_message,
                    )
                    if bt:
                        bt.logging.warning(
                            f"Task {task_id} attempt {attempt + 1} raised exception: {exc}. Retrying..."
                        )
                    prompt = fixed_prompt
                else:
                    # Max retries reached, return error
                    return (
                        ACResult(
                            task_id=task_id,
                            status="error",
                            notes=f"Failed after {max_retries} attempts. Last error: {error_message}",
                        ),
                        None,
                    )

        # Should never reach here, but just in case
        return (
            ACResult(task_id=task_id, status="error", notes="Retry loop exhausted"),
            None,
        )

    async def _handle_task_single_attempt(
        self,
        *,
        task_id: str,
        prompt: str,
        attempt: int,
        error_history: list[dict[str, Any]],
    ) -> tuple[ACResult, Optional[ACEvidence]]:
        """
        Single attempt at generating and applying Terraform.

        Returns (result, evidence) where result.status can be:
        - "success": Terraform applied successfully, tfstate exists
        - "error": Error occurred (will trigger retry if attempt < max_retries)
        - "not_implemented": Feature not yet implemented (should not retry)
        """
        # Phase 1: Parse prompt
        parsed_requirements = None
        if self._prompt_parser:
            try:
                parsed_requirements = self._prompt_parser.parse(prompt)
                if bt:
                    bt.logging.info(
                        f"[Attempt {attempt + 1}] Parsed prompt: {len(parsed_requirements.get('resources', []))} resources, "
                        f"{len(parsed_requirements.get('iam_grants', []))} IAM grants"
                    )
            except PromptParseError as e:
                return (
                    ACResult(
                        task_id=task_id,
                        status="error",
                        notes=f"Prompt parsing failed: {e}",
                    ),
                    None,
                )
            except Exception as e:
                return (
                    ACResult(
                        task_id=task_id,
                        status="error",
                        notes=f"Prompt parsing exception: {e}",
                    ),
                    None,
                )

        # Phase 2-5: Terraform generation, apply, ZIP packaging
        if not self._terraform_generator:
            # Terraform generator not available
            status = "not_implemented"
            notes = (
                "Phase 1 (prompt parsing) complete. "
                "Terraform generator not available. "
                f"This was attempt {attempt + 1}."
            )
            evidence_attachments = {"kind": "phase1_only", "phase": 1, "attempt": attempt + 1}
            if parsed_requirements:
                evidence_attachments["parsed_resources_count"] = len(parsed_requirements.get("resources", []))
                evidence_attachments["parsed_iam_grants_count"] = len(parsed_requirements.get("iam_grants", []))
            return (
                ACResult(task_id=task_id, status=status, notes=notes),
                ACEvidence(task_id=task_id, attachments=evidence_attachments),
            )

        if not parsed_requirements:
            # Cannot generate Terraform without parsed requirements
            return (
                ACResult(
                    task_id=task_id,
                    status="error",
                    notes="Cannot generate Terraform: prompt parsing failed or returned no resources",
                ),
                None,
            )

        # Phase 2: Generate Terraform workspace
        try:
            workspace = self._terraform_generator.generate_workspace(parsed_requirements)
            if bt:
                bt.logging.info(f"[Attempt {attempt + 1}] Generated Terraform workspace at {workspace.path}")
        except TerraformGenerationError as e:
            return (
                ACResult(
                    task_id=task_id,
                    status="error",
                    notes=f"Terraform generation failed: {e}",
                ),
                None,
            )
        except Exception as e:
            return (
                ACResult(
                    task_id=task_id,
                    status="error",
                    notes=f"Terraform generation exception: {e}",
                ),
                None,
            )

        # Phase 3: Run terraform init
        init_result = await self._run_terraform_init(workspace.path)
        if not init_result.success:
            # Cleanup workspace on error
            import shutil
            if workspace.path.exists():
                shutil.rmtree(workspace.path, ignore_errors=True)
            return (
                ACResult(
                    task_id=task_id,
                    status="error",
                    notes=f"terraform init failed: {init_result.error}",
                ),
                None,
            )

        # Phase 4: Run terraform apply
        apply_result = await self._run_terraform_apply(workspace.path)
        if not apply_result.success:
            # Cleanup workspace on error
            import shutil
            if workspace.path.exists():
                shutil.rmtree(workspace.path, ignore_errors=True)
            return (
                ACResult(
                    task_id=task_id,
                    status="error",
                    notes=f"terraform apply failed: {apply_result.error}",
                ),
                None,
            )

        # Phase 5: Verify tfstate exists
        tfstate_path = workspace.path / "terraform.tfstate"
        if not tfstate_path.exists():
            # Cleanup workspace on error
            import shutil
            if workspace.path.exists():
                shutil.rmtree(workspace.path, ignore_errors=True)
            return (
                ACResult(
                    task_id=task_id,
                    status="error",
                    notes="terraform.tfstate not found after apply",
                ),
                None,
            )

        # Success! Package workspace info for evidence
        evidence_attachments = {
            "kind": "phase2_complete",
            "phase": 2,
            "attempt": attempt + 1,
            "workspace_path": str(workspace.path),
        }

        return (
            ACResult(
                task_id=task_id,
                status="success",
                notes=f"Terraform applied successfully (attempt {attempt + 1})",
            ),
            ACEvidence(task_id=task_id, attachments=evidence_attachments),
        )

    async def _generate_fixed_prompt(
        self,
        *,
        original_prompt: str,
        error_history: list[dict[str, Any]],
        latest_error: str,
    ) -> str:
        """
        Generate an improved prompt by feeding errors back to LLM.

        This uses the same OpenAI client as prompt parsing, but with
        a different system message focused on fixing errors.
        """
        if not self._prompt_parser or not hasattr(self._prompt_parser, "client"):
            # Fallback: just append error to prompt (not ideal, but better than nothing)
            return f"{original_prompt}\n\nPREVIOUS ERROR: {latest_error}\nPlease fix the above error and regenerate the Terraform configuration."

        # Build error history summary (only last 3 errors to avoid token bloat)
        recent_errors = error_history[-3:] if len(error_history) > 3 else error_history
        error_summary = "\n".join(
            [f"Attempt {err['attempt'] + 1}: {err['error'][:500]}" for err in recent_errors]
        )

        system_message = """You are a Terraform configuration fixer. Given an original prompt and error messages from failed attempts, generate an improved prompt that will produce correct Terraform code.

Your task:
1. Analyze the original prompt and error messages
2. Identify what went wrong (validation errors, dependency issues, syntax errors, etc.)
3. Generate a corrected version of the prompt that will fix these issues
4. Ensure the corrected prompt is clear, specific, and will generate valid Terraform

Return ONLY the corrected prompt text, nothing else."""

        user_message = f"""ORIGINAL PROMPT:
{original_prompt}

ERROR HISTORY:
{error_summary}

LATEST ERROR:
{latest_error}

Please generate a corrected prompt that addresses these errors and will produce valid Terraform configuration."""

        try:
            # Get model from prompt parser config
            model = getattr(self._prompt_parser, "model", "gpt-4o-mini")
            temperature = 0.3  # Lower temperature for more focused fixes

            response = self._prompt_parser.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                max_tokens=2000,
            )

            fixed_prompt = response.choices[0].message.content.strip()
            if bt:
                bt.logging.info(f"Generated fixed prompt (length: {len(fixed_prompt)})")
            return fixed_prompt

        except Exception as exc:
            if bt:
                bt.logging.error(f"Failed to generate fixed prompt: {exc}. Using fallback.")
            # Fallback: append error to original prompt
            return f"{original_prompt}\n\nPREVIOUS ERROR: {latest_error}\nPlease fix the error above."

    async def _run_terraform_init(self, workspace_path: Path, timeout: int = 300) -> TerraformResult:
        """Run terraform init and capture output/errors."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "terraform",
                "init",
                "-input=false",
                "-backend=false",
                "-no-color",
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                # Extract error from stderr (last 500 chars for relevance)
                error_snippet = stderr_text[-500:] if stderr_text else stdout_text[-500:]
                return TerraformResult(
                    success=False,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    error=error_snippet,
                    returncode=proc.returncode,
                )

            return TerraformResult(
                success=True,
                stdout=stdout_text,
                stderr=stderr_text,
                returncode=0,
            )

        except asyncio.TimeoutError:
            return TerraformResult(
                success=False,
                stdout="",
                stderr="",
                error=f"terraform init timed out after {timeout}s",
                returncode=-1,
            )
        except Exception as exc:
            return TerraformResult(
                success=False,
                stdout="",
                stderr="",
                error=f"terraform init exception: {exc}",
                returncode=-1,
            )

    async def _run_terraform_apply(self, workspace_path: Path, timeout: int = 600) -> TerraformResult:
        """Run terraform apply and capture output/errors."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "terraform",
                "apply",
                "-auto-approve",
                "-no-color",
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                # Extract error from stderr (last 500 chars for relevance)
                error_snippet = stderr_text[-500:] if stderr_text else stdout_text[-500:]
                return TerraformResult(
                    success=False,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    error=error_snippet,
                    returncode=proc.returncode,
                )

            return TerraformResult(
                success=True,
                stdout=stdout_text,
                stderr=stderr_text,
                returncode=0,
            )

        except asyncio.TimeoutError:
            return TerraformResult(
                success=False,
                stdout="",
                stderr="",
                error=f"terraform apply timed out after {timeout}s",
                returncode=-1,
            )
        except Exception as exc:
            return TerraformResult(
                success=False,
                stdout="",
                stderr="",
                error=f"terraform apply exception: {exc}",
                returncode=-1,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_prompt(synapse: TaskSynapse) -> str:
        prompt = (getattr(synapse, "prompt", None) or "").strip()
        if prompt:
            return prompt
        task_spec = getattr(synapse, "task_spec", None)
        if isinstance(task_spec, dict):
            maybe = task_spec.get("prompt")
            if isinstance(maybe, str) and maybe.strip():
                return maybe.strip()
        return ""

    @staticmethod
    def _env_flag(name: str, *, default: bool = False) -> bool:
        raw = os.getenv(name, None)
        if raw is None or raw == "":
            return bool(default)
        return raw.strip().lower() in ("1", "true", "yes", "y", "on")

    @staticmethod
    def _safe_preview(text: str, *, limit: int = 240) -> str:
        cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not cleaned:
            return ""
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit] + "…"

    @staticmethod
    def _log_human_block(prefix: str, lines: list[str]) -> None:
        if bt is None:
            return
        for line in lines:
            try:
                bt.logging.info(f"{prefix}{line}")
            except Exception:
                pass

    def _dendrite_summary(self, synapse: object) -> str:
        dend = getattr(synapse, "dendrite", None)
        if dend is None:
            return "from=?"
        hotkey = getattr(dend, "hotkey", None)
        ip = getattr(dend, "ip", None)
        port = getattr(dend, "port", None)
        hotkey_short = f"{str(hotkey)[:10]}…" if hotkey else "?"
        ip_part = f"{ip}:{port}" if ip else "?"
        return f"from={hotkey_short} ip={ip_part}"

    def _log_incoming(self, kind: str, synapse: object, *, extra: Optional[dict] = None) -> None:
        if bt is None:
            return
        uid = int(getattr(self, "uid", -1))
        bits = [f"<-- {kind:<18} uid={uid} {self._dendrite_summary(synapse)}"]
        extra = extra or {}
        if kind == "TaskSynapse":
            task_id = str(extra.get("task_id") or getattr(synapse, "task_id", "") or "")
            if task_id:
                bits.append(f"task_id={task_id}")
        if kind == "TaskFeedbackSynapse":
            task_id = str(extra.get("task_id") or getattr(synapse, "task_id", "") or "")
            score = extra.get("score", getattr(synapse, "score", None))
            if task_id:
                bits.append(f"task_id={task_id}")
            if score is not None:
                bits.append(f"score={float(score):.4f}")
        if kind == "StartRoundSynapse":
            round_id = str(extra.get("round_id") or getattr(synapse, "round_id", "") or "")
            validator_version = str(
                extra.get("validator_version")
                or getattr(synapse, "validator_version", "")
                or ""
            )
            if round_id:
                bits.append(f"round_id={round_id}")
            bits.append(f"validator_version={validator_version or 'unknown'}")
        if kind == "TaskCleanupSynapse":
            task_id = str(extra.get("task_id") or getattr(synapse, "task_id", "") or "")
            if task_id:
                bits.append(f"task_id={task_id}")
        self._log_human_block("", [" ".join(bits)])

    def _log_outgoing(self, kind: str, synapse: object, *, extra: Optional[dict] = None) -> None:
        if bt is None:
            return
        uid = int(getattr(self, "uid", -1))
        extra = extra or {}
        bits = [f"--> {kind:<18} uid={uid}"]
        if kind == "TaskSynapse":
            task_id = str(extra.get("task_id") or getattr(synapse, "task_id", "") or "")
            status = str(extra.get("result_status") or (getattr(synapse, "result_summary", {}) or {}).get("status") or "")
            zip_bytes = extra.get("workspace_zip_size_bytes", getattr(synapse, "workspace_zip_size_bytes", None))
            if task_id:
                bits.append(f"task_id={task_id}")
            if status:
                bits.append(f"status={status}")
            if zip_bytes is not None:
                bits.append(f"zip_bytes={int(zip_bytes)}")
        if kind == "TaskFeedbackSynapse":
            task_id = str(getattr(synapse, "task_id", "") or "")
            score = getattr(synapse, "score", None)
            if task_id:
                bits.append(f"task_id={task_id}")
            if score is not None:
                bits.append(f"score={float(score):.4f}")
            bits.append("ack=true")
        if kind == "StartRoundSynapse":
            bits.append(f"is_ready={bool(getattr(synapse, 'is_ready', False))}")
            bits.append(f"capacity={int(getattr(synapse, 'available_capacity', 0) or 0)}")
        if kind == "TaskCleanupSynapse":
            bits.append(f"ack={bool(getattr(synapse, 'acknowledged', False))}")
            bits.append(f"cleanup_ok={bool(getattr(synapse, 'cleanup_ok', False))}")
        self._log_human_block("", [" ".join(bits)])

    def _log_prompt(self, prompt: str) -> None:
        prompt = (prompt or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not prompt:
            self._log_human_block("", ["prompt=(empty)"])
            return

        try:
            max_chars = int(os.getenv("ALPHACORE_MINER_PROMPT_LOG_MAX_CHARS", "2500") or "2500")
        except Exception:
            max_chars = 2500
        max_chars = max(200, max_chars)

        one_line = " ".join(prompt.split())
        if len(one_line) > max_chars:
            self._log_human_block("", [f"prompt={one_line[:max_chars]}… (truncated, total_chars={len(one_line)})"])
            return
        self._log_human_block("", [f"prompt={one_line}"])

    @staticmethod
    def _package_workspace_zip(workspace_path: Path) -> bytes:
        """
        Package Terraform workspace directory into ZIP archive.

        Includes all .tf files and terraform.tfstate at the repository root.
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Add all .tf files
            for tf_file in workspace_path.glob("*.tf"):
                if tf_file.is_file():
                    zf.write(tf_file, arcname=tf_file.name)

            # Add terraform.tfstate if it exists
            tfstate_path = workspace_path / "terraform.tfstate"
            if tfstate_path.exists() and tfstate_path.is_file():
                zf.write(tfstate_path, arcname="terraform.tfstate")

        return buf.getvalue()

    @staticmethod
    def _build_dummy_zip(*, task_id: str, prompt: str) -> bytes:
        """
        Build a tiny ZIP artifact for wiring tests.

        This is NOT a valid submission for scoring; it's just a transport example.
        """
        payload = {"task_id": task_id, "prompt": prompt, "status": "dummy"}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("output.json", json.dumps(payload, indent=2, sort_keys=True))
            zf.writestr("README.txt", "AlphaCore starter miner artifact (dummy)\n")
        return buf.getvalue()

    @staticmethod
    def _build_example_zip(*, task_id: str, prompt: str) -> bytes:
        """
        Build a tiny Terraform workspace ZIP that is safe to run.

        The config intentionally creates no resources (no providers needed), so
        `terraform init` / `terraform apply` can succeed without downloading plugins.
        """
        tf_main = (
            'terraform {\n'
            '  required_version = ">= 1.5.0"\n'
            '}\n'
            '\n'
            'output "alphacore_miner_example" {\n'
            '  value = "hello-from-starter-miner"\n'
            '}\n'
        )
        meta = {
            "task_id": task_id,
            "kind": "starter_example",
            "prompt_preview": (prompt or "")[:240],
            "created_at_unix": int(time.time()),
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("main.tf", tf_main)
            zf.writestr(
                "README.md",
                (
                    "# AlphaCore Starter Miner Submission (Example)\n\n"
                    "This ZIP is an example artifact returned by the starter miner.\n"
                    "It contains a minimal Terraform project that creates no resources.\n"
                    "Replace this with your own Terraform code + real `terraform.tfstate` output.\n"
                ),
            )
            zf.writestr("alphacore_miner_meta.json", json.dumps(meta, indent=2, sort_keys=True))
        return buf.getvalue()

    def _heartbeat_loop(self) -> None:
        if bt is None:
            return
        try:
            interval_s = float(os.getenv("ALPHACORE_MINER_APP_HEARTBEAT_SECONDS", "60") or "60")
        except Exception:
            interval_s = 60.0
        interval_s = max(1.0, float(interval_s))

        while not self._app_heartbeat_stop.is_set() and not bool(getattr(self, "should_exit", False)):
            try:
                tasks_handled = int(getattr(self, "_tasks_handled", 0) or 0)
                last_task_id = getattr(self, "_last_task_id", None)
                hotkey = None
                try:
                    hotkey = str(getattr(getattr(self.wallet, "hotkey", None), "ss58_address", None))
                except Exception:
                    hotkey = None
                bt.logging.info(
                    f"hb uid={int(getattr(self, 'uid', -1))} hotkey={hotkey} "
                    f"tasks={tasks_handled} last_task_id={str(last_task_id) if last_task_id else 'null'}"
                )
            except Exception:
                pass
            self._app_heartbeat_stop.wait(interval_s)


if __name__ == "__main__":
    if bt is None or build_config is None:
        raise SystemExit("bittensor is not installed; cannot run miner.")
    cfg = build_config(role="miner")
    with Miner(config=cfg) as miner:
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            bt.logging.info("Miner shutting down.")
