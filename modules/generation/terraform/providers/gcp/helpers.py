"""
Utility helpers and reusable building blocks for GCP Terraform tasks.

These functions centralise the entropy we inject into simple primitives
so that individual task builders can compose richer scenarios (for example,
creating a network, subnetwork, firewall, and instance that share the same
nonce-derived slug).
"""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

_SYSTEM_RANDOM = random.SystemRandom()

# Cheap regions/zones so miners can complete runs without incurring heavy spend.
REGION_TO_ZONES: Dict[str, Tuple[str, ...]] = {
    "us-central1": ("us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"),
    "us-east1": ("us-east1-b", "us-east1-c", "us-east1-d"),
    "europe-west1": ("europe-west1-b", "europe-west1-c", "europe-west1-d"),
}

# Budget-conscious machine types that still give enough CPU to apply plans.
CHEAP_MACHINE_TYPES = ("e2-micro", "e2-small", "e2-medium")

# Stick to specific regions instead of vague multi-regions (e.g. "EU") so
# prompts never ask miners to deploy to ambiguous locations.
BUCKET_LOCATIONS = (
    "US-CENTRAL1",
    "US-EAST1",
    "US-WEST1",
    "EUROPE-WEST1",
    "ASIA-SOUTHEAST1",
)
BUCKET_STORAGE_CLASSES = ("STANDARD", "NEARLINE", "COLDLINE")
ARTIFACT_LOCATIONS = ("us-central1", "us-east1", "us-west1", "europe-west1", "asia-southeast1")
ARTIFACT_FORMATS = ("DOCKER", "PYTHON")
# Validation should require read-only permissions; keep generated IAM roles conservative.
BUCKET_IAM_ROLES = ("roles/storage.objectViewer",)
PROJECT_IAM_ROLES = ("roles/viewer",)
PUBSUB_RETENTION_WINDOWS = ("600s", "900s", "1200s")
PUBSUB_ACK_DEADLINES = (10, 20, 30, 60)
PUBSUB_EXPIRATION_TTLS = ("86400s", "172800s", "259200s")
SCHEDULER_JOB_SCHEDULES = ("*/5 * * * *", "*/10 * * * *", "*/15 * * * *", "0 * * * *")
SECRET_IAM_ROLES = ("roles/secretmanager.secretAccessor",)
LOGGING_FILTERS = (
    'resource.type="gce_instance"',
    'severity="ERROR"',
    'resource.type="cloud_function"',
    'logName="projects/PROJECT_ID/logs/syslog"',
)
DNS_RECORD_TYPES = ("A", "CNAME", "TXT", "MX")
DNS_RECORD_TTLS = (300, 600, 1800, 3600)
CUSTOM_ROLE_PERMISSION_SETS = (
    ["storage.objects.get"],
    ["storage.objects.list", "storage.objects.get"],
    ["compute.instances.get"],
    ["pubsub.topics.list", "pubsub.topics.get"],
)
SERVICE_ACCOUNT_IAM_ROLES = ("roles/iam.serviceAccountViewer",)

_STARTUP_SCRIPT_TEMPLATES = (
    "#!/bin/bash\nset -euo pipefail\necho '{token}' > /var/tmp/acore-token\n",
    "#!/bin/bash\nprintf '{token}' > /var/tmp/acore-token\n",
    "#!/bin/bash\n/usr/bin/env echo '{token}' > /var/tmp/acore-token\n",
)


@dataclass(frozen=True)
class FirewallProfile:
    """Declarative description of the firewall posture we want."""

    label: str
    direction: str
    priority: int
    allow_protocol: str
    allow_ports: tuple[str, ...] = ()
    disabled: bool = False
    description_template: str = "Allow listed traffic for {token}"

    def describe(self, token: str) -> str:
        return self.description_template.format(token=token)


FIREWALL_PROFILES = (
    FirewallProfile(
        label="ssh",
        direction="INGRESS",
        priority=1000,
        allow_protocol="tcp",
        allow_ports=("22",),
        description_template="Allow SSH only for {token}",
    ),
    FirewallProfile(
        label="http",
        direction="INGRESS",
        priority=1001,
        allow_protocol="tcp",
        allow_ports=("80",),
        description_template="Allow HTTP only for {token}",
    ),
    FirewallProfile(
        label="icmp",
        direction="INGRESS",
        priority=1002,
        allow_protocol="icmp",
        description_template="Allow ICMP echo for {token}",
    ),
)


