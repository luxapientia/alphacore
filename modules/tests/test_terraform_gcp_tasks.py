from types import SimpleNamespace

from modules.generation.instructions import TaskInstructionGenerator
from modules.generation.terraform.providers.gcp import composite_resource_bank
from modules.generation.terraform.providers.gcp import single_resource_bank
from modules.generation.terraform.providers.gcp.task_bank import (
    GCPDynamicTaskBank,
)


def test_dynamic_task_bank_discovers_templates():
    bank = GCPDynamicTaskBank(min_resources=1, max_resources=2)
    task = bank.build_task("validator@example.com")
    metadata = task.spec.metadata
    assert metadata is not None
    assert metadata["resource_keys"], "resource_keys missing"
    assert metadata["hints"], "hints missing"
    assert len(task.spec.invariants) >= len(metadata["resource_keys"])


def test_single_resource_bank_returns_minimal_task():
    task = single_resource_bank.build_task("validator@example.com")
    metadata = task.spec.metadata or {}
    assert metadata.get("resource_keys")
    assert metadata.get("resource_kinds")
    assert metadata.get("composition_family") is not None
    # At least one invariant should exist.
    assert len(task.spec.invariants) >= 1


def test_composite_resource_bank_includes_multiple_resources():
    task = composite_resource_bank.build_task("validator@example.com")
    metadata = task.spec.metadata or {}
    resource_count = len(metadata.get("resource_keys", []))
    assert resource_count >= 2
    assert metadata.get("composition_family") is not None
    assert len(task.spec.invariants) >= resource_count


def test_instruction_generator_consumes_metadata_hints():
    task = single_resource_bank.build_task("validator@example.com")
    generator = _make_stub_generator()
    instructions = generator.generate(task, task_name="test")
    metadata = task.spec.metadata or {}
    hint = metadata["hints"][0]
    assert hint.split()[0] in instructions
    assert task.validator_sa in instructions
    assert "Attribute checklist" not in instructions


def test_composite_bank_varies_families():
    seen_families = set()
    for _ in range(8):
        task = composite_resource_bank.build_task("validator@example.com")
        metadata = task.spec.metadata or {}
        family = metadata.get("composition_family")
        if family:
            seen_families.add(family)
    assert len(seen_families) >= 2


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
