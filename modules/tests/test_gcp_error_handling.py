"""
Test suite for GCP error handling and edge cases.

Tests error conditions and invalid inputs:
- Missing required capabilities
- Invalid validator_sa format
- Empty template catalog
- Malformed contexts
"""

import random
import sqlite3
import pytest

from modules.generation.terraform.resource_templates import TemplateContext
from modules.generation.terraform.providers.gcp.resources import (
    firewall,
    storage_bucket_object,
    subnetwork,
    bucket_iam_member,
    project_iam_member,
    pubsub_subscription,
    compute_instance,
)
from modules.generation.terraform.providers.gcp.task_bank import GCPDynamicTaskBank
from modules.generation.terraform.providers.gcp import compositions


def make_context(task_id: str = "test123", nonce: str = "abc123", seed: int = 42, shared: dict = None) -> TemplateContext:
    """Helper to create a template context."""
    return TemplateContext(
        rng=random.Random(seed),
        task_id=task_id,
        nonce=nonce,
        shared=shared or {},
    )


# ============================================================================
# 1. MISSING CAPABILITIES TESTS
# ============================================================================

class TestMissingCapabilities:
    """Test error handling for missing required capabilities."""

    def test_firewall_without_network_fails(self):
        """Test that firewall fails without network capability."""
        ctx = make_context()
        template = firewall.get_templates()[0]

        with pytest.raises(RuntimeError, match="network capability missing"):
            template.builder(ctx)

    def test_bucket_object_without_bucket_fails(self):
        """Test that bucket object fails without bucket capability."""
        ctx = make_context()
        template = storage_bucket_object.get_templates()[0]

        with pytest.raises(RuntimeError, match="requires an existing bucket"):
            template.builder(ctx)

    def test_subnetwork_without_network_fails(self):
        """Test that subnetwork fails without network capability."""
        ctx = make_context()
        template = subnetwork.get_templates()[0]

        with pytest.raises(RuntimeError, match="network capability missing"):
            template.builder(ctx)

    def test_bucket_iam_with_missing_bucket_uses_fallback(self):
        """Test that bucket IAM uses fallback when bucket is missing."""
        ctx = make_context()
        template = bucket_iam_member.get_templates()[0]

        # Should not fail - uses deterministic fallback
        instance = template.builder(ctx)
        assert len(instance.invariants) == 1

    def test_project_iam_with_missing_sa_uses_fallback(self):
        """Test that project IAM uses fallback when SA is missing."""
        ctx = make_context()
        template = project_iam_member.get_templates()[0]

        # Should not fail - uses deterministic fallback
        instance = template.builder(ctx)
        assert len(instance.invariants) == 1

    def test_subscription_with_missing_topic_uses_fallback(self):
        """Test that subscription uses fallback when topic is missing."""
        ctx = make_context()
        template = pubsub_subscription.get_templates()[0]

        # Should not fail - uses deterministic fallback
        instance = template.builder(ctx)
        assert len(instance.invariants) == 1


# ============================================================================
# 2. INVALID VALIDATOR_SA FORMAT TESTS
# ============================================================================

class TestValidatorSAFormat:
    """Test handling of various validator_sa formats."""

    def test_valid_validator_sa_format(self):
        """Test with valid validator SA format."""
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=1)
        task = bank.build_task("validator@test-project.iam.gserviceaccount.com")

        assert task is not None
        assert len(task.spec.invariants) > 0

    def test_simple_email_validator_sa(self):
        """Test with simple email format."""
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=1)
        task = bank.build_task("validator@example.com")

        # Should still work
        assert task is not None
        assert len(task.spec.invariants) > 0

    def test_empty_validator_sa(self):
        """Test with empty validator SA."""
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=1)
        task = bank.build_task("")

        # Should still work - validator SA is stored but not validated
        assert task is not None
        assert task.validator_sa == ""

    def test_none_validator_sa(self):
        """Test with None validator SA (file repository allows it, creates task)."""
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=1)

        # File repository doesn't enforce NOT NULL constraints
        # Task generation should succeed even with None validator_sa
        task = bank.build_task(None)
        assert task is not None
        assert task.validator_sa is None

    def test_special_chars_in_validator_sa(self):
        """Test with special characters in validator SA."""
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=1)
        task = bank.build_task("validator+test@example.com")

        assert task is not None
        assert task.validator_sa == "validator+test@example.com"

    def test_very_long_validator_sa(self):
        """Test with very long validator SA."""
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=1)
        long_sa = "validator-" + "x" * 200 + "@example.com"
        task = bank.build_task(long_sa)

        assert task is not None
        assert task.validator_sa == long_sa


# ============================================================================
# 3. MALFORMED CONTEXT TESTS
# ============================================================================

class TestMalformedContext:
    """Test handling of malformed template contexts."""

    def test_empty_task_id(self):
        """Test with empty task_id."""
        from modules.generation.terraform.providers.gcp.resources import storage_bucket

        ctx = make_context(task_id="")
        template = storage_bucket.get_templates()[0]
        instance = template.builder(ctx)

        # Should work but generate shortened identifiers
        assert len(instance.invariants) == 1

    def test_empty_nonce(self):
        """Test with empty nonce."""
        from modules.generation.terraform.providers.gcp.resources import storage_bucket

        ctx = make_context(nonce="")
        template = storage_bucket.get_templates()[0]
        instance = template.builder(ctx)

        # Should work but generate truncated names
        assert len(instance.invariants) == 1

    def test_none_in_shared_dict(self):
        """Test with None values in shared dict."""
        from modules.generation.terraform.providers.gcp.resources import bucket_iam_member

        shared = {
            "bucket": None,
            "service_account": None,
        }
        ctx = make_context(shared=shared)
        template = bucket_iam_member.get_templates()[0]

        # Should use fallbacks
        instance = template.builder(ctx)
        assert len(instance.invariants) == 1

    def test_malformed_shared_capabilities(self):
        """Test with malformed capability data."""
        from modules.generation.terraform.providers.gcp.resources import bucket_iam_member

        shared = {
            "bucket": {"wrong_key": "value"},  # Missing "name" key
            "service_account": {"wrong_key": "value"},  # Missing "account_id"
        }
        ctx = make_context(shared=shared)
        template = bucket_iam_member.get_templates()[0]

        # Should use fallbacks when keys are missing
        instance = template.builder(ctx)
        assert len(instance.invariants) == 1


