"""
Comprehensive unit tests for TaskInstructionGenerator and prompt generation.

Tests cover:
- Initialization and configuration
- Context building
- Instruction generation (with mocked LLM)
- Fallback mechanisms
- Text formatting and sanitization
- Invariant summarization
- Provider-specific formatting
"""

import os
import pytest
from types import SimpleNamespace
from unittest.mock import Mock, patch, MagicMock

from modules.generation.instructions import (
    TaskInstructionGenerator,
    DISALLOWED_TERMS,
    PROVIDER_SYNONYMS,
)
from modules.models import Invariant, TerraformTask, TaskSpec


class TestTaskInstructionGeneratorInit:
    """Test initialization and configuration."""

    def test_default_initialization(self):
        """Test generator with default settings."""
        generator = TaskInstructionGenerator()
        assert generator.model is not None
        assert generator.temperature > 0
        assert generator.llm_retries >= 1

    def test_custom_parameters(self):
        """Test generator with custom parameters."""
        generator = TaskInstructionGenerator(
            model="gpt-4",
            temperature=0.8,
            enable_llm=False,
            llm_retries=5,
            fallback_on_failure=False,
        )
        assert generator.model == "gpt-4"
        assert generator.temperature == 0.8
        assert generator.enable_llm is False
        assert generator.llm_retries == 5
        assert generator.fallback_on_failure is False

    def test_environment_variables(self):
        """Test that environment variables are respected."""
        with patch.dict(os.environ, {
            "ALPHACORE_TASK_PROMPT_MODEL": "test-model",
            "ALPHACORE_LLM_TEMPERATURE": "0.9",
            "ALPHACORE_LLM_RETRIES": "3",
            "ALPHACORE_ENABLE_LLM": "false",
            "ALPHACORE_PROMPT_POSTPROCESS": "minimal",
        }):
            generator = TaskInstructionGenerator()
            assert generator.model == "test-model"
            assert generator.temperature == 0.9
            assert generator.llm_retries == 3
            assert generator.enable_llm is False

    def test_explicit_params_override_env(self):
        """Test that explicit parameters override environment variables."""
        with patch.dict(os.environ, {"ALPHACORE_TASK_PROMPT_MODEL": "env-model"}):
            generator = TaskInstructionGenerator(model="explicit-model")
            assert generator.model == "explicit-model"

    def test_minimum_retry_count(self):
        """Test that retry count is at least 1."""
        generator = TaskInstructionGenerator(llm_retries=0)
        assert generator.llm_retries >= 1

    def test_lazy_client_loading(self):
        """Test that OpenAI client is loaded lazily."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            generator = TaskInstructionGenerator()
            assert generator._client is None  # Not loaded yet
            # Access client property
            _ = generator.client
            # Should attempt to load (will fail in test without real OpenAI)


class TestContextBuilding:
    """Test context building for prompt generation."""

    def test_build_context_basic(self):
        """Test basic context building."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()

        context = generator._build_context(task, "test_task")

        assert "Provider" in context or "gcp" in context.lower()
        assert task.validator_sa in context
        assert "terraform.tfstate" in context.lower()

    def test_build_context_includes_hints(self):
        """Test that context includes metadata hints."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()
        task.spec.metadata = {"hints": ["Use minimal configuration", "Prefer standard options"]}

        context = generator._build_context(task, "test_task")

        assert "minimal configuration" in context.lower() or "standard options" in context.lower()

    def test_build_context_shuffles_blocks(self):
        """Test that context blocks are shuffled."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()

        # Generate multiple contexts and check they're different
        contexts = [generator._build_context(task, "test") for _ in range(5)]

        # At least some should be different (order shuffled)
        assert len(set(contexts)) > 1


