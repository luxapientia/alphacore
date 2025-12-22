#!/usr/bin/env python3
"""Terraform execution helper intended to run inside the sandbox guest."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

BUNDLE_ROOT = Path("/opt/acore-sandbox-bundle")
DEFAULT_PATH = f"{BUNDLE_ROOT}/bin:/usr/local/bin:/usr/bin:/bin"


def write_error(result_error: Path, msg: str) -> None:
    """Write a minimal error.json for terraform failures."""
    token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
    if token:
        msg = msg.replace(token, "[REDACTED]")

    try:
        result_error.parent.mkdir(parents=True, exist_ok=True)
        payload = {"status": "error", "msg": msg, "score": 0}
        result_error.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - best effort logging only
        sys.stderr.write(f"[Runner] Failed to write error.json: {exc}\n")
    try:
        subprocess.run(["sync"], check=False)
    except Exception:
        pass


def run_tf(cmd: list[str], *, env: dict[str, str], workdir: Path, label: str, result_error: Path) -> None:
    """Run a terraform subcommand with live streaming and error propagation."""
    print(f"[Runner] {label}...")

    stdout_buf: deque[str] = deque(maxlen=50)
    stderr_buf: deque[str] = deque(maxlen=50)

    proc = subprocess.Popen(
        cmd,
        cwd=str(workdir),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )

    def _pump(stream, target, buffer):
        for line in stream:
            target.write(line)
            target.flush()
            buffer.append(line.rstrip("\n"))

    threads = [
        threading.Thread(target=_pump, args=(proc.stdout, sys.stdout, stdout_buf), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, sys.stderr, stderr_buf), daemon=True),
    ]
    for t in threads:
        t.start()

    proc.wait()
    for t in threads:
        t.join(timeout=1)

    if proc.returncode != 0:
        snippet_lines = list(stderr_buf) or list(stdout_buf)
        snippet = "\n".join(snippet_lines)[-800:] if snippet_lines else ""
        message = f"{label} failed (rc={proc.returncode})"
        if snippet:
            message = f"{message}: {snippet}"
        write_error(result_error, message)
        raise subprocess.CalledProcessError(proc.returncode, cmd, None, None)


def main() -> int:
    workdir = Path(os.environ.get("WORKDIR", "/workspace")).resolve()
    home = Path(os.environ.get("HOME", str(workdir)))
    results_dir = Path(os.environ.get("RESULTS_DIR", "/run/results")).resolve()
    result_error = Path(os.environ.get("TF_ERROR_JSON") or results_dir / "error.json")

    if not os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN"):
        sys.stderr.write("[Runner] ERROR: GOOGLE_OAUTH_ACCESS_TOKEN is not set; aborting.\n")
        write_error(result_error, "GOOGLE_OAUTH_ACCESS_TOKEN is not set")
        return 1

    tf_bin = BUNDLE_ROOT / "bin" / "terraform"
    if not tf_bin.exists() or not os.access(tf_bin, os.X_OK):
        sys.stderr.write(f"[Runner] ERROR: terraform binary not found at {tf_bin}\n")
        write_error(result_error, "terraform binary not found in bundle")
        return 1

    if not results_dir.exists():
        sys.stderr.write(f"[Runner] ERROR: RESULTS_DIR missing or not a directory: {results_dir}\n")
        return 1

    env = os.environ.copy()
    if os.environ.get("TF_LOG_PROVIDER_DEBUG", "0") == "1":
        env["TF_LOG_PROVIDER"] = "DEBUG"
    env["PATH"] = DEFAULT_PATH
    env["HOME"] = str(home)
    env["TF_IN_AUTOMATION"] = "1"
    tf_rc = BUNDLE_ROOT / "config" / "terraform.rc"
    if tf_rc.exists():
        env["TF_CLI_CONFIG_FILE"] = str(tf_rc)

    if not env.get("http_proxy") and not env.get("https_proxy"):
        proxy = "http://172.16.0.1:8888"
        env["http_proxy"] = proxy
        env["https_proxy"] = proxy

    try:
        run_tf([str(tf_bin), "init", "-input=false", "-backend=false", "-no-color"], env=env, workdir=workdir, label="terraform init", result_error=result_error)
        run_tf([str(tf_bin), "apply", "-refresh-only", "-auto-approve", "-no-color"], env=env, workdir=workdir, label="terraform apply", result_error=result_error)
    except subprocess.CalledProcessError:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
