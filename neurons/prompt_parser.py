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
                            bt.logging.warning(f"Failed to parse JSON response (attempt {attempt + 1}/{self.max_retries}): {e}")
                        time.sleep(1)
                        continue
                    raise PromptParseError(f"Invalid JSON response: {e}") from e

                # Validate and normalize parsed structure
                normalized = self._normalize_parsed(parsed)
                return normalized

            except Exception as e:
                if attempt < self.max_retries - 1:
                    if bt:
                        bt.logging.warning(f"LLM call failed (attempt {attempt + 1}/{self.max_retries}): {e}")
                    time.sleep(1)
                    continue
                raise PromptParseError(f"Failed to parse prompt after {self.max_retries} attempts: {e}") from e

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
      "name": "repo-123",
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

Supported resource types:
- google_compute_network (VPC)
- google_compute_subnetwork
- google_compute_firewall
- google_compute_instance
- google_pubsub_topic
- google_pubsub_subscription
- google_artifact_registry_repository
- google_storage_bucket
- google_secret_manager_secret
- google_cloud_scheduler_job
- google_dns_managed_zone
- google_dns_record_set
- google_logging_project_sink
- google_project_iam_member
- google_service_account
- google_service_account_iam_member

Key extraction rules:
1. Extract exact resource names as specified in prompt
2. Extract regions/zones exactly as written
3. Extract CIDR blocks exactly (e.g., "10.86.156.0/24")
4. Extract metadata_startup_script exactly, preserving newlines as \\n
5. For IAM grants, look for "Grant X viewer access" or similar patterns
6. Extract all boolean values (auto_create_subnetworks, disabled, etc.)
7. Preserve exact priority values for firewalls
8. Extract message_retention_duration as duration strings (e.g., "900s", "600s")

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

        # Validate each resource has required fields
        for idx, resource in enumerate(resources):
            if not isinstance(resource, dict):
                raise PromptParseError(f"Resource at index {idx} must be an object")
            if "type" not in resource:
                raise PromptParseError(f"Resource at index {idx} missing 'type' field")
            if "name" not in resource:
                raise PromptParseError(f"Resource at index {idx} missing 'name' field")

        # Validate each IAM grant has required fields
        for idx, grant in enumerate(iam_grants):
            if not isinstance(grant, dict):
                raise PromptParseError(f"IAM grant at index {idx} must be an object")
            if "service_account" not in grant:
                raise PromptParseError(f"IAM grant at index {idx} missing 'service_account' field")
            if "role" not in grant:
                raise PromptParseError(f"IAM grant at index {idx} missing 'role' field")

        # Add metadata if missing
        if "metadata" not in parsed:
            parsed["metadata"] = {}

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