def new_suffix(length: int = 6) -> str:
    """Short random suffix for resource names."""
    return secrets.token_hex(max(1, length // 2))


def pick_region_and_zone(rng: Optional[random.Random] = None) -> Tuple[str, str]:
    """Choose a cheap region and one of its zones."""
    rng = rng or _SYSTEM_RANDOM
    region = rng.choice(list(REGION_TO_ZONES.keys()))
    zone = rng.choice(REGION_TO_ZONES[region])
    return region, zone


def pick_machine_type(rng: Optional[random.Random] = None) -> str:
    """Return a low-cost machine profile."""
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(CHEAP_MACHINE_TYPES)


def bucket_name(suffix: str) -> str:
    """Return a globally-unique bucket name derived from the suffix."""
    base = f"acore-{suffix}".lower()
    return base[:63]


def bucket_location(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(BUCKET_LOCATIONS)


def bucket_storage_class(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(BUCKET_STORAGE_CLASSES)


def bucket_iam_role(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(BUCKET_IAM_ROLES)


def project_iam_role(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(PROJECT_IAM_ROLES)


def artifact_location(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(ARTIFACT_LOCATIONS)


def artifact_format(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(ARTIFACT_FORMATS)


def artifact_repository_id(suffix: str) -> str:
    return f"repo-{suffix}".lower()


def pubsub_topic_id(suffix: str) -> str:
    return f"topic-{suffix}".lower()


def pubsub_subscription_id(suffix: str) -> str:
    return f"sub-{suffix}".lower()


def pubsub_retention_window(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(PUBSUB_RETENTION_WINDOWS)


def pubsub_ack_deadline(rng: Optional[random.Random] = None) -> int:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(PUBSUB_ACK_DEADLINES)


def pubsub_expiration_ttl(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(PUBSUB_EXPIRATION_TTLS)


def scheduler_job_name(suffix: str) -> str:
    return f"job-{suffix}".lower()


def scheduler_job_schedule(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(SCHEDULER_JOB_SCHEDULES)


def secret_id(suffix: str) -> str:
    return f"secret-{suffix}".lower()


def secret_payload(nonce: str) -> str:
    """Generate a simple secret payload from nonce."""
    return f"acore-secret-{nonce[:16]}"


def secret_iam_role(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(SECRET_IAM_ROLES)


def logging_sink_name(suffix: str) -> str:
    return f"sink-{suffix}".lower()


def logging_filter(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(LOGGING_FILTERS)


def dns_zone_name(suffix: str) -> str:
    return f"zone-{suffix}".lower()


def dns_record_type(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(DNS_RECORD_TYPES)


def dns_record_ttl(rng: Optional[random.Random] = None) -> int:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(DNS_RECORD_TTLS)


def dns_record_data(record_type: str, rng: Optional[random.Random] = None) -> list[str]:
    """Generate appropriate data for DNS record type."""
    rng = rng or _SYSTEM_RANDOM
    if record_type == "A":
        return [f"192.0.2.{rng.randint(1, 254)}"]
    elif record_type == "CNAME":
        return ["example.com."]
    elif record_type == "TXT":
        return [f'"v=alphacore-{rng.randint(1000, 9999)}"']
    elif record_type == "MX":
        return [f"10 mail.example.com."]
    return ["192.0.2.1"]


def custom_role_id(suffix: str) -> str:
    return f"acore_role_{suffix}".lower()


def custom_role_permissions(rng: Optional[random.Random] = None) -> list[str]:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(CUSTOM_ROLE_PERMISSION_SETS)


def service_account_iam_role(rng: Optional[random.Random] = None) -> str:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(SERVICE_ACCOUNT_IAM_ROLES)


def random_cidr_block(rng: Optional[random.Random] = None) -> str:
    """Generate a /24 block under the private 10.0.0.0/8 range."""
    rng = rng or _SYSTEM_RANDOM
    second = rng.randint(10, 200)
    third = rng.randint(0, 240)
    return f"10.{second}.{third}.0/24"


def random_firewall_profile(rng: Optional[random.Random] = None) -> FirewallProfile:
    rng = rng or _SYSTEM_RANDOM
    return rng.choice(FIREWALL_PROFILES)


def startup_script(token: str, rng: Optional[random.Random] = None) -> str:
    """Render a deterministic script snippet miners must keep verbatim."""
    rng = rng or _SYSTEM_RANDOM
    template = rng.choice(_STARTUP_SCRIPT_TEMPLATES)
    return template.format(token=token)

__all__ = [
    "CHEAP_MACHINE_TYPES",
    "FIREWALL_PROFILES",
    "FirewallProfile",
    "artifact_format",
    "artifact_location",
    "artifact_repository_id",
    "bucket_iam_role",
    "bucket_location",
    "bucket_name",
    "bucket_storage_class",

    "custom_role_id",
    "custom_role_permissions",
    "dns_record_data",
    "dns_record_ttl",
    "dns_record_type",
    "dns_zone_name",
    "logging_filter",
    "logging_sink_name",
    "new_suffix",
    "pick_machine_type",
    "pick_region_and_zone",
    "project_iam_role",
    "pubsub_ack_deadline",
    "pubsub_expiration_ttl",

    "pubsub_retention_window",
    "pubsub_subscription_id",
    "pubsub_topic_id",
    "random_cidr_block",
    "random_firewall_profile",
    "scheduler_job_name",
    "scheduler_job_schedule",
    "secret_iam_role",
    "secret_id",
    "secret_payload",
    "service_account_iam_role",
    "startup_script",
]
