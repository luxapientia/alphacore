import tempfile
import pytest
from pathlib import Path

pytest.skip("Sandbox controller features not yet implemented", allow_module_level=True)

# from alphacore_subnet.validator.sandbox import (
#     NetworkPolicy,
#     SandboxController,
#     SandboxRunStatus,
#     SandboxTask,
# )


def _write_tf(config_dir: Path, name: str, content: str) -> Path:
    path = config_dir / name
    path.write_text(content)
    return path


def test_controller_returns_lint_failure_without_vm_run() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        task = SandboxTask(
            task_id="t-empty",
            config_dir=config_dir,
            network_policy=NetworkPolicy.NONE,
        )
        controller = SandboxController()

        result = controller.run_task(task)

        assert result.status == SandboxRunStatus.LINT_FAILED
        assert not result.lint_result.passed


def test_controller_stops_at_not_implemented_after_lint_passes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        _write_tf(
            config_dir,
            "main.tf",
            """
terraform {
  required_providers {
    random = {
      source = "hashicorp/random"
    }
  }
}
resource "random_pet" "example" {}
""",
        )
        task = SandboxTask(task_id="t-safe", config_dir=config_dir)
        controller = SandboxController()

        with pytest.raises(NotImplementedError):
            controller.run_task(task)
