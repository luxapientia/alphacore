"""
Integration tests for TaskInstructionGenerator with real OpenAI API calls.

These tests require OPENAI_API_KEY environment variable to be set.
They make actual LLM API calls and validate the quality of generated prompts.

Run with: pytest modules/tests/test_instruction_generator_integration.py -v
Skip with: pytest -m "not integration"
"""
import os
import pytest
from modules.generation import TaskInstructionGenerator
from modules.models import TaskSpec, Invariant, TerraformTask
from modules.generation.terraform.providers.gcp.single_resource_bank import build_task
from modules.generation.terraform.registry import terraform_task_registry


# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def require_openai_key():
    """Ensure OPENAI_API_KEY is set for integration tests."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or not api_key.strip():
        pytest.skip("OPENAI_API_KEY environment variable must be set for integration tests")
    return api_key


@pytest.fixture
def real_generator(require_openai_key):
    """Create a TaskInstructionGenerator with real LLM enabled."""
    return TaskInstructionGenerator(
        enable_llm=True,
        llm_retries=3,
        fallback_on_failure=True
    )


@pytest.fixture
def sample_gcp_storage_task():
    """Create a sample GCP storage bucket task."""
    spec = TaskSpec(
        version="1.0",
        task_id="integration-test-001",
        nonce="test-nonce-abc123",
        kind="gcp.storage.bucket",
        invariants=[
            Invariant(
                resource_type="google_storage_bucket",
                match={
                    "values.name": "test-integration-bucket",
                    "values.location": "US",
                    "values.storage_class": "STANDARD"
                }
            )
        ],
        metadata={
            "resource_keys": ["storage_bucket"],
            "hints": ["Use standard storage class", "Enable versioning"],
        }
    )
    return TerraformTask(
        engine="terraform",
        provider="gcp",
        validator_sa="validator@test-project.iam.gserviceaccount.com",
        spec=spec,
        instructions=None
    )


@pytest.fixture
def sample_gcp_compute_task():
    """Create a sample GCP compute instance task."""
    spec = TaskSpec(
        version="1.0",
        task_id="integration-test-002",
        nonce="test-nonce-def456",
        kind="gcp.compute.instance",
        invariants=[
            Invariant(
                resource_type="google_compute_instance",
                match={
                    "values.name": "test-vm",
                    "values.zone": "us-central1-a",
                    "values.machine_type": "e2-micro"
                }
            )
        ],
        metadata={
            "resource_keys": ["compute_instance"],
            "hints": ["Use minimal resources", "Include boot disk"],
        }
    )
    return TerraformTask(
        engine="terraform",
        provider="gcp",
        validator_sa="validator@test-project.iam.gserviceaccount.com",
        spec=spec,
        instructions=None
    )


class TestLLMPromptGeneration:
    """Test real LLM prompt generation with OpenAI API."""

    def test_generate_storage_bucket_instructions(self, real_generator, sample_gcp_storage_task):
        """Test generating instructions for a storage bucket task."""
        instructions = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")

        # Verify instructions were generated
        assert instructions
        assert len(instructions) > 100
        assert len(instructions) < 2000

        # Verify required content is present
        assert "storage bucket" in instructions.lower() or "bucket" in instructions.lower()
        assert "test-integration-bucket" in instructions
        assert "US" in instructions or "us" in instructions.lower()
        assert "terraform" in instructions.lower() or "gcp" in instructions.lower()

        # Verify packaging instructions
        assert "archive" in instructions.lower() or "zip" in instructions.lower()
        assert "terraform.tfstate" in instructions.lower()

        # Verify validator SA is mentioned
        assert "validator@test-project.iam.gserviceaccount.com" in instructions

        # Verify no disallowed content
        assert "readme" not in instructions.lower()
        assert "tutorial" not in instructions.lower()
        assert "documentation" not in instructions.lower()

        # Verify plain text (no markdown)
        assert "**" not in instructions
        assert "```" not in instructions
        assert not instructions.startswith("- ")
        assert not instructions.startswith("1. ")

    def test_generate_compute_instance_instructions(self, real_generator, sample_gcp_compute_task):
        """Test generating instructions for a compute instance task."""
        instructions = real_generator.generate(sample_gcp_compute_task, task_name="compute_instance")

        # Verify instructions were generated
        assert instructions
        assert len(instructions) > 100

        # Verify required content
        assert "compute instance" in instructions.lower() or "vm" in instructions.lower() or "virtual machine" in instructions.lower()
        assert "test-vm" in instructions
        assert "us-central1-a" in instructions
        assert "e2-micro" in instructions

        # Verify provider mention
        assert "gcp" in instructions.lower() or "google cloud" in instructions.lower()

        # Verify packaging instructions
        assert "archive" in instructions.lower() or "zip" in instructions.lower()
        assert "terraform.tfstate" in instructions.lower()

    def test_generate_with_hints(self, real_generator, sample_gcp_storage_task):
        """Test that generated instructions incorporate metadata hints."""
        instructions = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")

        # Check if hints are reflected (may be paraphrased)
        # The LLM should mention storage class or versioning in some form
        lowered = instructions.lower()
        has_storage_class_mention = any(term in lowered for term in ["storage class", "standard"])
        has_versioning_mention = any(term in lowered for term in ["version", "versioning"])

        # At least one hint should be reflected
        assert has_storage_class_mention or has_versioning_mention

    def test_instructions_are_varied(self, real_generator, sample_gcp_storage_task):
        """Test that multiple generations produce varied instructions."""
        instructions_1 = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")
        instructions_2 = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")
        instructions_3 = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")

        # All should be valid
        assert all([instructions_1, instructions_2, instructions_3])

        # Should be different (at least 2 should differ significantly)
        # Using similarity check - if too similar, they're likely identical
        def similarity_ratio(s1, s2):
            """Simple similarity check."""
            words1 = set(s1.lower().split())
            words2 = set(s2.lower().split())
            intersection = words1.intersection(words2)
            union = words1.union(words2)
            return len(intersection) / len(union) if union else 1.0

        sim_12 = similarity_ratio(instructions_1, instructions_2)
        sim_13 = similarity_ratio(instructions_1, instructions_3)
        sim_23 = similarity_ratio(instructions_2, instructions_3)

        # At least one pair should be less than 95% similar (shows variation)
        assert min(sim_12, sim_13, sim_23) < 0.95, "Instructions should show variation between generations"

    def test_instructions_word_count(self, real_generator, sample_gcp_storage_task):
        """Test that instructions are within reasonable word count."""
        instructions = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")

        word_count = len(instructions.split())

        # Should be under 300 words (prompt asks for under 220)
        assert word_count < 300, f"Instructions too long: {word_count} words"

        # Should be at least 50 words
        assert word_count > 50, f"Instructions too short: {word_count} words"

    def test_ascii_only_output(self, real_generator, sample_gcp_storage_task):
        """Test that instructions contain only ASCII characters."""
        instructions = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")

        # Check all characters are ASCII
        assert all(ord(c) < 128 for c in instructions), "Instructions should be ASCII-only"

        # Verify no common unicode characters
        assert "→" not in instructions
        assert "•" not in instructions
        assert "–" not in instructions
        assert "—" not in instructions


class TestRegistryIntegration:
    """Test integration with task registry using real LLM."""

    def test_registry_build_task_with_real_llm(self, require_openai_key):
        """Test building a task through registry with real LLM."""
        # Create registry with real LLM generator
        generator = TaskInstructionGenerator(enable_llm=True, llm_retries=3)

        task = terraform_task_registry.build_random_task(
            provider="gcp",
            validator_sa="validator@test-integration.iam.gserviceaccount.com",
            instruction_generator=generator
        )

        # Verify task has instructions
        assert task.instructions
        assert len(task.instructions) > 50

        # Verify prompt was set in spec
        assert task.spec.prompt == task.instructions

        # Verify basic requirements
        assert "terraform.tfstate" in task.instructions.lower()
        assert "archive" in task.instructions.lower() or "zip" in task.instructions.lower()

    def test_single_resource_bank_task(self, require_openai_key):
        """Test single resource bank task generation with real LLM."""
        generator = TaskInstructionGenerator(enable_llm=True, llm_retries=3)
        task = build_task(validator_sa="validator@test.iam.gserviceaccount.com")

        # Generate instructions
        instructions = generator.generate(task, task_name="single_resource")

        # Verify instructions
        assert instructions
        assert len(instructions) > 50

        # Verify invariant values are mentioned
        first_invariant = task.spec.invariants[0]
        first_value = str(next(iter(first_invariant.match.values())))
        assert first_value in instructions

    def test_multiple_tasks_with_different_kinds(self, require_openai_key):
        """Test generating instructions for different task kinds."""
        generator = TaskInstructionGenerator(enable_llm=True, llm_retries=3)

        task_builders = terraform_task_registry.get_task_builders("gcp")

        # Test at least 3 different task types
        tested_tasks = 0
        for task_name, builder in list(task_builders.items())[:3]:
            task = builder(validator_sa="validator@test.iam.gserviceaccount.com")

            # Generate instructions (will use generator from fixture or registry default)
            if task.instructions:
                instructions = task.instructions
            else:
                instructions = generator.generate(task, task_name=task_name)

            # Basic validation
            assert instructions
            assert len(instructions) > 50
            assert "terraform.tfstate" in instructions.lower()

            tested_tasks += 1

        assert tested_tasks >= 3, "Should test at least 3 different task types"


class TestErrorHandling:
    """Test error handling with real LLM."""

    def test_retry_mechanism_succeeds(self, require_openai_key):
        """Test that retry mechanism works with real API."""
        generator = TaskInstructionGenerator(
            enable_llm=True,
            llm_retries=3,
            fallback_on_failure=True
        )

        task = build_task(validator_sa="validator@test.iam.gserviceaccount.com")

        # This should succeed (either via LLM or fallback)
        instructions = generator.generate(task, task_name="test")

        assert instructions
        assert len(instructions) > 50

    def test_fallback_when_llm_produces_invalid_content(self, require_openai_key):
        """Test fallback mechanism when LLM output is invalid."""
        generator = TaskInstructionGenerator(
            enable_llm=True,
            llm_retries=2,
            fallback_on_failure=True,
            temperature=1.5  # Higher temperature might produce less reliable output
        )

        task = build_task(validator_sa="validator@test.iam.gserviceaccount.com")

        # Should get instructions (either valid LLM output or fallback)
        instructions = generator.generate(task, task_name="test")

        assert instructions
        # Verify it meets basic requirements
        assert "terraform.tfstate" in instructions.lower()


class TestContentValidation:
    """Test content validation with real LLM output."""

    def test_generated_content_has_no_disallowed_terms(self, real_generator, sample_gcp_storage_task):
        """Test that generated content doesn't include disallowed terms."""
        instructions = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")

        lowered = instructions.lower()

        # Check disallowed terms
        disallowed_terms = ["readme", "tutorial", "documentation", "guide", "walkthrough"]
        for term in disallowed_terms:
            assert term not in lowered, f"Disallowed term '{term}' found in instructions"

    def test_generated_content_has_required_submission_details(self, real_generator, sample_gcp_storage_task):
        """Test that generated content includes submission requirements."""
        instructions = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")

        lowered = instructions.lower()

        # Required terms
        assert "zip" in lowered or "archive" in lowered, "Should mention zip/archive"
        assert "terraform.tfstate" in lowered, "Should mention terraform.tfstate"

        # Should mention validator SA
        assert sample_gcp_storage_task.validator_sa in instructions

    def test_generated_content_mentions_all_invariant_values(self, real_generator):
        """Test that generated content mentions all invariant values."""
        spec = TaskSpec(
            version="1.0",
            task_id="test-invariants-001",
            nonce="test-nonce-xyz789",
            kind="gcp.compute.instance",
            invariants=[
                Invariant(
                    resource_type="google_compute_instance",
                    match={
                        "values.name": "unique-vm-name-12345",
                        "values.zone": "europe-west2-b",
                        "values.machine_type": "n1-standard-2"
                    }
                )
            ],
            metadata={"resource_keys": ["compute_instance"]}
        )
        task = TerraformTask(
            engine="terraform",
            provider="gcp",
            validator_sa="validator@test-project.iam.gserviceaccount.com",
            spec=spec,
            instructions=None
        )

        instructions = real_generator.generate(task, task_name="compute_instance")

        # All invariant values should be mentioned
        assert "unique-vm-name-12345" in instructions
        assert "europe-west2-b" in instructions
        assert "n1-standard-2" in instructions