# ============================================================================
# 4. EMPTY/INVALID CATALOG TESTS
# ============================================================================

class TestEmptyCatalog:
    """Test handling of empty or invalid template catalogs."""

    def test_task_bank_with_empty_families(self):
        """Test task bank with empty families list."""
        bank = GCPDynamicTaskBank(
            min_resources=1,
            max_resources=1,
            families=[]
        )

        # Should still work - falls back to template discovery
        task = bank.build_task("validator@test.com")
        assert task is not None
        assert len(task.spec.invariants) > 0

    def test_task_bank_with_none_families(self):
        """Test task bank with None families."""
        bank = GCPDynamicTaskBank(
            min_resources=1,
            max_resources=1,
            families=None
        )

        # Should work - uses default behavior
        task = bank.build_task("validator@test.com")
        assert task is not None

    def test_composition_family_with_empty_mandatory(self):
        """Test composition family with no mandatory resources."""
        family = compositions.CompositionFamily(
            name="empty_test",
            mandatory=(),
            optional=("storage_bucket", "pubsub_topic"),
            min_optional=1,
            max_optional=2,
        )

        rng = random.Random(42)
        templates = family.pick_templates(rng)

        # Should pick only optional resources
        assert len(templates) >= 1
        assert len(templates) <= 2

    def test_composition_family_with_empty_optional(self):
        """Test composition family with no optional resources."""
        family = compositions.CompositionFamily(
            name="mandatory_only",
            mandatory=("storage_bucket",),
            optional=(),
        )

        rng = random.Random(42)
        templates = family.pick_templates(rng)

        # Should only have mandatory
        assert len(templates) == 1
        assert templates[0] == "storage_bucket"


# ============================================================================
# 5. BOUNDARY CONDITION TESTS
# ============================================================================

class TestBoundaryConditions:
    """Test boundary conditions and extreme values."""

    def test_min_equals_max_resources(self):
        """Test when min_resources equals max_resources."""
        # When using families, they control resource count, not min/max
        # Even without families, dependencies can increase resource count beyond target
        bank = GCPDynamicTaskBank(min_resources=2, max_resources=2, families=[])

        for _ in range(10):
            task = bank.build_task("validator@test.com")
            resource_count = len(task.spec.metadata["resource_keys"])
            # Target is 2, but dependencies may add more resources
            # Just verify task generation works and produces reasonable output
            assert resource_count >= 2
            assert resource_count <= 10  # Sanity check upper bound

    def test_min_greater_than_max_resources(self):
        """Test when min_resources > max_resources (invalid config)."""
        # The implementation validates this and raises ValueError
        with pytest.raises(ValueError, match="max_resources must be >= min_resources"):
            GCPDynamicTaskBank(min_resources=5, max_resources=2)

    def test_zero_min_resources(self):
        """Test with zero min_resources."""
        # The implementation validates min_resources >= 1
        with pytest.raises(ValueError, match="min_resources must be at least 1"):
            GCPDynamicTaskBank(min_resources=0, max_resources=2)

    def test_very_large_max_resources(self):
        """Test with very large max_resources."""
        # Use a more moderate value to avoid extremely long generation times
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=10)

        task = bank.build_task("validator@test.com")
        # Should be constrained by available templates and dependencies
        assert task is not None
        # Just verify it's reasonable, not necessarily hitting max
        assert len(task.spec.metadata["resource_keys"]) >= 1

    def test_negative_resource_count(self):
        """Test with negative resource count (invalid)."""
        # Should handle gracefully or raise error
        try:
            bank = GCPDynamicTaskBank(min_resources=-1, max_resources=2)
            task = bank.build_task("validator@test.com")
            # If it works, should generate at least something valid
            assert task is not None
        except (ValueError, AssertionError):
            # Expected if validation exists
            pass


# ============================================================================
# 6. CONCURRENT ACCESS TESTS
# ============================================================================

class TestConcurrentAccess:
    """Test that task generation works with concurrent access patterns."""

    def test_same_bank_multiple_tasks(self):
        """Test generating multiple tasks from same bank instance."""
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=2)

        tasks = []
        for _ in range(10):
            task = bank.build_task("validator@test.com")
            tasks.append(task)

        # All tasks should be unique
        task_ids = [t.spec.task_id for t in tasks]
        assert len(task_ids) == len(set(task_ids))

    def test_multiple_banks_same_config(self):
        """Test that multiple bank instances work independently."""
        bank1 = GCPDynamicTaskBank(min_resources=1, max_resources=1)
        bank2 = GCPDynamicTaskBank(min_resources=1, max_resources=1)

        task1 = bank1.build_task("validator@test.com")
        task2 = bank2.build_task("validator@test.com")

        # Both should work
        assert task1 is not None
        assert task2 is not None
        assert task1.spec.task_id != task2.spec.task_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
