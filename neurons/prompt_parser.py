"""
Prompt parser for AlphaCore miner.

Parses natural-language task prompts into structured JSON format suitable for
Terraform code generation.

Configuration via environment variables:
- OPENAI_API_KEY: API key (required; alias: ALPHACORE_OPENAI_API_KEY)
- OPENAI_BASE_URL: Base URL for local models (optional)
- ALPHACORE_PROMPT_PARSER_MODEL: Model name (default: gpt-4o-mini)
- ALPHACORE_PROMPT_PARSER_TEMPERATURE: Temperature (default: 0.3)
- ALPHACORE_PROMPT_PARSER_RETRIES: Max retry attempts (default: 3)
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

try:
    import bittensor as bt
except ModuleNotFoundError:  # pragma: no cover
    bt = None  # type: ignore[assignment]

try:  # pragma: no cover - optional dependency
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment,misc]


class PromptParseError(Exception):
    """Raised when prompt parsing fails."""

    pass


class PromptParser:
    """
    Parses natural-language AlphaCore task prompts into structured JSON.

    Uses LLM to extract resource requirements, configurations, and IAM grants.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_retries: int = 3,
    ) -> None:
        """
        Initialize the prompt parser.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            base_url: Base URL for API (optional, for local models)
            model: Model name (defaults to ALPHACORE_PROMPT_PARSER_MODEL or gpt-4o-mini)
            temperature: Temperature for LLM (default: 0.3 for deterministic parsing)
            max_retries: Maximum retry attempts (default: 3)
        """
        if OpenAI is None:
            raise ImportError(
                "openai package is required for prompt parsing. "
                "Install with: pip install openai"
            )

        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("ALPHACORE_OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OpenAI API key required. Set OPENAI_API_KEY or ALPHACORE_OPENAI_API_KEY environment variable."
            )

        self.base_url = base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("ALPHACORE_OPENAI_BASE_URL")
        self.model = model or os.getenv("ALPHACORE_PROMPT_PARSER_MODEL", "gpt-4o-mini")
        self.temperature = float(
            os.getenv("ALPHACORE_PROMPT_PARSER_TEMPERATURE", str(temperature))
        )
        self.max_retries = int(os.getenv("ALPHACORE_PROMPT_PARSER_RETRIES", str(max_retries)))

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url) if self.base_url else OpenAI(api_key=self.api_key)

    def parse(self, prompt: str) -> Dict[str, Any]:
        """
        Parse a natural-language prompt into structured JSON.

        Args:
            prompt: Natural-language task description

        Returns:
            Structured dictionary with:
            - resources: List of resource definitions
            - iam_grants: List of IAM grant requirements
            - metadata: Additional parsed metadata

        Raises:
            PromptParseError: If parsing fails after retries
        """
        if not prompt or not prompt.strip():
            raise PromptParseError("Empty prompt provided")

        system_prompt = self._get_system_prompt()
        user_prompt = f"Parse the following AlphaCore task prompt into structured JSON:\n\n{prompt}"

        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=self.temperature,
                    max_completion_tokens=4000,
                    response_format={"type": "json_object"},  # Force JSON response
                )

                content = response.choices[0].message.content
                if not content:
                    raise PromptParseError("Empty response from LLM")

                # Parse JSON response
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as e:
                    if attempt < self.max_retries - 1:
                        if bt:
                            bt.logging.warning(
                                f"Failed to parse JSON response (attempt {attempt + 1}/{self.max_retries}): {e}. "
                                f"Response preview: {content[:200]}..."
                            )
                        time.sleep(1)
                        continue
                    # Include response snippet in error for debugging
                    error_msg = f"Invalid JSON response: {e}"
                    if content:
                        error_msg += f"\nResponse preview: {content[:500]}"
                    raise PromptParseError(error_msg) from e

                # Validate and normalize parsed structure
                try:
                    normalized = self._normalize_parsed(parsed)
                    if bt:
                        bt.logging.info(
                            f"Successfully parsed prompt: {len(normalized.get('resources', []))} resources, "
                            f"{len(normalized.get('iam_grants', []))} IAM grants"
                        )
                        bt.logging.debug(
                            f"Parsed prompt content: {json.dumps(normalized, indent=2)}"
                        )
                    return normalized
                except PromptParseError as e:
                    # Re-raise validation errors immediately (don't retry)
                    raise
                except Exception as e:
                    # Wrap unexpected normalization errors
                    raise PromptParseError(f"Failed to normalize parsed structure: {e}") from e

            except PromptParseError:
                # Don't retry validation errors - they indicate structural issues
                raise
            except Exception as e:
                if attempt < self.max_retries - 1:
                    if bt:
                        bt.logging.warning(
                            f"LLM call failed (attempt {attempt + 1}/{self.max_retries}): {e}. "
                            f"Retrying in 1 second..."
                        )
                    time.sleep(1)
                    continue
                # Last attempt failed - include more context
                error_msg = f"Failed to parse prompt after {self.max_retries} attempts: {e}"
                if bt:
                    bt.logging.error(error_msg)
                raise PromptParseError(error_msg) from e

        raise PromptParseError(f"Failed to parse prompt after {self.max_retries} attempts")

    def _get_system_prompt(self) -> str:
        """Generate system prompt for LLM."""
        return """You are a Terraform infrastructure expert. Parse AlphaCore task prompts into structured JSON.

Output a JSON object with this exact structure:
{
  "resources": [
    {
      "type": "google_compute_network",
      "name": "net-123",
      "auto_create_subnetworks": false
    },
    {
      "type": "google_compute_subnetwork",
      "name": "subnet-123",
      "network": "net-123",
      "region": "us-east1",
      "ip_cidr_range": "10.0.0.0/24"
    },
    {
      "type": "google_compute_firewall",
      "name": "fw-ssh-123",
      "network": "net-123",
      "direction": "INGRESS",
      "allowed": [{"protocol": "tcp", "ports": ["22"]}],
      "priority": 1000,
      "disabled": false
    },
    {
      "type": "google_compute_instance",
      "name": "vm-123",
      "zone": "us-east1-c",
      "machine_type": "e2-micro",
      "subnetwork": "subnet-123",
      "metadata_startup_script": "#!/bin/bash\\necho 'token' > /var/tmp/acore-token\\n"
    },
    {
      "type": "google_pubsub_topic",
      "name": "topic-123",
      "message_retention_duration": "900s"
    },
    {
      "type": "google_artifact_registry_repository",
      "repository_id": "repo-123",  // CRITICAL: Use ONLY "repository_id", NEVER use "name" for this resource type
      "location": "asia-southeast1",
      "format": "DOCKER"
    }
  ],
  "iam_grants": [
    {
      "service_account": "validator@project.iam.gserviceaccount.com",
      "role": "roles/viewer",
      "resource_type": "project"  // optional: "project", "compute_instance", etc.
    }
  ]
}

Supported resource types (ONLY these are supported):
- google_compute_network (VPC) - requires: name, auto_create_subnetworks
- google_compute_subnetwork - requires: name, network, region, ip_cidr_range
- google_compute_firewall - requires: name, network, direction, allowed, priority
- google_compute_instance - requires: name, zone, machine_type, subnetwork
- google_pubsub_topic - requires: name
- google_artifact_registry_repository - requires: repository_id (NOT "name"), location, format
- google_project_iam_member - requires: member, role

CRITICAL: For google_artifact_registry_repository, you MUST use "repository_id" field and MUST NOT include "name" field.

Key extraction rules:
1. Extract exact resource names as specified in prompt (must be 1-63 chars, lowercase alphanumeric + hyphens)
2. For google_artifact_registry_repository: use ONLY "repository_id" field, NEVER include "name" field
3. Extract regions/zones exactly as written (common regions: us-east1, us-central1, europe-west1, asia-southeast1, etc.)
4. Extract CIDR blocks exactly (e.g., "10.86.156.0/24") - must be valid IPv4 CIDR
5. Extract metadata_startup_script exactly, preserving newlines as \\n
6. For IAM grants, look for "Grant X viewer access" or similar patterns - roles must start with "roles/"
7. Extract all boolean values (auto_create_subnetworks, disabled, etc.)
8. Preserve exact priority values for firewalls (typically 1000)
9. Extract message_retention_duration as duration strings (e.g., "900s", "600s")
10. Validate machine_type is a valid GCP type (e.g., e2-micro, e2-small, n1-standard-1)
11. Zone format must be "region-zone" (e.g., "us-east1-c")

Return ONLY valid JSON, no explanations or markdown.
"""

    def _normalize_parsed(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and normalize parsed JSON structure.

        Args:
            parsed: Raw parsed JSON from LLM

        Returns:
            Normalized and validated structure
        """
        # Ensure top-level structure
        if not isinstance(parsed, dict):
            raise PromptParseError("Parsed result must be a JSON object")

        # Ensure resources list exists
        resources = parsed.get("resources", [])
        if not isinstance(resources, list):
            resources = []
        parsed["resources"] = resources

        # Ensure iam_grants list exists
        iam_grants = parsed.get("iam_grants", [])
        if not isinstance(iam_grants, list):
            iam_grants = []
        parsed["iam_grants"] = iam_grants

        # Supported resource types (must match terraform_generator.py)
        SUPPORTED_RESOURCE_TYPES = {
            "google_compute_network",
            "google_compute_subnetwork",
            "google_compute_firewall",
            "google_compute_instance",
            "google_artifact_registry_repository",
            "google_pubsub_topic",
            "google_project_iam_member",
        }

        # Valid GCP regions (must match terraform_generator.py)
        VALID_REGIONS = {
            "us-east1", "us-east4", "us-central1", "us-west1", "us-west2", "us-west3", "us-west4",
            "europe-west1", "europe-west2", "europe-west3", "europe-west4", "europe-west6",
            "asia-east1", "asia-southeast1", "asia-south1", "asia-northeast1", "asia-northeast2",
            "australia-southeast1", "southamerica-east1",
        }

        # Validate each resource
        for idx, resource in enumerate(resources):
            if not isinstance(resource, dict):
                raise PromptParseError(f"Resource at index {idx} must be an object")

            # Validate resource type
            resource_type = resource.get("type")
            if not resource_type:
                raise PromptParseError(f"Resource at index {idx} missing 'type' field")

            if resource_type not in SUPPORTED_RESOURCE_TYPES:
                raise PromptParseError(
                    f"Resource at index {idx} has unsupported type '{resource_type}'. "
                    f"Supported types: {', '.join(sorted(SUPPORTED_RESOURCE_TYPES))}"
                )

            # Resource-specific validation
            if resource_type == "google_artifact_registry_repository":
                # Artifact registry uses ONLY "repository_id" - NOT "name"
                if "repository_id" not in resource:
                    raise PromptParseError(
                        f"Resource at index {idx} (type: {resource_type}) missing required 'repository_id' field. "
                        "Note: Use 'repository_id', NOT 'name' for artifact registry repositories."
                    )
                # Reject "name" field if present - enforce consistency
                if "name" in resource:
                    raise PromptParseError(
                        f"Resource at index {idx} (type: {resource_type}) should use 'repository_id' field, "
                        "not 'name'. Remove 'name' and use 'repository_id' instead."
                    )
                # Validate repository_id format (same as GCP name format)
                repository_id = resource.get("repository_id")
                if repository_id and isinstance(repository_id, str):
                    import re
                    if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", repository_id) or len(repository_id) > 63:
                        raise PromptParseError(
                            f"Resource at index {idx} (type: {resource_type}) has invalid repository_id '{repository_id}'. "
                            "GCP names must be 1-63 characters, lowercase alphanumeric with hyphens only."
                        )
            else:
                # All other resources require "name"
                if "name" not in resource:
                    raise PromptParseError(
                        f"Resource at index {idx} (type: {resource_type}) missing required 'name' field"
                    )
                # Validate GCP name format (1-63 chars, lowercase alphanumeric + hyphens)
                name = resource.get("name")
                if name and isinstance(name, str):
                    import re
                    if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", name) or len(name) > 63:
                        raise PromptParseError(
                            f"Resource at index {idx} (type: {resource_type}) has invalid name '{name}'. "
                            "GCP names must be 1-63 characters, lowercase alphanumeric with hyphens only."
                        )

            # Validate region if present
            region = resource.get("region") or resource.get("location")
            if region and isinstance(region, str):
                if region not in VALID_REGIONS:
                    if bt:
                        bt.logging.warning(
                            f"Resource at index {idx} (type: {resource_type}) has region '{region}' "
                            f"not in known valid regions list. This may still work if it's a valid GCP region."
                        )

            # Validate zone format if present (e.g., "us-east1-c")
            zone = resource.get("zone")
            if zone and isinstance(zone, str):
                import re
                if not re.match(r"^[a-z]+-[a-z]+[0-9]+-[a-z]$", zone):
                    if bt:
                        bt.logging.warning(
                            f"Resource at index {idx} (type: {resource_type}) has zone '{zone}' "
                            "with unexpected format. Expected format: 'region-zone' (e.g., 'us-east1-c')"
                        )

            # Validate machine_type if present
            machine_type = resource.get("machine_type")
            if machine_type and isinstance(machine_type, str):
                VALID_MACHINE_TYPES = {
                    "e2-micro", "e2-small", "e2-medium", "e2-standard-2", "e2-standard-4",
                    "n1-standard-1", "n1-standard-2", "f1-micro",
                }
                if machine_type not in VALID_MACHINE_TYPES:
                    if bt:
                        bt.logging.warning(
                            f"Resource at index {idx} (type: {resource_type}) has machine_type '{machine_type}' "
                            "not in known valid types. This may still work if it's a valid GCP machine type."
                        )

            # Validate CIDR format if present
            ip_cidr_range = resource.get("ip_cidr_range")
            if ip_cidr_range and isinstance(ip_cidr_range, str):
                import ipaddress
                try:
                    ipaddress.IPv4Network(ip_cidr_range, strict=True)
                except ValueError:
                    raise PromptParseError(
                        f"Resource at index {idx} (type: {resource_type}) has invalid CIDR '{ip_cidr_range}'. "
                        "Expected format: 'x.x.x.x/y' (e.g., '10.0.0.0/24')"
                    )

        # Validate each IAM grant has required fields
        for idx, grant in enumerate(iam_grants):
            if not isinstance(grant, dict):
                raise PromptParseError(f"IAM grant at index {idx} must be an object")
            if "service_account" not in grant:
                raise PromptParseError(f"IAM grant at index {idx} missing 'service_account' field")
            if "role" not in grant:
                raise PromptParseError(f"IAM grant at index {idx} missing 'role' field")

            # Validate role format (should start with "roles/")
            role = grant.get("role")
            if role and isinstance(role, str) and not role.startswith("roles/"):
                if bt:
                    bt.logging.warning(
                        f"IAM grant at index {idx} has role '{role}' that doesn't start with 'roles/'. "
                        "This may still work if it's a valid GCP IAM role."
                    )

        # Add metadata if missing
        if "metadata" not in parsed:
            parsed["metadata"] = {}

        # Log summary
        if bt:
            bt.logging.debug(
                f"Normalized parsed structure: {len(resources)} resources, {len(iam_grants)} IAM grants"
            )

        return parsed


def parse_prompt(prompt: str, **kwargs: Any) -> Dict[str, Any]:
    """
    Convenience function to parse a prompt.

    Args:
        prompt: Natural-language task description
        **kwargs: Optional arguments passed to PromptParser constructor

    Returns:
        Structured dictionary with resources and IAM grants

    Raises:
        PromptParseError: If parsing fails
    """
    parser = PromptParser(**kwargs)
    return parser.parse(prompt)
