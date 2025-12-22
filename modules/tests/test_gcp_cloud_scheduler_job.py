import random

from modules.generation.terraform.providers.gcp import helpers
from modules.generation.terraform.providers.gcp.resources import (
    cloud_scheduler_job,
)
from modules.generation.terraform.resource_templates import TemplateContext


class TestCloudSchedulerJob:
    def test_scheduler_job_template_exists(self):
        templates = cloud_scheduler_job.get_templates()
        assert len(templates) == 1
        assert templates[0].key == "cloud_scheduler_job"
        assert templates[0].kind == "cloud scheduler job"

    def test_scheduler_job_builds_with_pubsub_target(self):
        ctx = TemplateContext(
            nonce="test12345678",
            task_id="task001",
            rng=random.Random(42),
            shared={"pubsub_topic": {"name": "test-topic"}},
        )
        instance = cloud_scheduler_job._build_scheduler_job(ctx)

        assert len(instance.invariants) == 1
        inv = instance.invariants[0]
        assert inv.resource_type == "google_cloud_scheduler_job"
        assert "values.name" in inv.match
        assert "values.schedule" in inv.match
        assert "values.pubsub_target.0.topic_name" in inv.match
        assert inv.match["values.pubsub_target.0.topic_name"] == "test-topic"

    def test_scheduler_job_builds_with_http_target(self):
        ctx = TemplateContext(
            nonce="test12345678",
            task_id="task001",
            rng=random.Random(42),
            shared={},
        )
        instance = cloud_scheduler_job._build_scheduler_job(ctx)

        assert len(instance.invariants) == 1
        inv = instance.invariants[0]
        assert inv.resource_type == "google_cloud_scheduler_job"
        assert "values.http_target.0.uri" in inv.match

    def test_scheduler_job_schedule_helper(self):
        schedule = helpers.scheduler_job_schedule(random.Random(42))
        assert schedule in helpers.SCHEDULER_JOB_SCHEDULES
        assert "*" in schedule
