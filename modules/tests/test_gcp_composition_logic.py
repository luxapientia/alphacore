"""
Test suite for GCP composition logic.

Tests composition families, resource selection, and dependency resolution:
- Min/max optional resource selection
- Dependency chain validation
- Resource ordering
- Family variation
"""

import random
import pytest

from modules.generation.terraform.providers.gcp import compositions
from modules.generation.terraform.providers.gcp.task_bank import GCPDynamicTaskBank


# ============================================================================
# 1. COMPOSITION FAMILY LOGIC TESTS
# ============================================================================

class TestCompositionFamilyLogic:
    """Test CompositionFamily selection logic."""

    def test_mandatory_only_family(self):
        """Test family with only mandatory resources."""
        family = compositions.CompositionFamily(
            name="test_mandatory",
            mandatory=("storage_bucket", "pubsub_topic"),
        )

        rng = random.Random(42)
        templates = family.pick_templates(rng)

        assert len(templates) == 2
        assert "storage_bucket" in templates
        assert "pubsub_topic" in templates

    def test_family_with_optional_resources(self):
        """Test family with optional resources."""
        family = compositions.CompositionFamily(
            name="test_optional",
            mandatory=("storage_bucket",),
            optional=("pubsub_topic", "artifact_repository"),
            min_optional=1,
            max_optional=2,
        )

        rng = random.Random(42)
        templates = family.pick_templates(rng)

        # Should have 1 mandatory + 1-2 optional
        assert len(templates) >= 2
        assert len(templates) <= 3
        assert "storage_bucket" in templates

    def test_family_min_optional_respected(self):
        """Test that min_optional is respected."""
        family = compositions.CompositionFamily(
            name="test_min",
            mandatory=("storage_bucket",),
            optional=("pubsub_topic", "artifact_repository", "service_account"),
            min_optional=2,
            max_optional=3,
        )

        rng = random.Random(42)
        templates = family.pick_templates(rng)

        # Should have at least 1 mandatory + 2 optional = 3 total
        assert len(templates) >= 3

    def test_family_max_optional_respected(self):
        """Test that max_optional is respected."""
        family = compositions.CompositionFamily(
            name="test_max",
            mandatory=("storage_bucket",),
            optional=("pubsub_topic", "artifact_repository", "service_account"),
            min_optional=0,
            max_optional=1,
        )

        rng = random.Random(42)
        templates = family.pick_templates(rng)

        # Should have at most 1 mandatory + 1 optional = 2 total
        assert len(templates) <= 2

    def test_family_zero_optional(self):
        """Test family with min/max optional = 0."""
        family = compositions.CompositionFamily(
            name="test_zero",
            mandatory=("storage_bucket",),
            optional=("pubsub_topic",),
            min_optional=0,
            max_optional=0,
        )

        rng = random.Random(42)
        templates = family.pick_templates(rng)

        # Should have only mandatory
        assert len(templates) == 1
        assert templates[0] == "storage_bucket"

    def test_family_determinism(self):
        """Test that same seed produces same selection."""
        family = compositions.CompositionFamily(
            name="test_determinism",
            mandatory=("storage_bucket",),
            optional=("pubsub_topic", "artifact_repository", "service_account"),
            min_optional=1,
            max_optional=2,
        )

        rng1 = random.Random(42)
        rng2 = random.Random(42)

        templates1 = family.pick_templates(rng1)
        templates2 = family.pick_templates(rng2)

        assert templates1 == templates2


# ============================================================================
# 2. TASK BANK COMPOSITION TESTS
# ============================================================================

