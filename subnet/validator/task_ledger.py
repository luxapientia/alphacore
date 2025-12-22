"""
Validator-side task ledger.

Stores JSONL events describing:
- task generation (prompt + invariants + generation trace)
- handshake/dispatch mapping of tasks -> miners
- evaluation results (scores, validation summary)
- settlement outputs (weights/burn)

This is intentionally a lightweight local append-only log so we can later ingest
it into a database without changing the validator/miner protocol.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from threading import Lock
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


class TaskLedger:
    def __init__(
        self,
        *,
        path: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.enabled = enabled if enabled is not None else self._get_bool_env(
            "ALPHACORE_TASK_LEDGER_ENABLED", True
        )
        self.path = Path(path or self._default_path())
        self._lock = Lock()

        if self.enabled:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                # Never fail validator execution due to telemetry.
                self.enabled = False

    @staticmethod
    def _get_bool_env(key: str, default: bool) -> bool:
        raw = os.getenv(key)
        if raw is None:
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _default_path() -> str:
        explicit = os.getenv("ALPHACORE_TASK_LEDGER_PATH")
        if explicit:
            return explicit
        process_name = os.getenv("PROCESS_NAME") or os.getenv("ALPHACORE_PROCESS_NAME") or "validator"
        return str(Path("logs") / "ledger" / f"{process_name}.jsonl")

    def write(self, event: str, payload: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        record = {
            "ts": time.time(),
            "event": str(event),
            **_to_jsonable(payload),
        }
        line = json.dumps(record, ensure_ascii=True, sort_keys=False)
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            # Never fail validator execution due to telemetry.
            return

