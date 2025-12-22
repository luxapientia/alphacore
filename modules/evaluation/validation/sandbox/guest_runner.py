#!/usr/bin/env python3
"""Guest-side runner for terraform + validator with guaranteed error.json on failure."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path


def write_error_json(results_dir: Path, msg: str, score: float | str | None = None) -> None:
    """Write an error.json payload to the results directory (best effort)."""
    def _sanitize(payload: str) -> str:
        token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
        return payload.replace(token, "[REDACTED]") if token else payload

    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {"msg": _sanitize(msg)}
        if score is not None:
            try:
                payload["score"] = float(score)
            except (TypeError, ValueError):
                payload["score"] = score
        with (results_dir / "error.json").open("w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except Exception as exc:  # pragma: no cover - best effort logging only
        sys.stderr.write(f"[Guest] Failed to write error.json: {exc}\n")
    try:
        subprocess.run(["sync"], check=False)
    except Exception:
        pass


def summarize_failure(prefix: str, exc: subprocess.CalledProcessError) -> str:
    """Summarize a command failure with return code and tail of stderr/stdout."""
    token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
    snippet = ""
    if exc.stderr:
        snippet = exc.stderr.strip()
    elif exc.stdout:
        snippet = exc.stdout.strip()
    if snippet:
        snippet = snippet.replace("\r", "")
        if len(snippet) > 800:
            snippet = snippet[-800:]
        summary = f"{prefix} failed (rc={exc.returncode}): {snippet}"
    else:
        summary = f"{prefix} failed (rc={exc.returncode})"
    if token:
        summary = summary.replace(token, "[REDACTED]")
    return summary


def run_cmd(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run a command and raise on failure while logging stderr/stdout."""
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, proc.stdout, proc.stderr)
    return proc


def run_streaming(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    """Run a command with live streaming while keeping a small failure buffer."""
    stdout_buf: deque[str] = deque(maxlen=50)
    stderr_buf: deque[str] = deque(maxlen=50)

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
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
        raise subprocess.CalledProcessError(
            proc.returncode,
            cmd,
            "\n".join(stdout_buf) if stdout_buf else None,
            "\n".join(stderr_buf) if stderr_buf else None,
        )


def main() -> int:
    workdir = Path(os.environ.get("WORKDIR", ".")).resolve()
    results_dir = Path(os.environ.get("RESULTS_DIR", "./results")).resolve()
    validator_dir = Path(os.environ.get("VALIDATOR_DIR", "/tmp/validator")).resolve()
    skip_tf = os.environ.get("SKIP_TF", "0") == "1"
    task_json_path = Path(os.environ.get("TASK_JSON_PATH", workdir / "task.json"))
    tfstate_path = Path(os.environ.get("TFSTATE_PATH", workdir / "terraform.tfstate"))
    token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
    if not token:
        write_error_json(results_dir, "Missing GOOGLE_OAUTH_ACCESS_TOKEN")
        return 1

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    terraform_runner = workdir / "terraform_runner.py"
    try:
        if not skip_tf:
            if terraform_runner.exists() and terraform_runner.is_file():
                print("[Guest] Running terraform runner...")
                try:
                    run_streaming(["python3", str(terraform_runner)], cwd=workdir, env=env)
                except subprocess.CalledProcessError as exc:
                    if not (results_dir / "error.json").exists():
                        write_error_json(results_dir, summarize_failure("Terraform runner", exc))
                    return 1
            else:
                write_error_json(results_dir, "Terraform runner not found in workspace")
                return 1
        else:
            print("[Guest] Skipping terraform execution (SKIP_TF=1).")

        validate_script = validator_dir / "validate.py"
        if not validate_script.exists():
            write_error_json(results_dir, f"Validator script not found at {validate_script}")
            return 1

        print("[Guest] Running validator...")
        cmd = [
            "python3",
            str(validate_script),
            "-t",
            str(task_json_path),
            "-s",
            str(tfstate_path),
            "--success-json",
            str(results_dir / "success.json"),
            "--error-json",
            str(results_dir / "error.json"),
        ]
        try:
            run_cmd(cmd, cwd=workdir, env=env)
        except subprocess.CalledProcessError as exc:
            if not (results_dir / "error.json").exists():
                write_error_json(results_dir, summarize_failure("Validator", exc))
            return 1

        return 0
    except subprocess.CalledProcessError as exc:
        write_error_json(results_dir, summarize_failure("Command", exc))
        return 1
    except Exception as exc:  # pylint: disable=broad-except
        write_error_json(results_dir, f"Guest runner exception: {exc}")
        return 1
    finally:
        try:
            subprocess.run(["sync"], check=False)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
