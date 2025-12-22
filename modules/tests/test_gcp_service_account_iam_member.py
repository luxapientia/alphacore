import random

from modules.generation.terraform.providers.gcp import helpers
from modules.generation.terraform.providers.gcp.resources import (
    service_account_iam_member,
    secret_manager_secret_iam,
    dns_record_set,
)


class TestServiceAccountIAMMember:
    def test_sa_iam_template_exists(self):
        templates = service_account_iam_member.get_templates()
        assert len(templates) == 1
        assert templates[0].key == "service_account_iam_member"
        assert templates[0].requires == ("service_account",)

    def test_sa_iam_role_helper(self):
        role = helpers.service_account_iam_role(random.Random(42))
        assert role in helpers.SERVICE_ACCOUNT_IAM_ROLES


class TestResourceDependencyContracts:
    def test_dependency_chains_are_valid(self):
        templates = secret_manager_secret_iam.get_templates()
        assert "secret_manager_secret" in templates[0].requires

        templates = dns_record_set.get_templates()
        assert "dns_zone" in templates[0].requires

        templates = service_account_iam_member.get_templates()
        assert "service_account" in templates[0].requires