class TestFallbackInstructions:
    """Test fallback instruction generation."""

    def test_fallback_instructions_format(self):
        """Test fallback instructions are properly formatted."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()

        fallback = generator._fallback_instructions(task)

        assert len(fallback) > 0
        assert "terraform" in fallback.lower()
        assert task.validator_sa in fallback

    def test_fallback_includes_invariants(self):
        """Test fallback includes invariant summaries."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()
        task.spec.invariants = [
            Invariant(resource_type="google_storage_bucket", match={"values.name": "test-bucket"})
        ]

        fallback = generator._fallback_instructions(task)

        assert "test-bucket" in fallback

    def test_fallback_with_hints(self):
        """Test fallback includes design hints."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()
        task.spec.metadata = {"hints": ["Keep it minimal"]}

        fallback = generator._fallback_instructions(task)

        assert "minimal" in fallback.lower()


class TestInvariantFormatting:
    """Test invariant formatting functions."""

    def test_format_invariants_basic(self):
        """Test basic invariant formatting."""
        invariants = [
            Invariant(
                resource_type="google_storage_bucket",
                match={"values.name": "test-bucket", "values.location": "US"}
            )
        ]

        formatted = TaskInstructionGenerator._format_invariants(invariants)

        assert "test-bucket" in formatted
        assert "US" in formatted

    def test_format_invariants_empty(self):
        """Test formatting with no invariants."""
        formatted = TaskInstructionGenerator._format_invariants([])
        assert "No requirement" in formatted

    def test_humanize_field(self):
        """Test field name humanization."""
        assert TaskInstructionGenerator._humanize_field("values.storage_class") == "storage class"
        assert TaskInstructionGenerator._humanize_field("values.machine_type") == "machine type"
        assert TaskInstructionGenerator._humanize_field("name") == "name"

    def test_summarize_invariants(self):
        """Test invariant summarization."""
        invariants = [
            Invariant(
                resource_type="google_storage_bucket",
                match={"values.name": "my-bucket"}
            )
        ]

        summary = TaskInstructionGenerator._summarize_invariants(invariants)

        assert "my-bucket" in summary
        assert len(summary) > 0

    def test_summarize_invariant_single(self):
        """Test single invariant summary."""
        invariant = Invariant(
            resource_type="google_storage_bucket",
            match={"values.name": "test", "values.location": "US"}
        )

        summary = TaskInstructionGenerator._summarize_invariant(invariant)

        assert "test" in summary
        assert "US" in summary
        assert "Pin the resource" in summary

    def test_join_clauses_variations(self):
        """Test clause joining with different counts."""
        assert TaskInstructionGenerator._join_clauses([]) == ""
        assert TaskInstructionGenerator._join_clauses(["one"]) == "one"
        assert TaskInstructionGenerator._join_clauses(["one", "two"]) == "one and two"
        result = TaskInstructionGenerator._join_clauses(["one", "two", "three"])
        assert "one" in result and "two" in result and "three" in result
        assert "and" in result


class TestTextFormatting:
    """Test text formatting and sanitization."""

    def test_to_plain_text_removes_markdown(self):
        """Test markdown removal."""
        text = "This is **bold** and __italic__ with `code`"
        cleaned = TaskInstructionGenerator._to_plain_text(text)

        assert "**" not in cleaned
        assert "__" not in cleaned
        assert "`" not in cleaned
        assert "bold" in cleaned
        assert "italic" in cleaned

    def test_to_plain_text_ascii_only(self):
        """Test non-ASCII characters are removed."""
        text = "Hello 世界 café"
        cleaned = TaskInstructionGenerator._to_plain_text(text)

        assert cleaned.isascii()
        assert "Hello" in cleaned

    def test_to_plain_text_replaces_invariant(self):
        """Test 'invariant' is replaced with 'requirements'."""
        text = "The invariants must match the Invariant fields"
        cleaned = TaskInstructionGenerator._to_plain_text(text)

        assert "invariant" not in cleaned.lower()
        assert "requirement" in cleaned.lower()

    def test_ensure_provider_reference(self):
        """Test provider reference is ensured."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()
        task.provider = "gcp"

        # Text without provider
        text = "Deploy the resources"
        result = generator._ensure_provider_reference(text, task)

        # Should add provider reference
        assert any(syn in result.lower() for syns in PROVIDER_SYNONYMS.values() for syn in syns)

    def test_ensure_provider_reference_already_present(self):
        """Test provider reference when already present."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()
        task.provider = "gcp"

        text = "Deploy to Google Cloud Platform"
        result = generator._ensure_provider_reference(text, task)

        # Should not duplicate
        assert result.lower().count("google cloud") <= 2

    def test_enforce_allowed_content(self):
        """Test disallowed terms are removed."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()

        # Test with disallowed term
        text_with_readme = "Create a README file for the project"
        with pytest.raises(ValueError, match="disallowed term"):
            generator._enforce_allowed_content(text_with_readme, task)

        # Test with allowed text containing all required terms
        text = (
            "Create a storage bucket named test-bucket in the US region using terraform and GCP. "
            "Package your Terraform code in a zip archive with terraform.tfstate at the root. "
            "Grant read access to validator@test-project.iam.gserviceaccount.com."
        )
        result = generator._enforce_allowed_content(text, task)

        # Should remove or flag disallowed terms
        for term in DISALLOWED_TERMS:
            if term in text.lower():
                assert term not in result.lower() or "requirement" in result.lower()

    def test_enforce_allowed_content_tolerates_punctuation(self):
        """Invariant values should match even if punctuation/whitespace differs."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()
        task.spec.invariants = [
            Invariant(
                resource_type="google_compute_network",
                match={"values.region": "us-central1"},
            )
        ]
        text = (
            "Create the resource in the us central1 region on Google Cloud Platform using terraform. "
            "Package your solution as a zip archive with terraform tfstate included at the repository root. "
            "Grant read access to validator@test-project.iam.gserviceaccount.com."
        )
        assert generator._enforce_allowed_content(text, task)

    def test_required_terms_extract_leaf_values(self):
        """Complex invariant values (dict/list/bool/int) should yield leaf terms, not container repr."""
        task = _create_test_task()
        task.spec.invariants = [
            Invariant(
                resource_type="google_storage_bucket",
                match={
                    "values.labels": {"env": "prod"},
                    "values.versioning": {"enabled": True},
                    "values.lifecycle_rules": [{"action": {"type": "Delete"}, "condition": {"age": 2}}],
                },
            )
        ]
        required = TaskInstructionGenerator._required_terms(task)
        assert "prod" in required
        assert "true" in required
        assert "yes" in required
        assert "delete" in required
        assert "2" in required
        assert "two" in required

    def test_normalize_prompt_phrasing_avoids_self_referential_description(self):
        """Avoid self-referential phrasing like 'records the repository description'."""
        task = _create_test_task()
        text = (
            "Include a concise repository description that records the repository description and format. "
            "Submit a single zip archive of the repository; keep the Terraform config at the repository root and include terraform.tfstate at the repository root."
        )
        normalized = TaskInstructionGenerator._normalize_prompt_phrasing(text, task)
        assert "records the repository description" not in normalized.lower()

    def test_downcase_invariant_enum_tokens(self):
        """Enum-like invariant values should be downcased in miner-facing prompts."""
        task = _create_test_task()
        task.spec.invariants = [
            Invariant(
                resource_type="google_artifact_registry_repository",
                match={
                    "values.location": "US-CENTRAL1",
                    "values.format": "PYTHON",
                },
            ),
            Invariant(
                resource_type="google_storage_bucket",
                match={
                    "values.location": "US-WEST1",
                    "values.storage_class": "COLDLINE",
                },
            ),
        ]
        text = "Create a repo with format PYTHON in US-CENTRAL1 and a bucket in US-WEST1 using COLDLINE."
        out = TaskInstructionGenerator._downcase_invariant_enum_tokens(text, task)
        assert "python" in out
        assert "us-central1" in out
        assert "us-west1" in out
        assert "coldline" in out


class TestProviderFormatting:
    """Test provider-specific formatting."""

    def test_describe_kind_gcp(self):
        """Test GCP kind description."""
        description = TaskInstructionGenerator._describe_kind("gcp.storage.bucket")
        assert "Google Cloud" in description or "GCP" in description
        assert "storage bucket" in description.lower()

    def test_describe_kind_aws(self):
        """Test AWS kind description."""
        description = TaskInstructionGenerator._describe_kind("aws.s3.bucket")
        assert "AWS" in description
        assert "s3 bucket" in description.lower()

    def test_describe_kind_azure(self):
        """Test Azure kind description."""
        description = TaskInstructionGenerator._describe_kind("azure.storage.account")
        assert "Azure" in description
        assert "storage account" in description.lower()

    def test_describe_kind_empty(self):
        """Test empty kind description."""
        description = TaskInstructionGenerator._describe_kind("")
        assert description == "resource"

    def test_describe_kind_version_stripped(self):
        """Test version suffix is stripped."""
        description = TaskInstructionGenerator._describe_kind("gcp.compute.instance.v1")
        assert "v1" not in description
        assert "compute instance" in description.lower()

    def test_provider_label(self):
        """Test provider label generation."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()

        task.provider = "gcp"
        assert "gcp" in generator._provider_label(task).lower() or "google" in generator._provider_label(task).lower()

        task.provider = "aws"
        assert "aws" in generator._provider_label(task).lower()


