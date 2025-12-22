"""
Secure sandbox execution environment for task evaluation.

This module provides sandboxed execution of Terraform and other tasks
using Firecracker VM technology with jailer isolation.

### Components

**Python Interface:**
- `sandbox.py`: Python wrapper for Firecracker sandbox execution
- `validate.py`: Task validation script run inside sandbox

**Setup & Initialization:**
- `setup.sh`: Initialize Firecracker, kernel, rootfs, and jailer
- `init-sandbox.sh`: Initialize sandbox environment (workspace from dir or zip)

**Execution & Testing:**
- `jailer.sh`: Firecracker + jailer orchestration (smoke test)
- `terraform_runner.py`: Terraform execution within sandbox
- `check-kvm.sh`: Verify KVM capability

**Test Data:**
- `test/`: Example Terraform configuration and task specifications

### Security Model

Tasks execute in isolated Firecracker VMs managed by jailer:
1. Validator creates task specification
2. VM boots with task workspace
3. Guest executes task (Terraform, scripts, etc.)
4. Results returned to host via socket API
5. VM terminates, resources freed

### Usage

**Setup environment (one-time):**
```bash
sudo ./modules/evaluation/validation/sandbox/setup.sh
```

**Run task in sandbox:**
```bash
python modules/evaluation/validation/sandbox/sandbox.py \\
  --workspace-dir /path/to/workspace \\
  --stream-log
```

**Run smoke test:**
```bash
sudo ./modules/evaluation/validation/sandbox/jailer.sh
```
"""

from pathlib import Path

# Sandbox tools locations
SANDBOX_DIR = Path(__file__).parent
SANDBOX_SCRIPT = SANDBOX_DIR / "sandbox.py"
JAILER_SCRIPT = SANDBOX_DIR / "jailer.sh"
TERRAFORM_RUNNER = SANDBOX_DIR / "terraform_runner.py"
INIT_SANDBOX = SANDBOX_DIR / "init-sandbox.sh"
SETUP_SCRIPT = SANDBOX_DIR / "setup.sh"
CHECK_KVM = SANDBOX_DIR / "check-kvm.sh"
VALIDATE_SCRIPT = SANDBOX_DIR / "validate.py"

__all__ = [
    "SANDBOX_SCRIPT",
    "JAILER_SCRIPT",
    "TERRAFORM_RUNNER",
    "INIT_SANDBOX",
    "SETUP_SCRIPT",
    "CHECK_KVM",
    "VALIDATE_SCRIPT",
]
