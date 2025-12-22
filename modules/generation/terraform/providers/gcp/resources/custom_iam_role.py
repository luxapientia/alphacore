from __future__ import annotations

from modules.models import Invariant
from modules.generation.terraform.providers.gcp import helpers
from modules.generation.terraform.resource_templates import (
    ResourceInstance,
    ResourceTemplate,
    TemplateContext,
)


def _build_custom_role(ctx: TemplateContext) -> ResourceInstance:
    role_id = helpers.custom_role_id(ctx.nonce[:8])
    permissions = helpers.custom_role_permissions(ctx.rng)

    invariant = Invariant(
        resource_type="google_project_iam_custom_role",
        match={
            "values.role_id": role_id,
            # Avoid forcing the prompt to repeat long permission lists verbatim.
            # We validate that at least one permission is pinned (first item).
            "values.permissions.0": permissions[0],
        },
    )
    hint = f"Create a custom IAM role {role_id} with permissions {permissions}."
    return ResourceInstance(
        invariants=[invariant],
        prompt_hints=[hint],
        shared_values={"custom_iam_role": {"role_id": role_id}},
    )


def get_templates() -> list[ResourceTemplate]:
    return [
        ResourceTemplate(
            key="custom_iam_role",
            kind="custom iam role",
            provides=("custom_iam_role",),
            requires=(),
            builder=_build_custom_role,
            base_hints=("Define custom role with minimal permissions set.",),
            weight=0.75,
        )
    ]
