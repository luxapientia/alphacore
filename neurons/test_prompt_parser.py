#!/usr/bin/env python3
"""
Simple test script for prompt parser (Phase 1).

Usage:
    python neurons/test_prompt_parser.py

Make sure OPENAI_API_KEY is set in your environment.
"""

import os
import sys
import json
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from neurons.prompt_parser import PromptParser, PromptParseError

# Example prompts from actual miner logs
TEST_PROMPTS = [
    # Prompt 1: VPC with subnet, firewall, and VM
    """On Google Cloud, Create a custom VPC with auto create subnetworks set to false and call it net-695349 to keep this setup off the default networks. Carve out a regional subnetwork with IP CIDR 10.86.156.0/24 in us-east1 named subnet-695349 and attach it to net-695349. Restrict ingress on the bespoke VPC and add a firewall that is false for disabled, set to ingress, opening port 22 with priority 1000, named fw-ssh-695349, using tcp against net-695349. Use the dedicated VPC stack before adding the VM and provision a VM in zone us-east1-c named vmnet-69534992 with machine type e2-micro attached to subnet-695349 and metadata startup script "#!/bin/bash\n/usr/bin/env echo 'cf25e4-695349-net' > /var/tmp/acore-token\n". Grant prod-sn66-service-account@prod-validator-f062.iam.gserviceaccount.com viewer access so they can verify the deployment. Submit a single zip archive of the repository; keep the Terraform config at the repository root and include terraform.tfstate at the repository root.""",

    # Prompt 2: Simple Pub/Sub topic
    """Stand up a single Google Cloud Pub/Sub topic for lightweight staging. Set message retention duration to 900s to keep messages short-lived and isolated. Give it the name topic-b495249d and do not enable optional add-ons. Keep IAM minimal and scoped to the service account that needs verification. Grant prod-sn66-service-account@prod-validator-f062.iam.gserviceaccount.com viewer access so they can verify the deployment. Bundle the repository into one zip archive for submission; keep Terraform at the repository root and include terraform.tfstate at the repository root.""",

    # Prompt 3: Simple VM
    """In a Google Cloud project, Provision exactly one Compute Engine VM. Place the VM in us-east1-d, name it vm-d2762291, provide the metadata startup script "#!/bin/bash\nprintf '120521-d27622' > /var/tmp/acore-token\n", and choose machine type e2-small. Keep the deployment minimal to prioritize quick apply and destroy. Grant alphacore@alphacore-482714.iam.gserviceaccount.com viewer access so they can verify the deployment. Bundle the repository into one zip archive for submission; keep Terraform at the repository root and include terraform.tfstate at the repository root.""",
]


def test_parser():
    """Test the prompt parser with example prompts."""
    # Check API key
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ALPHACORE_OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY or ALPHACORE_OPENAI_API_KEY not set")
        print("Set it with: export OPENAI_API_KEY=sk-...")
        sys.exit(1)

    print("=" * 80)
    print("Testing AlphaCore Prompt Parser (Phase 1)")
    print("=" * 80)
    print()

    try:
        parser = PromptParser()
        print(f"✓ Parser initialized with model: {parser.model}")
        print()
    except Exception as e:
        print(f"✗ Failed to initialize parser: {e}")
        sys.exit(1)

    for idx, prompt in enumerate(TEST_PROMPTS, 1):
        print(f"\n{'=' * 80}")
        print(f"Test {idx}/{len(TEST_PROMPTS)}")
        print(f"{'=' * 80}")
        print(f"\nPrompt (first 200 chars):\n{prompt[:200]}...\n")

        try:
            parsed = parser.parse(prompt)

            print("✓ Parsing successful!")
            print(f"\nResources found: {len(parsed.get('resources', []))}")
            print(f"IAM grants found: {len(parsed.get('iam_grants', []))}")

            print("\nParsed JSON (pretty-printed):")
            print(json.dumps(parsed, indent=2))

            # Quick validation
            resources = parsed.get("resources", [])
            for res in resources:
                if "type" not in res or "name" not in res:
                    print(f"\n⚠ Warning: Resource missing required fields: {res}")

        except PromptParseError as e:
            print(f"✗ Parse error: {e}")
        except Exception as e:
            print(f"✗ Unexpected error: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 80}")
    print("Testing complete!")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    test_parser()
