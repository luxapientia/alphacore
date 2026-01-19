"""
Terraform generator for AlphaCore miner.

Converts parsed prompt requirements into valid Terraform HCL code.

Phase 2: Terraform Code Generation - Robust implementation with validation,
dependency resolution, and error handling.
"""

from __future__ import annotations

import ipaddress
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import bittensor as bt
except ModuleNotFoundError:  # pragma: no cover
    bt = None  # type: ignore[assignment]


class TerraformGenerationError(Exception):
    """Raised when Terraform generation fails."""

    pass


@dataclass
class TerraformWorkspace:
    """Represents a Terraform workspace with file paths."""

    path: Path
    main_tf_path: Path
    versions_tf_path: Path


class TerraformGenerator:
    """
    Converts parsed JSON from Phase 1 into valid Terraform HCL.

    Features:
    - Input validation
    - Dependency resolution
    - Resource generation (7 GCP resource types)
    - HCL file generation (versions.tf, main.tf)
    """

    # Valid GCP regions (common ones - can be expanded)
    VALID_REGIONS = {
        "us-east1",
        "us-east4",
        "us-central1",
        "us-west1",
        "us-west2",
        "us-west3",
        "us-west4",
        "europe-west1",
        "europe-west2",
        "europe-west3",
        "europe-west4",
        "europe-west6",
        "asia-east1",
        "asia-southeast1",
        "asia-south1",
        "asia-northeast1",
        "asia-northeast2",
        "australia-southeast1",
        "southamerica-east1",
    }

    # Valid machine types (common ones)
    VALID_MACHINE_TYPES = {
        "e2-micro",
        "e2-small",
        "e2-medium",
        "e2-standard-2",
        "e2-standard-4",
        "n1-standard-1",
        "n1-standard-2",
        "f1-micro",
    }

    def __init__(self) -> None:
        """Initialize the Terraform generator."""
        self._resource_generators = {
            "google_compute_network": self._generate_network,
            "google_compute_subnetwork": self._generate_subnetwork,
            "google_compute_firewall": self._generate_firewall,
            "google_compute_instance": self._generate_instance,
            "google_artifact_registry_repository": self._generate_artifact_registry,
            "google_pubsub_topic": self._generate_pubsub_topic,
            "google_project_iam_member": self._generate_iam_member,
        }

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def generate_workspace(self, parsed_data: Dict[str, Any]) -> TerraformWorkspace:
        """
        Generate complete Terraform workspace from parsed prompt data.

        Args:
            parsed_data: Parsed requirements from Phase 1 (prompt parser)

        Returns:
            TerraformWorkspace with paths to generated files

        Raises:
            TerraformGenerationError: If generation fails
        """
        if not parsed_data or not isinstance(parsed_data, dict):
            raise TerraformGenerationError("Invalid parsed_data: must be a non-empty dict")

        # Validate and normalize parsed data
        resources = parsed_data.get("resources", [])
        iam_grants = parsed_data.get("iam_grants", [])

        if not resources and not iam_grants:
            raise TerraformGenerationError("No resources or IAM grants found in parsed data")

        # Create temporary workspace directory
        workspace_dir = Path(tempfile.mkdtemp(prefix="alphacore_tf_"))
        workspace_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Validate all resources
            validated_resources = []
            for resource in resources:
                validated = self._validate_resource(resource)
                validated_resources.append(validated)

            # Auto-create VPC/subnetwork for compute instances that need them
            validated_resources = self._ensure_network_for_instances(validated_resources)

            # Resolve dependencies and determine generation order
            ordered_resources = self._resolve_dependencies(validated_resources)

            # Generate versions.tf
            versions_tf_content = self._generate_versions_tf()
            versions_tf_path = workspace_dir / "versions.tf"
            versions_tf_path.write_text(versions_tf_content, encoding="utf-8")

            # Generate main.tf with all resources
            main_tf_parts = []
            for resource in ordered_resources:
                try:
                    resource_hcl = self._generate_resource_hcl(resource)
                    if resource_hcl:
                        main_tf_parts.append(resource_hcl)
                except Exception as exc:
                    if bt:
                        bt.logging.warning(f"Failed to generate resource {resource.get('type')}: {exc}")
                    # Continue with other resources

            # Normalize IAM grants (convert service_account to member for consistency)
            normalized_iam_grants = []
            for iam_grant in iam_grants:
                normalized = iam_grant.copy()
                # Convert service_account to member if present
                if "service_account" in normalized and "member" not in normalized:
                    normalized["member"] = normalized.pop("service_account")
                normalized_iam_grants.append(normalized)

            # Generate IAM members (usually last)
            for iam_grant in normalized_iam_grants:
                try:
                    iam_hcl = self._generate_iam_member_hcl(iam_grant)
                    if iam_hcl:
                        main_tf_parts.append(iam_hcl)
                except Exception as exc:
                    if bt:
                        bt.logging.warning(f"Failed to generate IAM grant: {exc}")

            # Add data source for project ID (needed for IAM)
            if iam_grants:
                main_tf_parts.insert(0, 'data "google_project" "current" {}\n')

            main_tf_content = "\n".join(main_tf_parts)
            main_tf_path = workspace_dir / "main.tf"
            main_tf_path.write_text(main_tf_content, encoding="utf-8")

            return TerraformWorkspace(
                path=workspace_dir,
                main_tf_path=main_tf_path,
                versions_tf_path=versions_tf_path,
            )

        except Exception as exc:
            # Cleanup on error
            import shutil

            if workspace_dir.exists():
                shutil.rmtree(workspace_dir, ignore_errors=True)
            raise TerraformGenerationError(f"Failed to generate Terraform workspace: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Validation Layer
    # ------------------------------------------------------------------ #

    def _validate_resource(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and normalize a single resource."""
        if not isinstance(resource, dict):
            raise TerraformGenerationError(f"Invalid resource: must be a dict, got {type(resource)}")

        resource_type = resource.get("type")
        if not resource_type or resource_type not in self._resource_generators:
            raise TerraformGenerationError(f"Unsupported resource type: {resource_type}")

        validated = resource.copy()

        # Type-specific validation
        if resource_type == "google_compute_network":
            validated["name"] = self._validate_name(validated.get("name"), resource_type)
            validated["auto_create_subnetworks"] = self._validate_bool(
                validated.get("auto_create_subnetworks"), default=False
            )

        elif resource_type == "google_compute_subnetwork":
            validated["name"] = self._validate_name(validated.get("name"), resource_type)
            validated["network"] = validated.get("network")  # Will be validated during generation
            validated["region"] = self._validate_region(validated.get("region"))
            validated["ip_cidr_range"] = self._validate_cidr(validated.get("ip_cidr_range"))

        elif resource_type == "google_compute_firewall":
            validated["name"] = self._validate_name(validated.get("name"), resource_type)
            validated["network"] = validated.get("network")  # Will be validated during generation
            validated["direction"] = validated.get("direction", "INGRESS")
            validated["priority"] = self._validate_priority(validated.get("priority"))
            validated["disabled"] = self._validate_bool(validated.get("disabled"), default=False)

        elif resource_type == "google_compute_instance":
            validated["name"] = self._validate_name(validated.get("name"), resource_type)
            validated["zone"] = self._validate_zone(validated.get("zone"))
            validated["machine_type"] = self._validate_machine_type(validated.get("machine_type"))
            validated["subnetwork"] = validated.get("subnetwork")  # Will be validated during generation

        elif resource_type == "google_artifact_registry_repository":
            validated["repository_id"] = self._validate_name(validated.get("repository_id"), resource_type)
            validated["location"] = self._validate_region(validated.get("location"))
            validated["format"] = validated.get("format", "DOCKER")

        elif resource_type == "google_pubsub_topic":
            validated["name"] = self._validate_name(validated.get("name"), resource_type)
            if "message_retention_duration" in validated:
                validated["message_retention_duration"] = self._validate_duration(
                    validated.get("message_retention_duration")
                )

        elif resource_type == "google_project_iam_member":
            validated["member"] = self._normalize_iam_member(validated.get("member"))
            validated["role"] = validated.get("role", "roles/viewer")

        return validated

    def _validate_name(self, name: Any, resource_type: str = "") -> str:
        """Validate GCP resource name format."""
        if not name or not isinstance(name, str):
            raise TerraformGenerationError(f"Invalid name for {resource_type}: {name}")
        # GCP names: lowercase letters, numbers, hyphens, 1-63 chars
        if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", name) or len(name) > 63:
            raise TerraformGenerationError(f"Invalid GCP name format: {name} (must be 1-63 chars, lowercase alphanumeric + hyphens)")
        return name

    def _validate_cidr(self, cidr: Any) -> str:
        """Validate IP CIDR format."""
        if not cidr or not isinstance(cidr, str):
            raise TerraformGenerationError(f"Invalid CIDR: {cidr}")
        try:
            ipaddress.IPv4Network(cidr, strict=True)
        except ValueError as exc:
            raise TerraformGenerationError(f"Invalid CIDR format: {cidr}") from exc
        return cidr

    def _validate_region(self, region: Any) -> str:
        """Validate GCP region format."""
        if not region or not isinstance(region, str):
            raise TerraformGenerationError(f"Invalid region: {region}")
        # Accept any format that looks like a region (us-east1, europe-west1, etc.)
        if not re.match(r"^[a-z]+-[a-z]+[0-9]+$", region):
            if bt:
                bt.logging.warning(f"Region '{region}' doesn't match standard format, but accepting it")
        return region

    def _validate_zone(self, zone: Any) -> str:
        """Validate GCP zone format."""
        if not zone or not isinstance(zone, str):
            raise TerraformGenerationError(f"Invalid zone: {zone}")
        # Accept any format that looks like a zone (us-east1-c, europe-west1-a, etc.)
        if not re.match(r"^[a-z]+-[a-z]+[0-9]+-[a-z]$", zone):
            if bt:
                bt.logging.warning(f"Zone '{zone}' doesn't match standard format, but accepting it")
        return zone

    def _validate_machine_type(self, machine_type: Any) -> str:
        """Validate GCP machine type."""
        if not machine_type or not isinstance(machine_type, str):
            raise TerraformGenerationError(f"Invalid machine_type: {machine_type}")
        # Accept any machine type string (validation will happen at apply time)
        return machine_type

    def _validate_priority(self, priority: Any) -> int:
        """Validate firewall priority (0-65534)."""
        try:
            prio = int(priority) if priority is not None else 1000
        except (ValueError, TypeError):
            prio = 1000
        if not 0 <= prio <= 65534:
            raise TerraformGenerationError(f"Invalid priority: {prio} (must be 0-65534)")
        return prio

    def _validate_bool(self, value: Any, default: bool = False) -> bool:
        """Validate and normalize boolean value."""
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)

    def _validate_duration(self, duration: Any) -> str:
        """Validate duration string (e.g., '900s', '600s')."""
        if not duration or not isinstance(duration, str):
            raise TerraformGenerationError(f"Invalid duration: {duration}")
        # Should match pattern like "900s", "600s"
        if not re.match(r"^\d+s$", duration):
            # Try to fix: if it's just a number, append 's'
            try:
                num = int(duration)
                return f"{num}s"
            except ValueError:
                raise TerraformGenerationError(f"Invalid duration format: {duration} (expected format: '900s')") from None
        return duration

    def _normalize_iam_member(self, member: Any) -> str:
        """Ensure IAM member has correct prefix (serviceAccount:)."""
        if not member or not isinstance(member, str):
            raise TerraformGenerationError(f"Invalid IAM member: {member}")
        if member.startswith("serviceAccount:"):
            return member
        return f"serviceAccount:{member}"

    # ------------------------------------------------------------------ #
    # Auto-Resource Creation
    # ------------------------------------------------------------------ #

    def _ensure_network_for_instances(self, resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Automatically create VPC and subnetwork for compute instances that need them.

        If a compute instance doesn't have a subnetwork, create:
        1. A default VPC (if none exists)
        2. A default subnetwork in the instance's region
        """
        # Check if any compute instances need a subnetwork
        instances_needing_subnet = []
        existing_networks = set()
        existing_subnets = set()

        for resource in resources:
            resource_type = resource.get("type")
            if resource_type == "google_compute_network":
                existing_networks.add(resource.get("name", ""))
            elif resource_type == "google_compute_subnetwork":
                existing_subnets.add(resource.get("name", ""))
            elif resource_type == "google_compute_instance":
                subnetwork = resource.get("subnetwork", "")
                if not subnetwork:
                    instances_needing_subnet.append(resource)

        if not instances_needing_subnet:
            return resources

        # Create default network if needed
        default_network_name = "net-default"
        if default_network_name not in existing_networks:
            resources.insert(0, {
                "type": "google_compute_network",
                "name": default_network_name,
                "auto_create_subnetworks": False
            })
            existing_networks.add(default_network_name)

        # Create subnetworks for instances that need them
        # Group by region to avoid duplicate subnetworks
        region_to_subnet = {}
        for instance in instances_needing_subnet:
            zone = instance.get("zone", "")
            # Extract region from zone (e.g., "europe-west1-c" -> "europe-west1")
            region = zone.rsplit("-", 1)[0] if zone else "us-central1"

            if region not in region_to_subnet:
                subnet_name = f"subnet-default-{region.replace('-', '')}"
                region_to_subnet[region] = {
                    "type": "google_compute_subnetwork",
                    "name": subnet_name,
                    "network": default_network_name,
                    "region": region,
                    "ip_cidr_range": self._generate_default_cidr(region)
                }
                resources.append(region_to_subnet[region])

            # Update instance to use the subnetwork
            instance["subnetwork"] = region_to_subnet[region]["name"]

        return resources

    def _generate_default_cidr(self, region: str) -> str:
        """Generate a default CIDR for a region (simple hash-based approach)."""
        # Use a simple hash of region name to get consistent CIDR
        hash_val = abs(hash(region)) % 256
        return f"10.{hash_val}.0.0/24"

    # ------------------------------------------------------------------ #
    # Dependency Resolution
    # ------------------------------------------------------------------ #

    def _resolve_dependencies(self, resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Resolve resource dependencies and return resources in correct order.

        Dependency order:
        - google_compute_network (no deps)
        - google_compute_subnetwork (depends on: network)
        - google_compute_firewall (depends on: network)
        - google_compute_instance (depends on: subnetwork)
        - google_artifact_registry_repository (no deps)
        - google_pubsub_topic (no deps)
        - google_project_iam_member (no deps, but usually last)
        """
        # Build dependency graph
        resource_map: Dict[str, Dict[str, Any]] = {}
        for resource in resources:
            name = resource.get("name") or resource.get("repository_id")
            if name:
                resource_map[name] = resource

        # Topological sort
        ordered: List[Dict[str, Any]] = []
        processed: Set[str] = set()

        def add_resource(resource: Dict[str, Any]) -> None:
            resource_type = resource.get("type")
            name = resource.get("name") or resource.get("repository_id") or ""

            # Networks first (no dependencies)
            if resource_type == "google_compute_network":
                if name not in processed:
                    ordered.append(resource)
                    processed.add(name)

            # Subnetworks (depend on networks)
            elif resource_type == "google_compute_subnetwork":
                network_name = resource.get("network", "")
                # Network should already be processed, but check anyway
                if network_name in processed or not network_name:
                    if name not in processed:
                        ordered.append(resource)
                        processed.add(name)

            # Firewalls (depend on networks)
            elif resource_type == "google_compute_firewall":
                network_name = resource.get("network", "")
                if network_name in processed or not network_name:
                    if name not in processed:
                        ordered.append(resource)
                        processed.add(name)

            # Instances (depend on subnetworks)
            elif resource_type == "google_compute_instance":
                subnetwork_name = resource.get("subnetwork", "")
                if subnetwork_name in processed or not subnetwork_name:
                    if name not in processed:
                        ordered.append(resource)
                        processed.add(name)

            # Other resources (no dependencies)
            else:
                if name not in processed:
                    ordered.append(resource)
                    processed.add(name)

        # Process in dependency order
        for resource in resources:
            add_resource(resource)

        return ordered

    # ------------------------------------------------------------------ #
    # HCL Generation
    # ------------------------------------------------------------------ #

    def _generate_versions_tf(self) -> str:
        """Generate versions.tf with provider constraints."""
        return '''terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "7.12.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "7.12.0"
    }
  }
}
'''

    def _generate_resource_hcl(self, resource: Dict[str, Any]) -> str:
        """Generate HCL for a single resource."""
        resource_type = resource.get("type")
        generator = self._resource_generators.get(resource_type)
        if not generator:
            raise TerraformGenerationError(f"No generator for resource type: {resource_type}")
        return generator(resource)

    def _generate_network(self, resource: Dict[str, Any]) -> str:
        """Generate google_compute_network resource."""
        name = resource["name"]
        auto_create = resource.get("auto_create_subnetworks", False)
        tf_id = self._terraform_id(name)

        return f'''resource "google_compute_network" "{tf_id}" {{
  name                    = "{name}"
  auto_create_subnetworks = {str(auto_create).lower()}
}}
'''

    def _generate_subnetwork(self, resource: Dict[str, Any]) -> str:
        """Generate google_compute_subnetwork resource."""
        name = resource["name"]
        region = resource["region"]
        ip_cidr_range = resource["ip_cidr_range"]
        network_name = resource.get("network", "")

        tf_id = self._terraform_id(name)

        # Resolve network reference
        network_ref = self._resolve_network_reference(network_name)

        return f'''resource "google_compute_subnetwork" "{tf_id}" {{
  name          = "{name}"
  ip_cidr_range = "{ip_cidr_range}"
  region        = "{region}"
  network       = {network_ref}
}}
'''

    def _generate_firewall(self, resource: Dict[str, Any]) -> str:
        """Generate google_compute_firewall resource."""
        name = resource["name"]
        network_name = resource.get("network", "")
        direction = resource.get("direction", "INGRESS")
        priority = resource.get("priority", 1000)
        disabled = resource.get("disabled", False)

        # Extract allowed rules
        allowed = resource.get("allowed", {})
        if isinstance(allowed, dict):
            protocol = allowed.get("protocol", "tcp")
            ports = allowed.get("ports", [])
        else:
            protocol = "tcp"
            ports = []

        tf_id = self._terraform_id(name)
        network_ref = self._resolve_network_reference(network_name)

        # Generate allow block
        ports_str = ""
        if ports:
            ports_list = ", ".join([f'"{p}"' for p in ports])
            ports_str = f"\n    ports    = [{ports_list}]"

        return f'''resource "google_compute_firewall" "{tf_id}" {{
  name      = "{name}"
  network   = {network_ref}
  direction = "{direction}"
  priority  = {priority}
  disabled  = {str(disabled).lower()}

  allow {{
    protocol = "{protocol}"{ports_str}
  }}
}}
'''

    def _generate_instance(self, resource: Dict[str, Any]) -> str:
        """Generate google_compute_instance resource."""
        name = resource["name"]
        zone = resource["zone"]
        machine_type = resource["machine_type"]
        subnetwork_name = resource.get("subnetwork", "")

        # Extract startup script
        startup_script = resource.get("metadata_startup_script", "")
        startup_script = self._normalize_startup_script(startup_script)

        tf_id = self._terraform_id(name)

        # Resolve subnetwork reference
        subnetwork_ref = self._resolve_subnetwork_reference(subnetwork_name)

        # Generate metadata block
        metadata_block = ""
        if startup_script:
            metadata_block = f'''
  metadata = {{
    startup-script = <<-EOF
{startup_script}    EOF
  }}'''

        return f'''resource "google_compute_instance" "{tf_id}" {{
  name         = "{name}"
  machine_type = "{machine_type}"
  zone         = "{zone}"

  boot_disk {{
    initialize_params {{
      image = "debian-cloud/debian-12"
    }}
  }}

  network_interface {{
    subnetwork = {subnetwork_ref}
  }}{metadata_block}
}}
'''

    def _generate_artifact_registry(self, resource: Dict[str, Any]) -> str:
        """Generate google_artifact_registry_repository resource."""
        repository_id = resource["repository_id"]
        location = resource["location"]
        format_type = resource.get("format", "DOCKER")

        tf_id = self._terraform_id(repository_id)

        return f'''resource "google_artifact_registry_repository" "{tf_id}" {{
  repository_id = "{repository_id}"
  location      = "{location}"
  format        = "{format_type}"
}}
'''

    def _generate_pubsub_topic(self, resource: Dict[str, Any]) -> str:
        """Generate google_pubsub_topic resource."""
        name = resource["name"]
        retention = resource.get("message_retention_duration")

        tf_id = self._terraform_id(name)

        retention_line = ""
        if retention:
            retention_line = f'\n  message_retention_duration = "{retention}"'

        return f'''resource "google_pubsub_topic" "{tf_id}" {{
  name = "{name}"{retention_line}
}}
'''

    def _generate_iam_member_hcl(self, iam_grant: Dict[str, Any]) -> str:
        """Generate google_project_iam_member resource from IAM grant."""
        # IAM grants should already be normalized to use "member" field
        member = iam_grant.get("member", "")
        if not member:
            raise TerraformGenerationError("IAM grant missing 'member' field")

        # Normalize member format (ensure serviceAccount: prefix)
        member = self._normalize_iam_member(member)
        role = iam_grant.get("role", "roles/viewer")

        # Use data source for project ID
        return f'''resource "google_project_iam_member" "viewer_access_{self._terraform_id(member.replace('@', '_').replace('.', '_').replace(':', '_'))}" {{
  project = data.google_project.current.project_id
  role    = "{role}"
  member  = "{member}"
}}
'''

    def _generate_iam_member(self, resource: Dict[str, Any]) -> str:
        """Generate google_project_iam_member resource (legacy method)."""
        # This shouldn't be called directly, but kept for compatibility
        return self._generate_iam_member_hcl(resource)

    # ------------------------------------------------------------------ #
    # Helper Functions
    # ------------------------------------------------------------------ #

    def _terraform_id(self, name: str) -> str:
        """Convert GCP resource name to valid Terraform identifier."""
        # Replace hyphens and dots with underscores
        return name.replace("-", "_").replace(".", "_")

    def _normalize_startup_script(self, script: str) -> str:
        """Convert escaped \\n to actual newlines for Terraform heredoc."""
        if not script:
            return ""
        # Replace \\n with actual newlines
        normalized = script.replace("\\n", "\n")
        # Ensure it ends with newline
        if not normalized.endswith("\n"):
            normalized += "\n"
        # Indent each line for heredoc (if not already indented)
        lines = normalized.split("\n")
        indented = "\n".join([f"      {line}" if line.strip() else "" for line in lines])
        return indented

    def _resolve_network_reference(self, network_name: str) -> str:
        """Resolve network name to Terraform resource reference."""
        if not network_name:
            raise TerraformGenerationError("Network name is required for subnetwork/firewall")
        tf_id = self._terraform_id(network_name)
        return f'google_compute_network.{tf_id}.id'

    def _resolve_subnetwork_reference(self, subnetwork_name: str) -> str:
        """Resolve subnetwork name to Terraform resource reference."""
        if not subnetwork_name:
            raise TerraformGenerationError("Subnetwork name is required for compute instance")
        tf_id = self._terraform_id(subnetwork_name)
        return f'google_compute_subnetwork.{tf_id}.id'