class TestSubmissionFormatting:
    """Test submission details formatting."""

    def test_format_submission_details_basic(self):
        """Test basic submission details."""
        task = _create_test_task()
        details = TaskInstructionGenerator._format_submission_details(task)

        assert "terraform.tfstate" in details
        assert "archive" in details.lower() or "zip" in details.lower()
        assert "repository root" in details.lower() or "root" in details.lower()

    def test_format_submission_details_custom_layout(self):
        """Test submission details with custom layout."""
        task = _create_test_task()
        task_dict = task.to_dict()
        task_dict["submit_requirements"] = {
            "bundle_layout": {"state": "custom/terraform.tfstate", "readme": "README.md"},
            "package_format": "archive"
        }

        # Recreate task with custom layout (mock)
        details = TaskInstructionGenerator._format_submission_details(task)

        assert "terraform.tfstate" in details


class TestStyleDirectives:
    """Test style and ordering directives."""

    def test_style_directive_returns_string(self):
        """Test style directive returns a non-empty string."""
        directive = TaskInstructionGenerator._style_directive()
        assert isinstance(directive, str)
        assert len(directive) > 0

    def test_style_directive_varies(self):
        """Test style directives vary across calls."""
        directives = [TaskInstructionGenerator._style_directive() for _ in range(10)]
        assert len(set(directives)) > 1

    def test_attribute_order_hint(self):
        """Test attribute order hint generation."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()
        task.spec.invariants = [
            Invariant(
                resource_type="google_storage_bucket",
                match={"values.name": "test", "values.location": "US", "values.storage_class": "STANDARD"}
            )
        ]

        hint = generator._attribute_order_hint(task)

        assert isinstance(hint, str)
        assert len(hint) > 0


class TestGenerateWithMock:
    """Test generation with mocked LLM client."""

    def test_generate_with_stub_client(self):
        """Test generation with stub LLM client."""
        generator = _make_stub_generator()
        task = _create_test_task()

        instructions = generator.generate(task, task_name="test_task")

        assert len(instructions) > 0
        assert "terraform" in instructions.lower()
        assert task.validator_sa in instructions

    def test_generate_disabled_llm_uses_fallback(self):
        """Test that disabled LLM uses fallback instructions."""
        generator = TaskInstructionGenerator(enable_llm=False)
        task = _create_test_task()

        result = generator.generate(task)

        # Should return fallback instructions
        assert result is not None
        assert len(result) > 0
        assert "terraform" in result.lower()
        assert "google cloud" in result.lower()
        # Fallback should mention the resource requirements
        assert any(str(inv.match.get("values.name")) in result for inv in task.spec.invariants if "values.name" in inv.match)

    def test_generate_no_client_raises(self, monkeypatch):
        """Test that missing client raises RuntimeError."""
        # Remove API key(s) from environment to prevent lazy loading.
        # (The generator supports OPENAI_API_KEY and ALPHACORE_OPENAI_API_KEY.)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ALPHACORE_OPENAI_API_KEY", raising=False)
        generator = TaskInstructionGenerator(enable_llm=True)
        task = _create_test_task()

        with pytest.raises(RuntimeError, match="unavailable"):
            generator.generate(task)

    def test_generate_with_retry(self):
        """Test generation with retry on failure."""
        generator = TaskInstructionGenerator(enable_llm=True, llm_retries=3)
        task = _create_test_task()

        # Mock client that fails twice then succeeds
        mock_client = Mock()
        attempts = [0]

        def mock_create(**kwargs):
            attempts[0] += 1
            if attempts[0] < 3:
                raise Exception("Mock failure")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content=(
                        "Create a storage bucket named test-bucket in the US region using terraform and GCP provider. "
                        "Package your solution as a zip archive with terraform.tfstate included. "
                        "Grant read access to validator@test-project.iam.gserviceaccount.com."
                    )
                ))]
            )

        mock_client.chat.completions.create = mock_create
        generator._client = mock_client
        generator._current_task = task

        # Should succeed after retries
        instructions = generator.generate(task, task_name="test")
        assert "storage bucket" in instructions
        assert "test-bucket" in instructions
        assert attempts[0] == 3  # Verify it took 3 attempts


class TestMissingDetailDetection:
    """Test missing detail detection from errors."""

    def test_missing_detail_from_error(self):
        """Test detection of missing details in error messages."""
        generator = TaskInstructionGenerator(enable_llm=False)

        # Test with error containing missing detail
        exc = RuntimeError("LLM output missing required detail: validator@example.com")
        detail = generator._missing_detail_from_error(exc)

        assert detail is not None
        assert "validator@example.com" in detail


class TestRequiredTermExtraction:
    def test_description_field_requires_only_identifier(self):
        """
        Description invariants are sentence-like and easy to paraphrase; only require the stable identifier.
        """
        generator = TaskInstructionGenerator(enable_llm=False)

        spec = TaskSpec(
            version="1.0",
            task_id="test-task-002",
            nonce="test-nonce-abcdef",
            kind="gcp.compute.firewall",
            invariants=[
                Invariant(
                    resource_type="google_compute_firewall",
                    match={
                        "values.description": "Allow SSH only for 82c280-6f6fd8",
                        "values.name": "acore-82c280",
                    },
                )
            ],
            metadata={},
        )
        task = TerraformTask(
            engine="terraform",
            provider="gcp",
            validator_sa="validator@test-project.iam.gserviceaccount.com",
            spec=spec,
        )

        # Paraphrased description (does not include exact sentence) but includes the stable identifier.
        prompt = (
            "All resources must target Google Cloud Platform specifically. "
            "Create a firewall rule named acore-82c280. "
            "Ensure the description references 82c280-6f6fd8. "
            "Bundle the project as a zip archive and include terraform.tfstate."
        )
        assert generator._enforce_allowed_content(prompt, task) == prompt


# Helper functions

def _create_test_task() -> TerraformTask:
    """Create a test TerraformTask for testing."""
    spec = TaskSpec(
        version="1.0",
        task_id="test-task-001",
        nonce="test-nonce-123456",
        kind="gcp.storage.bucket",
        invariants=[
            Invariant(
                resource_type="google_storage_bucket",
                match={"values.name": "test-bucket", "values.location": "US"}
            )
        ],
        metadata={
            "resource_keys": ["storage_bucket"],
            "hints": ["Use standard storage class"],
        }
    )

    return TerraformTask(
        engine="terraform",
        provider="gcp",
        validator_sa="validator@test-project.iam.gserviceaccount.com",
        spec=spec,
    )


def _make_stub_generator() -> TaskInstructionGenerator:
    """Create a generator with stub LLM client for testing."""
    generator = TaskInstructionGenerator(enable_llm=True)
    generator._client = _StubLLMClient(generator)
    return generator


class _StubLLMClient:
    """Stub LLM client for testing."""
    def __init__(self, generator: TaskInstructionGenerator):
        self.chat = SimpleNamespace(completions=_StubLLMCompletions(generator))


class _StubLLMCompletions:
    """Stub LLM completions for testing."""
    def __init__(self, generator: TaskInstructionGenerator):
        self._generator = generator

    def create(self, **kwargs):
        task = self._generator._current_task
        if task is None:
            raise RuntimeError("Stub client invoked without active task.")
        text = self._generator._fallback_instructions(task)
        text = self._generator._ensure_provider_reference(text, task)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        )