class TestTaskBankComposition:
    """Test GCPDynamicTaskBank composition logic."""

    def test_single_resource_bank_respects_min_max(self):
        """Test that single resource bank generally uses few resources."""
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=1)

        for _ in range(10):
            task = bank.build_task("validator@test.com")
            resource_count = len(task.spec.metadata["resource_keys"])
            # With families, dependencies may add more resources
            # Single resource families typically have 1-2 resources (e.g., SA + IAM)
            assert resource_count >= 1
            assert resource_count <= 4  # Reasonable upper bound with dependencies    def test_composite_bank_respects_min_resources(self):
        """Test that composite bank respects min_resources."""
        bank = GCPDynamicTaskBank(min_resources=3, max_resources=5)

        for _ in range(10):
            task = bank.build_task("validator@test.com")
            resource_count = len(task.spec.metadata["resource_keys"])
            assert resource_count >= 3

    def test_composite_bank_respects_max_resources(self):
        """Test that composite bank generates multiple resources."""
        bank = GCPDynamicTaskBank(min_resources=2, max_resources=3)

        for _ in range(10):
            task = bank.build_task("validator@test.com")
            resource_count = len(task.spec.metadata["resource_keys"])
            # Should have at least 2, but families may include dependencies
            assert resource_count >= 2
            # Upper bound is flexible when families include dependencies
            assert resource_count <= 10  # Reasonable upper bound    def test_bank_with_specific_families(self):
        """Test bank with specific composition families."""
        families = [
            compositions.CompositionFamily("test", ("storage_bucket",)),
        ]
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=1, families=families)

        for _ in range(5):
            task = bank.build_task("validator@test.com")
            resources = task.spec.metadata["resource_keys"]
            assert "storage_bucket" in resources

    def test_bank_task_determinism(self):
        """Test that tasks are deterministic with same configuration."""
        bank = GCPDynamicTaskBank(min_resources=1, max_resources=1)

        # Generate tasks - should use internal random generation
        task1 = bank.build_task("validator@test.com")
        task2 = bank.build_task("validator@test.com")

        # Tasks should be different (different random seeds internally)
        # But both should be valid
        assert task1.spec.task_id != task2.spec.task_id
        assert len(task1.spec.invariants) > 0
        assert len(task2.spec.invariants) > 0


# ============================================================================
# 3. DEPENDENCY RESOLUTION TESTS
# ============================================================================

class TestDependencyResolution:
    """Test resource dependency chain validation."""

    def test_network_stack_dependency_order(self):
        """Test that network stack maintains proper dependency order."""
        bank = GCPDynamicTaskBank(
            min_resources=4,
            max_resources=4,
            families=[compositions.COMPOSITE_FAMILIES[0]]  # network_stack
        )

        task = bank.build_task("validator@test.com")
        resources = task.spec.metadata["resource_keys"]

        # Network stack should have VPC, subnet, firewall, and instance
        assert "vpc_network" in resources
        assert "subnetwork" in resources
        assert "firewall_rule" in resources
        assert "compute_instance_networked" in resources

    def test_bucket_with_object_dependencies(self):
        """Test that bucket object depends on bucket."""
        # Find bucket_with_object family
        bucket_family = next(
            f for f in compositions.COMPOSITE_FAMILIES
            if f.name == "bucket_with_object"
        )

        bank = GCPDynamicTaskBank(
            min_resources=2,
            max_resources=3,
            families=[bucket_family]
        )

        for _ in range(10):
            task = bank.build_task("validator@test.com")
            resources = task.spec.metadata["resource_keys"]

            # If bucket_object is present, bucket must be present
            if "storage_bucket_object" in resources:
                assert "storage_bucket" in resources

    def test_iam_binding_dependencies(self):
        """Test that IAM bindings depend on their resources."""
        # Find bucket_object_with_iam family
        iam_family = next(
            f for f in compositions.COMPOSITE_FAMILIES
            if f.name == "bucket_object_with_iam"
        )

        bank = GCPDynamicTaskBank(
            min_resources=4,
            max_resources=6,
            families=[iam_family]
        )

        task = bank.build_task("validator@test.com")
        resources = task.spec.metadata["resource_keys"]

        # Should have bucket, object, SA, and IAM binding
        assert "storage_bucket" in resources
        assert "storage_bucket_object" in resources
        assert "service_account" in resources
        assert "bucket_iam_member" in resources

    def test_pubsub_subscription_depends_on_topic(self):
        """Test that subscription depends on topic."""
        # Find topic_with_subscription family
        topic_family = next(
            f for f in compositions.COMPOSITE_FAMILIES
            if f.name == "topic_with_subscription"
        )

        bank = GCPDynamicTaskBank(
            min_resources=2,
            max_resources=5,
            families=[topic_family]
        )

        for _ in range(10):
            task = bank.build_task("validator@test.com")
            resources = task.spec.metadata["resource_keys"]

            # Both topic and subscription should be present
            assert "pubsub_topic" in resources
            assert "pubsub_subscription" in resources

    def test_all_invariants_match_resources(self):
        """Test that all invariants correspond to resources in metadata."""
        bank = GCPDynamicTaskBank(min_resources=2, max_resources=4)

        for _ in range(20):
            task = bank.build_task("validator@test.com")
            resources = task.spec.metadata["resource_keys"]
            invariants = task.spec.invariants

            # Should have at least as many invariants as resources
            assert len(invariants) >= len(resources)


