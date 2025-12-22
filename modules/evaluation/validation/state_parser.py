"""Terraform state file parser."""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class TerraformStateParser:
    """Parse and extract resources from Terraform state files."""

    def __init__(self, state_file_path: str):
        """
        Initialize parser with a state file.

        Args:
            state_file_path: Path to terraform.tfstate file
        """
        self.state_file_path = Path(state_file_path)
        self._state_data: Optional[Dict[str, Any]] = None
        self._resources: Optional[List[Dict[str, Any]]] = None

    def parse(self) -> Dict[str, Any]:
        """
        Parse the Terraform state file.

        Returns:
            The full state data as a dictionary

        Raises:
            FileNotFoundError: If state file doesn't exist
            ValueError: If state file is invalid JSON
        """
        if self._state_data is not None:
            return self._state_data

        if not self.state_file_path.exists():
            raise FileNotFoundError(f"State file not found: {self.state_file_path}")

        try:
            with open(self.state_file_path, "r", encoding="utf-8") as f:
                self._state_data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in state file: {e}")

        return self._state_data

    def get_resources(self) -> List[Dict[str, Any]]:
        """
        Extract all resources from the state file.

        Returns:
            List of resource dictionaries with flattened structure

        State file structure:
        {
          "resources": [
            {
              "type": "google_compute_instance",
              "instances": [
                {
                  "attributes": {
                    "name": "vm-abc123",
                    "zone": "us-east1-c",
                    ...
                  }
                }
              ]
            }
          ]
        }
        """
        if self._resources is not None:
            return self._resources

        state = self.parse()
        self._resources = []

        # Terraform state v4 structure
        for resource in state.get("resources", []):
            resource_type = resource.get("type")
            resource_mode = resource.get("mode", "managed")

            # Only validate managed resources (not data sources)
            if resource_mode != "managed":
                continue

            # Each resource can have multiple instances
            for instance in resource.get("instances", []):
                attributes = instance.get("attributes", {})

                self._resources.append(
                    {
                        "type": resource_type,
                        "name": resource.get("name"),
                        "provider": resource.get("provider"),
                        "attributes": attributes,
                        "dependencies": instance.get("dependencies", []),
                    }
                )

        return self._resources

    def find_resource_by_type(self, resource_type: str) -> List[Dict[str, Any]]:
        """
        Find all resources of a specific type.

        Args:
            resource_type: The Terraform resource type (e.g., "google_compute_instance")

        Returns:
            List of matching resources
        """
        resources = self.get_resources()
        return [r for r in resources if r["type"] == resource_type]

    def get_resource_attribute(
        self, resource: Dict[str, Any], path: str
    ) -> Optional[Any]:
        """
        Extract a nested attribute from a resource using dot notation.

        Args:
            resource: Resource dictionary from get_resources()
            path: Attribute path like "values.name" or "values.network_interface.0.network"

        Returns:
            The attribute value, or None if not found

        Examples:
            >>> parser.get_resource_attribute(resource, "values.name")
            "vm-abc123"
            >>> parser.get_resource_attribute(resource, "values.zone")
            "us-east1-c"
        """
        # Handle "values." prefix (invariants use this notation)
        if path.startswith("values."):
            path = path[7:]  # Remove "values." prefix

        # Navigate nested structure
        current = resource.get("attributes", {})
        parts = path.split(".")

        for part in parts:
            if current is None:
                return None

            # Handle array indices: network_interface.0.network
            if part.isdigit():
                if isinstance(current, list):
                    try:
                        current = current[int(part)]
                    except (IndexError, ValueError):
                        return None
                else:
                    return None
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return None

        return current
