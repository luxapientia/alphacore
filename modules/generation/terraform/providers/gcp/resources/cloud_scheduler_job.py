from __future__ import annotations

from modules.models import Invariant
from modules.generation.terraform.providers.gcp import helpers
from modules.generation.terraform.resource_templates import (
    ResourceInstance,
    ResourceTemplate,
    TemplateContext,
)


def _build_scheduler_job(ctx: TemplateContext) -> ResourceInstance:
    name = helpers.scheduler_job_name(ctx.nonce[:8])
    schedule = helpers.scheduler_job_schedule(ctx.rng)
    # Keep the job consistent; timezone is a default behavior and not a meaningful constraint.
    time_zone = "UTC"

    # Use Pub/Sub target if topic is available, otherwise HTTP
    shared_topic = ctx.shared.get("pubsub_topic")
    if shared_topic:
        topic_name = shared_topic.get("name") or helpers.pubsub_topic_id(ctx.nonce[:8])
        target_type = "pubsub"
        target_config = f"pubsub_target with topic {topic_name}"
        invariant = Invariant(
            resource_type="google_cloud_scheduler_job",
            match={
                "values.name": name,
                "values.schedule": schedule,
                "values.pubsub_target.0.topic_name": topic_name,
            },
        )
    else:
        http_uri = f"https://example.com/webhook/{ctx.nonce[:8]}"
        target_type = "http"
        target_config = f"http_target with uri {http_uri}"
        invariant = Invariant(
            resource_type="google_cloud_scheduler_job",
            match={
                "values.name": name,
                "values.schedule": schedule,
                "values.http_target.0.uri": http_uri,
            },
        )

    hint = f"Create a Cloud Scheduler job {name} with schedule {schedule} and {target_config}."
    return ResourceInstance(
        invariants=[invariant],
        prompt_hints=[hint],
        shared_values={"cloud_scheduler_job": {"name": name}},
    )


def get_templates() -> list[ResourceTemplate]:
    return [
        ResourceTemplate(
            key="cloud_scheduler_job",
            kind="cloud scheduler job",
            provides=("cloud_scheduler_job",),
            requires=(),
            builder=_build_scheduler_job,
            base_hints=("Use minimal schedule frequency to minimize cost.",),
            weight=0.8,
        )
    ]