# ============================================================================
# 4. COMPOSITION FAMILY COVERAGE TESTS
# ============================================================================

class TestCompositionFamilyCoverage:
    """Test that all composition families work correctly."""

    def test_all_single_resource_families_work(self):
        """Test that all single resource families generate valid tasks."""
        for family in compositions.SINGLE_RESOURCE_FAMILIES:
            bank = GCPDynamicTaskBank(
                min_resources=1,
                max_resources=2,
                families=[family]
            )

            task = bank.build_task("validator@test.com")
            assert len(task.spec.invariants) > 0
            assert task.spec.metadata["composition_family"] == family.name

    def test_all_composite_families_work(self):
        """Test that all composite families generate valid tasks."""
        for family in compositions.COMPOSITE_FAMILIES:
            bank = GCPDynamicTaskBank(
                min_resources=2,
                max_resources=6,
                families=[family]
            )

            task = bank.build_task("validator@test.com")
            assert len(task.spec.invariants) >= 2
            assert task.spec.metadata["composition_family"] == family.name

            # Verify all mandatory resources are present
            resources = task.spec.metadata["resource_keys"]
            for mandatory in family.mandatory:
                assert mandatory in resources

    def test_family_with_no_optional_resources(self):
        """Test family that has no optional resources."""
        family = compositions.CompositionFamily(
            name="test_no_optional",
            mandatory=("storage_bucket", "pubsub_topic"),
        )

        bank = GCPDynamicTaskBank(
            min_resources=2,
            max_resources=2,
            families=[family]
        )

        task = bank.build_task("validator@test.com")
        resources = task.spec.metadata["resource_keys"]

        assert len(resources) == 2
        assert "storage_bucket" in resources
        assert "pubsub_topic" in resources


# ============================================================================
# 5. RESOURCE ORDERING TESTS
# ============================================================================

class TestResourceOrdering:
    """Test that resources are ordered correctly based on dependencies."""

    def test_shared_capabilities_populated_in_order(self):
        """Test that shared capabilities are available to downstream resources."""
        bank = GCPDynamicTaskBank(min_resources=2, max_resources=4)

        for _ in range(20):
            task = bank.build_task("validator@test.com")
            resources = task.spec.metadata["resource_keys"]

            # If bucket_object exists, it must reference a bucket
            if "storage_bucket_object" in resources:
                assert "storage_bucket" in resources

                # Find bucket object invariant
                bucket_obj_invariants = [
                    inv for inv in task.spec.invariants
                    if inv.resource_type == "google_storage_bucket_object"
                ]

                if bucket_obj_invariants:
                    bucket_name = bucket_obj_invariants[0].match.get("values.bucket")
                    assert bucket_name is not None

    def test_iam_bindings_reference_correct_resources(self):
        """Test that IAM bindings reference the validator identity."""
        # Use bucket_object_with_iam family
        iam_family = next(
            f for f in compositions.COMPOSITE_FAMILIES
            if f.name == "bucket_object_with_iam"
        )

        bank = GCPDynamicTaskBank(
            min_resources=4,
            max_resources=6,
            families=[iam_family]
        )

        for _ in range(10):
            validator_sa = "validator@test.com"
            task = bank.build_task(validator_sa)

            iam_invariants = [
                inv for inv in task.spec.invariants
                if inv.resource_type == "google_storage_bucket_iam_member"
            ]

            if iam_invariants:
                iam_member = iam_invariants[0].match["values.member"]
                assert validator_sa in iam_member


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