class TestPromptQuality:
    """Test the quality and consistency of generated prompts."""

    def test_prompt_is_imperative_voice(self, real_generator, sample_gcp_storage_task):
        """Test that prompt uses imperative voice (commands/instructions)."""
        instructions = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")

        # Check for imperative indicators (common verbs at start of sentences)
        imperative_indicators = ["create", "configure", "set", "use", "ensure", "deploy", "provision", "package", "grant"]
        lowered = instructions.lower()

        # Should have at least one imperative verb
        has_imperative = any(indicator in lowered for indicator in imperative_indicators)
        assert has_imperative, "Instructions should use imperative voice"

    def test_prompt_describes_resource_naturally(self, real_generator, sample_gcp_storage_task):
        """Test that prompt describes resources in natural language."""
        instructions = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")

        lowered = instructions.lower()

        # Should use natural language descriptions, not technical jargon
        # Should mention "storage bucket" or "bucket" rather than "google_storage_bucket"
        assert "storage bucket" in lowered or "bucket" in lowered

        # Should NOT contain resource type names
        assert "google_storage_bucket" not in lowered
        assert "resource_type" not in lowered

    def test_prompt_coherence(self, real_generator, sample_gcp_compute_task):
        """Test that prompt is coherent and readable."""
        instructions = real_generator.generate(sample_gcp_compute_task, task_name="compute_instance")

        # Basic coherence checks
        sentences = instructions.split(". ")

        # Should have multiple sentences
        assert len(sentences) >= 3, "Instructions should contain multiple sentences"

        # No sentence should be too long (> 50 words)
        for sentence in sentences:
            word_count = len(sentence.split())
            assert word_count < 50, f"Sentence too long: {word_count} words"

        # Should start with a capital letter
        assert instructions[0].isupper(), "Instructions should start with capital letter"

    def test_prompt_no_repetition(self, real_generator, sample_gcp_storage_task):
        """Test that prompt doesn't have obvious repetition."""
        instructions = real_generator.generate(sample_gcp_storage_task, task_name="storage_bucket")

        # Check for repeated phrases (simple check)
        words = instructions.lower().split()

        # Check for sequences of 5+ repeated words
        for i in range(len(words) - 10):
            sequence = " ".join(words[i:i+5])
            rest_of_text = " ".join(words[i+5:])

            # Same 5-word sequence shouldn't appear again
            assert sequence not in rest_of_text, f"Found repeated sequence: {sequence}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "integration"])
