from __future__ import annotations

from modules.models import Invariant
from modules.generation.terraform.providers.gcp import helpers
from modules.generation.terraform.resource_templates import (
    ResourceInstance,
    ResourceTemplate,
    TemplateContext,
)


def _build_logging_sink(ctx: TemplateContext) -> ResourceInstance:
    name = helpers.logging_sink_name(ctx.nonce[:8])
    log_filter = helpers.logging_filter(ctx.rng)

    # Logging sinks require a real destination resource (e.g., an existing bucket).
    # Do not generate standalone sinks with fictional destinations.
    shared_bucket = ctx.shared.get("bucket")
    if not shared_bucket:
        raise RuntimeError("logging_project_sink template requires an existing bucket.")
    bucket_name = shared_bucket.get("name") or helpers.bucket_name(ctx.nonce[:10])
    destination = f"storage.googleapis.com/{bucket_name}"
    dest_desc = f"bucket {bucket_name}"

    invariant = Invariant(
        resource_type="google_logging_project_sink",
        match={
            "values.name": name,
            "values.destination": destination,
            "values.filter": log_filter,
        },
    )
    hint = f"Create a logging project sink {name} sending logs matching filter '{log_filter}' to {dest_desc}."
    return ResourceInstance(
        invariants=[invariant],
        prompt_hints=[hint],
        shared_values={"logging_project_sink": {"name": name}},
    )


def get_templates() -> list[ResourceTemplate]:
    return [
        ResourceTemplate(
            key="logging_project_sink",
            kind="logging project sink",
            provides=("logging_project_sink",),
            requires=("bucket",),
            builder=_build_logging_sink,
            base_hints=("Configure a sink that exports logs to the provided storage bucket destination.",),
            weight=0.75,
        )
    ]
