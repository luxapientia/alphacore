import tempfile
from pathlib import Path
import pytest

pytest.skip("Sandbox rootfs features not yet implemented", allow_module_level=True)

# from alphacore_subnet.validator.sandbox import (
#     SandboxTask,
#     inject_task_payload,
#     prepare_rootfs,
#     collect_artifacts,
# )


def _make_dir_rootfs(base_dir: Path) -> Path:
    rootfs = base_dir / "base_rootfs"
    (rootfs / "acore/task").mkdir(parents=True, exist_ok=True)
    return rootfs


def test_prepare_rootfs_clones_directory_rootfs() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        base_rootfs = _make_dir_rootfs(tmp_path)
        (base_rootfs / "acore/task/existing.txt").write_text("hello")
        task = SandboxTask(task_id="t-rootfs", config_dir=tmp_path / "config")

        context = prepare_rootfs(task, base_rootfs=base_rootfs, work_dir=tmp_path / "work")

        assert context.rootfs_path.is_dir()
        assert (context.rootfs_path / "acore/task/existing.txt").read_text() == "hello"
        assert context.task_dir.is_dir()


def test_inject_task_payload_copies_config_into_rootfs_dir() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        base_rootfs = _make_dir_rootfs(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "main.tf").write_text("resource \"random_id\" \"example\" {}")
        task = SandboxTask(task_id="t-rootfs", config_dir=config_dir)
        context = prepare_rootfs(task, base_rootfs=base_rootfs, work_dir=tmp_path / "work")

        inject_task_payload(task, context)

        copied = context.rootfs_path / "acore/task/main.tf"
        assert copied.exists()
        assert copied.read_text() == 'resource "random_id" "example" {}'


def test_collect_artifacts_reads_from_dir_rootfs() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        base_rootfs = _make_dir_rootfs(tmp_path)
        task = SandboxTask(task_id="t-rootfs", config_dir=tmp_path / "config")
        context = prepare_rootfs(task, base_rootfs=base_rootfs, work_dir=tmp_path / "work")
        plan = context.rootfs_path / "acore/task/plan.json"
        log = context.rootfs_path / "acore/task/terraform.log"
        plan.write_text('{"plan": true}')
        log.write_text("log contents")

        artifacts = collect_artifacts(context)

        assert "plan_json" in artifacts
        assert artifacts["plan_json"].read_text() == '{"plan": true}'
        assert "terraform_log" in artifacts
        assert artifacts["terraform_log"].read_text() == "log contents"
