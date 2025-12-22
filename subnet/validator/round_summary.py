"""
Per-round JSON summary materialization for the validator.

The validator already writes an append-only JSONL ledger. This module produces
one JSON file per completed round that is easy to inspect or upload later.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    return str(value)


class RoundSummaryWriter:
    def __init__(self, *, output_dir: Optional[str] = None, enabled: Optional[bool] = None) -> None:
        self.enabled = enabled if enabled is not None else self._get_bool_env(
            "ALPHACORE_ROUND_SUMMARY_ENABLED", True
        )
        self.output_dir = Path(output_dir or os.getenv("ALPHACORE_ROUND_SUMMARY_DIR", "logs/ledger/rounds"))

    @staticmethod
    def _get_bool_env(key: str, default: bool) -> bool:
        raw = os.getenv(key)
        if raw is None:
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")

    def write_from_validator(self, validator: Any, round_id: str) -> Optional[Path]:
        if not self.enabled:
            return None

        # Best-effort: never break validator execution due to telemetry.
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None

        try:
            summary = self._build_summary(validator, round_id)
        except Exception:
            return None

        out_path = self.output_dir / f"{round_id}.json"
        latest_path = self.output_dir / "latest.json"
        try:
            out_path.write_text(json.dumps(_to_jsonable(summary), ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
            latest_path.write_text(json.dumps(_to_jsonable(summary), ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
            return out_path
        except Exception:
            return None

    def _build_summary(self, validator: Any, round_id: str) -> Dict[str, Any]:
        now = time.time()

        cfg = getattr(validator, "config", None)
        wallet = getattr(validator, "wallet", None)
        hotkey = getattr(getattr(wallet, "hotkey", None), "ss58_address", None) if wallet is not None else None
        axon = getattr(validator, "axon", None)

        tasks = []
        try:
            tasks = list(getattr(validator, "get_current_round_tasks", lambda: [])() or [])
        except Exception:
            tasks = []

        handshake_uids = []
        handshake_responses = {}
        try:
            handshake_uids = list(getattr(validator, "get_active_miner_uids", lambda _rid: [])(round_id) or [])
            handshake_responses = dict(getattr(validator, "get_handshake_responses", lambda _rid: {})(round_id) or {})
        except Exception:
            handshake_uids = []
            handshake_responses = {}

        responses = {}
        latencies = {}
        dispatch_failures = {}
        try:
            responses = dict(getattr(validator, "get_task_responses", lambda _rid: {})(round_id) or {})
            latencies = dict(getattr(validator, "get_latencies", lambda _rid: {})(round_id) or {})
            dispatch_failures = dict(getattr(validator, "get_dispatch_failures", lambda _rid: {})(round_id) or {})
        except Exception:
            responses = {}
            latencies = {}
            dispatch_failures = {}

        scores = {}
        validation_results = {}
        validation_attempts = {}
        try:
            scores = dict(getattr(validator, "get_scores", lambda _rid: {})(round_id) or {})
            validation_results = dict(getattr(validator, "get_validation_results", lambda _rid: {})(round_id) or {})
            validation_attempts = dict(getattr(validator, "get_validation_attempts", lambda _rid: {})(round_id) or {})
        except Exception:
            scores = {}
            validation_results = {}
            validation_attempts = {}

        settlement = {}
        try:
            settlement = dict(getattr(validator, "_settlement_by_round", {}).get(round_id, {}) or {})
        except Exception:
            settlement = {}

        generation_traces = {}
        try:
            generation_traces = dict(getattr(validator, "_task_generation_traces", {}) or {})
        except Exception:
            generation_traces = {}

        task_summaries = []
        for task in tasks:
            task_id = getattr(task, "task_id", None) if not isinstance(task, dict) else task.get("task_id")
            task_id = str(task_id or "")
            prompt = getattr(task, "prompt", None) if not isinstance(task, dict) else task.get("prompt")
            params = getattr(task, "params", None) if not isinstance(task, dict) else task.get("params")
            invariants = []
            try:
                if isinstance(params, dict):
                    task_obj = params.get("task") or {}
                    if isinstance(task_obj, dict):
                        invariants = task_obj.get("invariants") or []
            except Exception:
                invariants = []
            trace = generation_traces.get(task_id, {})
            task_summaries.append(
                {
                    "task_id": task_id,
                    "provider": getattr(task, "provider", None) if not isinstance(task, dict) else task.get("provider"),
                    "kind": getattr(task, "kind", None) if not isinstance(task, dict) else task.get("kind"),
                    "prompt": prompt,
                    "invariants": invariants,
                    "prompt_generation": trace.get("prompt_trace") if isinstance(trace, dict) else None,
                    "prompt_generation_time_s": trace.get("generation_time_s") if isinstance(trace, dict) else None,
                }
            )

        miner_summaries = []
        # responses: uid -> task_id -> TaskSynapse|None
        for uid, by_task in (responses or {}).items():
            try:
                uid_int = int(uid)
            except Exception:
                continue
            tasks_for_uid = []
            if isinstance(by_task, dict):
                for task_id, syn in by_task.items():
                    latency = None
                    try:
                        latency = float(latencies.get((uid_int, task_id), latencies.get(uid_int, 0.0)))
                    except Exception:
                        latency = None
                    failure = None
                    try:
                        failure = dispatch_failures.get((uid_int, str(task_id)))
                    except Exception:
                        failure = None
                    tasks_for_uid.append(
                        {
                            "task_id": str(task_id),
                            "ack": bool(syn is not None),
                            "dispatch_failure": failure,
                            "latency_s": latency,
                            "workspace_zip_sha256": getattr(syn, "workspace_zip_sha256", None) if syn is not None else None,
                            "workspace_zip_size_bytes": getattr(syn, "workspace_zip_size_bytes", None) if syn is not None else None,
                        }
                    )
            miner_resp = handshake_responses.get(uid_int)
            uid_validation_attempts = validation_attempts.get(uid_int, {}) if isinstance(validation_attempts, dict) else {}
            miner_summaries.append(
                {
                    "uid": uid_int,
                    "hotkey": getattr(getattr(validator, "metagraph", None), "hotkeys", [None] * (uid_int + 1))[uid_int]
                    if getattr(validator, "metagraph", None) is not None and uid_int < len(getattr(validator.metagraph, "hotkeys", []))
                    else None,
                    "is_alive": bool(uid_int in handshake_uids),
                    "handshake": {
                        "miner_version": getattr(miner_resp, "miner_version", None) if miner_resp is not None else None,
                        "available_capacity": getattr(miner_resp, "available_capacity", None) if miner_resp is not None else None,
                        "error_message": getattr(miner_resp, "error_message", None) if miner_resp is not None else None,
                    },
                    "tasks": tasks_for_uid,
                    "final_score": float(scores.get(uid_int, 0.0)) if uid_int in scores else None,
                    "validation": validation_results.get(uid_int, {}),
                    "validation_attempts": uid_validation_attempts,
                }
            )

        # Round-level validation summary (even when API is down).
        validation_status_counts: Dict[str, int] = {}
        try:
            for uid, by_task in (validation_attempts or {}).items():
                if not isinstance(by_task, dict):
                    continue
                for _, attempt in by_task.items():
                    status = None
                    if isinstance(attempt, dict):
                        status = attempt.get("status")
                    status = str(status or "unknown")
                    validation_status_counts[status] = validation_status_counts.get(status, 0) + 1
        except Exception:
            validation_status_counts = {}

        return {
            "round_id": str(round_id),
            "generated_at": float(now),
            "validator": {
                "hotkey": hotkey,
                "uid": getattr(validator, "uid", None),
                "netuid": getattr(cfg, "netuid", None) if cfg is not None else None,
                "chain_endpoint": getattr(getattr(cfg, "subtensor", None), "chain_endpoint", None) if cfg is not None else None,
                "axon_ip": getattr(axon, "external_ip", None) if axon is not None else None,
                "axon_port": getattr(axon, "external_port", None) if axon is not None else None,
                "process_name": os.getenv("PROCESS_NAME") or None,
            },
            "tasks": task_summaries,
            "miners": miner_summaries,
            "validation_summary": {
                "status_counts": validation_status_counts,
            },
            "settlement": settlement,
        }
