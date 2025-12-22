import tempfile
from pathlib import Path
import pytest

pytest.skip("Sandbox linting features not yet implemented", allow_module_level=True)

# from alphacore_subnet.validator.sandbox import NetworkPolicy, SandboxTask, lint_task_config


def _write_tf(config_dir: Path, name: str, content: str) -> Path:
    path = config_dir / name
    path.write_text(content)
    return path


def test_safe_config_passes_lint() -> None:
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

resource "random_pet" "example" {
  length = 2
}
""",
        )
        task = SandboxTask(task_id="t-123", config_dir=config_dir)

        result = lint_task_config(task, allowed_providers={"hashicorp/random"})

        assert result.passed
        assert result.errors == []


def test_rejects_provisioners() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        _write_tf(
            config_dir,
            "main.tf",
            """
resource "null_resource" "bad" {
  provisioner "local-exec" {
    command = "echo unsafe"
  }
}
""",
        )
        task = SandboxTask(task_id="t-unsafe", config_dir=config_dir)

        result = lint_task_config(task, allowed_providers={"hashicorp/random"})

        assert not result.passed
        assert any(err.rule_id == "provisioner_forbidden" for err in result.errors)


def test_rejects_external_data_source() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        _write_tf(
            config_dir,
            "main.tf",
            """
data "external" "bad" {
  program = ["echo", "oops"]
}
""",
        )
        task = SandboxTask(task_id="t-external", config_dir=config_dir)

        result = lint_task_config(task, allowed_providers={"hashicorp/random"})

        assert not result.passed
        assert any(err.rule_id == "external_data_forbidden" for err in result.errors)


def test_rejects_unapproved_providers() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        _write_tf(
            config_dir,
            "main.tf",
            """
terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
    }
  }
}
""",
        )
        task = SandboxTask(task_id="t-provider", config_dir=config_dir)

        result = lint_task_config(task, allowed_providers={"hashicorp/random"})

        assert not result.passed
        assert any(err.rule_id == "provider_not_allowed" for err in result.errors)


def test_rejects_missing_tf_files() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        task = SandboxTask(task_id="t-empty", config_dir=config_dir)

        result = lint_task_config(task, allowed_providers={"hashicorp/random"})

        assert not result.passed
        assert any(err.rule_id == "missing_tf_files" for err in result.errors)
