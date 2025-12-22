import os
import pytest
from types import SimpleNamespace
from modules.generation.terraform.registry import terraform_task_registry
from modules.generation import TaskInstructionGenerator


@pytest.fixture(autouse=True)
def stub_llm_for_tests():
    """Use stub LLM client to avoid real API calls during tests."""
    original = terraform_task_registry.default_instruction_generator
    terraform_task_registry.default_instruction_generator = _make_stub_generator()
    yield
    terraform_task_registry.default_instruction_generator = original


def _make_stub_generator() -> TaskInstructionGenerator:
    generator = TaskInstructionGenerator(enable_llm=True)
    generator._client = _StubLLMClient(generator)
    return generator


class _StubLLMClient:
    def __init__(self, generator: TaskInstructionGenerator) -> None:
        self.chat = SimpleNamespace(completions=_StubLLMCompletions(generator))


class _StubLLMCompletions:
    def __init__(self, generator: TaskInstructionGenerator) -> None:
        self._generator = generator

    def create(self, **_: object):
        task = self._generator._current_task
        if task is None:
            raise RuntimeError("Stub client invoked without active task.")
        text = self._generator._fallback_instructions(task)
        text = self._generator._ensure_provider_reference(text, task)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        )


@pytest.fixture(scope="module")
def check_openai_api_key():
    """Note: This fixture is not used since we stub the LLM client."""
    pass


def test_gcp_task_registry_has_task_banks():
    """Test that GCP task banks are available in registry"""
    tasks = terraform_task_registry.get_task_builders("gcp")
    assert len(tasks) > 0, "Should have GCP task banks registered"
    assert "single_resource_bank" in tasks or "composite_resource_bank" in tasks


def test_gcp_task_registry_pick_random():
    """Test that pick_random_task returns a valid task builder"""
    task_name, builder = terraform_task_registry.pick_random_task("gcp")
    assert task_name is not None
    assert builder is not None
    assert callable(builder)


def test_gcp_single_resource_task_generation():
    """Test that a GCP single resource task can be generated successfully"""
    task_builders = terraform_task_registry.get_task_builders("gcp")
    if "single_resource_bank" in task_builders:
        builder = task_builders["single_resource_bank"]
        task = builder(validator_sa="test-validator@test-project.iam.gserviceaccount.com")

        # Verify task has required attributes
        assert hasattr(task, "spec")
        assert hasattr(task, "to_dict")

        # Verify spec has required fields
        assert task.spec.task_id is not None
        assert task.spec.nonce is not None


def test_gcp_composite_resource_task_generation():
    """Test that a GCP composite resource task can be generated successfully"""
    task_builders = terraform_task_registry.get_task_builders("gcp")
    if "composite_resource_bank" in task_builders:
        builder = task_builders["composite_resource_bank"]
        task = builder(validator_sa="test-validator@test-project.iam.gserviceaccount.com")

        # Verify task has required attributes
        assert hasattr(task, "spec")
        assert hasattr(task, "to_dict")

        # Verify spec has required fields
        assert task.spec.task_id is not None
        assert task.spec.nonce is not None


def test_gcp_task_to_dict():
    """Test that generated tasks can be serialized to dict"""
    task_name, builder = terraform_task_registry.pick_random_task("gcp")
    task = builder(validator_sa="test-validator@test-project.iam.gserviceaccount.com")

    task_dict = task.to_dict()
    assert isinstance(task_dict, dict)
    assert "task" in task_dict
    # Invariants are nested inside task
    assert "invariants" in task_dict["task"]


def test_gcp_task_has_metadata():
    """Test that generated tasks have metadata with resource information"""
    task_name, builder = terraform_task_registry.pick_random_task("gcp")
    task = builder(validator_sa="test-validator@test-project.iam.gserviceaccount.com")

    assert task.spec.metadata is not None
    assert "resource_keys" in task.spec.metadata
    assert "hints" in task.spec.metadata


def test_gcp_task_has_invariants():
    """Test that generated tasks have invariants for verification"""
    task_name, builder = terraform_task_registry.pick_random_task("gcp")
    task = builder(validator_sa="test-validator@test-project.iam.gserviceaccount.com")

    assert len(task.spec.invariants) > 0, "Task should have at least one invariant"


def test_gcp_registry_has_gcp_provider():
    """Test that GCP provider is registered"""
    providers = terraform_task_registry.get_all_providers()
    assert "gcp" in providers, "GCP should be in registered providers"
