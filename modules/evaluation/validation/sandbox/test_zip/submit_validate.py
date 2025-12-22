#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _post_json(url: str, payload: dict[str, Any], timeout_s: float = 30.0) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        return int(exc.code), body


def _start_wait_spinner(label: str, *, interval_s: float = 0.2, line_every_s: float = 10.0) -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()
    start = time.time()

    def _run() -> None:
        spinner = "|/-\\"
        idx = 0
        last_line = 0.0
        is_tty = sys.stderr.isatty()
        while not stop.is_set():
            elapsed = time.time() - start
            if is_tty:
                sys.stderr.write(f"\r{label} {spinner[idx % len(spinner)]}  elapsed={int(elapsed)}s")
                sys.stderr.flush()
                idx += 1
            else:
                if elapsed - last_line >= line_every_s:
                    sys.stderr.write(f"{label} elapsed={int(elapsed)}s\n")
                    sys.stderr.flush()
                    last_line = elapsed
            time.sleep(max(0.05, float(interval_s)))
        if is_tty:
            sys.stderr.write("\r")
            sys.stderr.flush()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return stop, t


def main() -> int:
    script_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description="Submit a /validate request to the validator sandbox API.")
    parser.add_argument("--api-url", default=os.environ.get("API_URL", "http://127.0.0.1:8888"))
    parser.add_argument("--zip-path", type=Path, default=script_dir / "miner-result.zip")
    parser.add_argument("--task-json", type=Path, default=script_dir / "task.json")
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--net-checks", action="store_true")
    parser.add_argument("--stream-log", action="store_true")
    parser.add_argument("--no-quiet-kernel", dest="quiet_kernel", action="store_false", default=True)
    args = parser.parse_args()

    zip_path = args.zip_path.resolve()
    task_json_path = args.task_json.resolve()

    if not zip_path.is_file():
        sys.stderr.write(f"zip not found: {zip_path}\n")
        return 1
    if not task_json_path.is_file():
        sys.stderr.write(f"task.json not found: {task_json_path}\n")
        return 1

    payload: dict[str, Any] = {
        "workspace_zip_path": str(zip_path),
        "task_json": json.loads(task_json_path.read_text(encoding="utf-8")),
        "timeout_s": int(args.timeout_s),
        "net_checks": bool(args.net_checks),
        "stream_log": bool(args.stream_log),
        "quiet_kernel": bool(args.quiet_kernel),
    }

    url = args.api_url.rstrip("/") + "/validate"
    task_id = None
    try:
        task_id = payload.get("task_json", {}).get("task_id")
    except Exception:
        task_id = None
    sys.stderr.write(f"POST {url}\n")
    sys.stderr.write(f"zip_path={zip_path}\n")
    if task_id:
        sys.stderr.write(f"task_id={task_id}\n")
    sys.stderr.write(
        "Tip: in another terminal you can watch active logs with:\n"
        '  tail -F "$PWD/logs/validation/active"/*.log\n'
    )
    sys.stderr.flush()

    stop, spinner_thread = _start_wait_spinner("waiting for /validate response...")
    try:
        status, body = _post_json(url, payload, timeout_s=float(args.timeout_s) + 60.0)
    finally:
        stop.set()
        spinner_thread.join(timeout=2.0)
        if sys.stderr.isatty():
            sys.stderr.write("\n")
            sys.stderr.flush()
    print(body)
    if not (200 <= status < 300):
        return 1

    # The API returns the final result payload (success.json or error.json) directly.
    try:
        resp = json.loads(body)
        if isinstance(resp, dict):
            job_id = resp.get("job_id")
            task_id = resp.get("task_id")
            log_path = resp.get("log_path")
            submission_path = resp.get("submission_path")
            if job_id:
                sys.stderr.write(f"job_id={job_id} task_id={task_id or ''}\n")
            if log_path:
                sys.stderr.write(f"log_path={log_path}\n")
            if submission_path:
                sys.stderr.write(f"submission_path={submission_path}\n")
        result = resp.get("result") if isinstance(resp, dict) else None
        status_value = result.get("status") if isinstance(result, dict) else None
        return 0 if status_value == "pass" else 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
