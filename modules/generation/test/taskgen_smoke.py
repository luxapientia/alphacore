#!/usr/bin/env python3
"""
Generate a batch of Terraform tasks and validate prompt/invariant constraints.

This is a developer smoke tool (not a pytest test) intended to help diagnose prompt
generation stability and missing/invalid required details derived from invariants.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

def _ensure_repo_on_path() -> Path:
    """
    Ensure the repository root is on sys.path so local imports work
    when running this file directly.
    """
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / ".git").is_dir() and (parent / "modules").is_dir():
            sys.path.insert(0, str(parent))
            return parent
    return here.parents[2]


_REPO_ROOT = _ensure_repo_on_path()

# Default config for generation registry if not provided by the caller.
if not os.getenv("ALPHACORE_CONFIG"):
    candidate = _REPO_ROOT / "modules" / "task_config.yaml"
    if candidate.is_file():
        os.environ["ALPHACORE_CONFIG"] = str(candidate)

from modules.generation.instructions import TaskInstructionGenerator  # noqa: E402
from modules.models import ACTaskSpec, Invariant, TaskSpec, TerraformTask  # noqa: E402


def _now_slug() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def _json_default(value: object) -> object:
    try:
        return asdict(value)  # type: ignore[arg-type]
    except Exception:
        return str(value)


def _safe_preview(text: str, limit: int = 280) -> str:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "…"


def _build_terraform_task_from_actask(spec: ACTaskSpec) -> TerraformTask:
    payload = spec.params if isinstance(spec.params, dict) else {}
    task_payload = payload.get("task") if isinstance(payload, dict) else None
    task_payload = task_payload if isinstance(task_payload, dict) else {}

    invariants_raw = task_payload.get("invariants")
    invariants_raw = invariants_raw if isinstance(invariants_raw, list) else []
    invariants: list[Invariant] = []
    for inv in invariants_raw:
        if not isinstance(inv, dict):
            continue
        resource_type = inv.get("resource_type")
        match = inv.get("match")
        if not isinstance(resource_type, str) or not resource_type.strip():
            continue
        if not isinstance(match, dict):
            match = {}
        invariants.append(Invariant(resource_type=resource_type, match=match))

    spec_obj = TaskSpec(
        version=str(task_payload.get("version") or "v0"),
        task_id=str(task_payload.get("task_id") or spec.task_id),
        nonce=str(task_payload.get("nonce") or ""),
        kind=str(task_payload.get("kind") or spec.kind),
        invariants=invariants,
        prompt=(spec.prompt or None),
        metadata=(task_payload.get("metadata") if isinstance(task_payload.get("metadata"), dict) else None),
    )
    return TerraformTask(
        engine=str(payload.get("engine") or "terraform"),
        provider=str(payload.get("provider") or spec.provider),
        validator_sa=str(payload.get("validator_sa") or ""),
        spec=spec_obj,
        instructions=spec.prompt,
    )


def _validate_actask(spec: ACTaskSpec) -> tuple[bool, list[dict[str, Any]]]:
    """
    Validate invariants integrity and prompt content against generator rules.

    Returns (ok, issues) where issues is a list of {kind, message, ...}.
    """
    issues: list[dict[str, Any]] = []

    if not isinstance(spec.prompt, str) or not spec.prompt.strip():
        issues.append({"kind": "prompt_missing", "message": "ACTaskSpec.prompt is empty"})

    if isinstance(spec.params, dict) and "prompt" in spec.params:
        issues.append({"kind": "prompt_leaked", "message": "spec.params unexpectedly contains top-level 'prompt'"})

    try:
        task_payload = spec.params.get("task") if isinstance(spec.params, dict) else None
        if not isinstance(task_payload, dict):
            issues.append({"kind": "task_payload_missing", "message": "spec.params['task'] missing or not a dict"})
        else:
            nested_prompt = task_payload.get("prompt")
            if isinstance(spec.prompt, str) and nested_prompt != spec.prompt:
                issues.append(
                    {
                        "kind": "prompt_mismatch",
                        "message": "spec.params['task']['prompt'] != spec.prompt",
                        "task_prompt_type": type(nested_prompt).__name__,
                    }
                )
            invariants = task_payload.get("invariants")
            if not isinstance(invariants, list) or not invariants:
                issues.append(
                    {"kind": "invariants_missing", "message": "spec.params['task']['invariants'] missing/empty"}
                )
    except Exception as exc:
        issues.append({"kind": "params_parse_error", "message": str(exc)})

    tf_task = _build_terraform_task_from_actask(spec)
    if not tf_task.spec.invariants:
        issues.append({"kind": "invariants_unparseable", "message": "No invariants could be parsed into dataclasses"})

    validator = TaskInstructionGenerator(enable_llm=False)
    try:
        validator._enforce_allowed_content(spec.prompt or "", tf_task)
    except Exception as exc:
        prompt_text = spec.prompt or ""
        required = validator._required_terms(tf_task)
        missing_terms = sorted(
            term
            for term in required
            if not validator._contains_required_detail(prompt_text, term) and term not in {"zip", "archive"}
        )
        if not (
            validator._contains_required_detail(prompt_text, "zip")
            or validator._contains_required_detail(prompt_text, "archive")
        ):
            missing_terms.insert(0, "zip or archive")
        invariants_preview = []
        for inv in (tf_task.spec.invariants or [])[:5]:
            match = getattr(inv, "match", None)
            invariants_preview.append(
                {
                    "resource_type": getattr(inv, "resource_type", None),
                    "match_keys": sorted(list(match.keys())) if isinstance(match, dict) else [],
                }
            )
        issues.append(
            {
                "kind": "prompt_invalid",
                "message": str(exc),
                "missing_terms": missing_terms[:80],
                "missing_terms_count": len(missing_terms),
                "missing_term_details": {
                    term: validator._missing_detail_origin(tf_task, term) for term in missing_terms[:80]
                },
                "invariants_preview": invariants_preview,
                "prompt_preview": _safe_preview(prompt_text, 360),
            }
        )

    return (len(issues) == 0), issues


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate + validate task prompts/invariants.")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--disable-llm",
        action="store_true",
        help="Force deterministic prompt generation (sets ALPHACORE_ENABLE_LLM=false).",
    )
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print the full natural-language prompt for each generated task.",
    )
    parser.add_argument(
        "--include-prompt",
        action="store_true",
        help="Include the full prompt text in the JSONL output (in addition to a preview).",
    )
    parser.add_argument(
        "--include-trace",
        action="store_true",
        help="Include the full generation trace dict in JSONL output (verbose).",
    )
    parser.add_argument(
        "--include-llm-raw",
        action="store_true",
        help="Include the full raw LLM text in JSONL output (verbose).",
    )
    parser.add_argument(
        "--fallback-on-failure",
        action="store_true",
        help="If LLM is enabled and fails, fall back to deterministic prompt generation.",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Output JSONL path (default: logs/taskgen/taskgen_smoke_<timestamp>.jsonl).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete prior smoke logs (logs/taskgen) and repository tasks before generating.",
    )
    args = parser.parse_args(argv)

    count = max(1, int(args.count))
    seed = int(args.seed)
    random.seed(seed)

    # Capture raw LLM preview in `TaskInstructionGenerator.last_trace` by default so
    # failures can be debugged from the JSONL without re-running.
    os.environ["ALPHACORE_LOG_LLM_RAW"] = "true" if args.include_llm_raw else "false"
    os.environ["ALPHACORE_LOG_LLM_RAW_PREVIEW_CHARS"] = os.getenv("ALPHACORE_LOG_LLM_RAW_PREVIEW_CHARS", "2000")
    os.environ["ALPHACORE_LOG_LLM_RAW_MAX_CHARS"] = os.getenv("ALPHACORE_LOG_LLM_RAW_MAX_CHARS", "20000")
    os.environ.setdefault("ALPHACORE_LLM_MAX_OUTPUT_TOKENS", "4096")

    if args.disable_llm:
        os.environ["ALPHACORE_ENABLE_LLM"] = "false"
    if args.fallback_on_failure:
        os.environ["ALPHACORE_LLM_FALLBACK"] = "true"

    # Import after environment variables are applied: importing the pipeline
    # instantiates the task registry, which reads config and prompt settings.
    from modules.generation.pipeline import TaskGenerationPipeline

    if args.clean:
        def _rm_tree(path: Path) -> None:
            if not path.exists():
                return
            if path.is_file() or path.is_symlink():
                try:
                    path.unlink()
                except Exception:
                    pass
                return
            for child in sorted(path.glob("**/*"), reverse=True):
                try:
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                except Exception:
                    pass
            try:
                path.rmdir()
            except Exception:
                pass

        # Clean smoke logs under this folder.
        _rm_tree(Path("logs") / "taskgen")

        # Clean generated task fixtures if present (file repository writes under generation/test/tasks by default).
        _rm_tree(Path("tasks"))

    out_path = Path(args.out) if args.out else Path("logs/taskgen") / f"taskgen_smoke_{_now_slug()}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    generator = TaskInstructionGenerator()
    pipeline = TaskGenerationPipeline(instruction_generator=generator)

    summary = {
        "count": count,
        "seed": seed,
        "disable_llm": bool(args.disable_llm),
        "fallback_on_failure": bool(args.fallback_on_failure),
        "enable_llm_effective": bool(getattr(generator, "enable_llm", False)),
        "model": getattr(generator, "model", None),
        "temperature": getattr(generator, "temperature", None),
        "out": str(out_path),
        "started_at": time.time(),
        "ok": 0,
        "failed_generate": 0,
        "failed_validate": 0,
        "ok_first_attempt": 0,
        "ok_multi_attempt": 0,
        "attempt_histogram": {},
        "tokens_total_sum": 0,
        "tokens_total_count": 0,
        "duration_total_sum": 0.0,
        "duration_total_count": 0,
    }

    with out_path.open("w", encoding="utf-8") as handle:
        for i in range(count):
            record: dict[str, Any] = {
                "i": i,
                "ts": time.time(),
                "status": "unknown",
                "error": None,
                "task": {
                    "task_id": None,
                    "provider": None,
                    "kind": None,
                    "prompt": None,
                    "prompt_preview": None,
                    "prompt_chars": None,
                },
                "generation": {
                    "model": None,
                    "final_attempt": None,
                    "attempts_count": None,
                    "total_duration_s": None,
                    "llm_raw_sha256": None,
                    "llm_raw_preview": None,
                    "llm_raw": None,
                    "usage": {
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "total_tokens": None,
                    },
                    "auto_fix": {
                        "validator_sa": None,
                        "disallowed_terms": None,
                        "invariant_terms": None,
                    },
                },
                "validation": {
                    "issues": None,
                },
            }
            try:
                spec = pipeline.generate()
                ok, issues = _validate_actask(spec)
                trace = getattr(generator, "last_trace", None)
                record["task"].update(
                    {
                        "task_id": spec.task_id,
                        "provider": spec.provider,
                        "kind": spec.kind,
                        "prompt": (spec.prompt or "") if args.include_prompt else None,
                        "prompt_preview": _safe_preview(spec.prompt or "", 280),
                        "prompt_chars": len(spec.prompt or ""),
                    }
                )
                if isinstance(trace, dict):
                    record["generation"]["model"] = trace.get("model")
                    record["generation"]["llm_raw_sha256"] = trace.get("llm_raw_sha256")
                    record["generation"]["llm_raw_preview"] = trace.get("llm_raw_preview")
                    record["generation"]["llm_raw"] = trace.get("llm_raw") if args.include_llm_raw else None
                    final_attempt = trace.get("final_attempt")
                    if isinstance(final_attempt, int):
                        record["generation"]["final_attempt"] = final_attempt
                        if final_attempt == 1:
                            summary["ok_first_attempt"] += 1
                        else:
                            summary["ok_multi_attempt"] += 1
                        hist = summary.get("attempt_histogram") or {}
                        hist[str(final_attempt)] = int(hist.get(str(final_attempt), 0)) + 1
                        summary["attempt_histogram"] = hist
                    attempts = trace.get("attempts")
                    if isinstance(final_attempt, int):
                        record["generation"]["attempts_count"] = int(final_attempt)
                    elif isinstance(attempts, list):
                        record["generation"]["attempts_count"] = len(attempts)
                    dur = trace.get("duration_s")
                    if isinstance(dur, (int, float)):
                        record["generation"]["total_duration_s"] = float(dur)
                        summary["duration_total_sum"] += float(dur)
                        summary["duration_total_count"] += 1
                    usage = trace.get("usage")
                    if isinstance(usage, dict):
                        record["generation"]["usage"]["prompt_tokens"] = usage.get("prompt_tokens")
                        record["generation"]["usage"]["completion_tokens"] = usage.get("completion_tokens")
                        record["generation"]["usage"]["total_tokens"] = usage.get("total_tokens")
                        total = usage.get("total_tokens")
                        if isinstance(total, int):
                            summary["tokens_total_sum"] += int(total)
                            summary["tokens_total_count"] += 1
                    record["generation"]["auto_fix"]["validator_sa"] = bool(trace.get("auto_fixed_validator_sa"))
                    record["generation"]["auto_fix"]["disallowed_terms"] = trace.get("auto_fixed_disallowed_terms") or []
                    record["generation"]["auto_fix"]["invariant_terms"] = trace.get("auto_fixed_invariant_terms") or []
                    if args.include_trace:
                        record["generation"]["trace"] = trace
                if args.print_prompt:
                    raw = ""
                    if isinstance(trace, dict):
                        raw = str(trace.get("llm_raw") or trace.get("llm_raw_preview") or "")
                    if raw:
                        print(f"\n# llm_raw task_id={spec.task_id} kind={spec.kind}\n{raw}\n")
                    print(f"\n# prompt task_id={spec.task_id} kind={spec.kind}\n{spec.prompt or ''}\n")
                if ok:
                    record["status"] = "ok"
                    summary["ok"] += 1
                else:
                    record["status"] = "invalid"
                    record["validation"]["issues"] = issues
                    summary["failed_validate"] += 1
            except Exception as exc:
                record["status"] = "error"
                record["error"] = {"type": type(exc).__name__, "message": str(exc)}
                trace = getattr(generator, "last_trace", None)
                if isinstance(trace, dict):
                    record["task"]["task_id"] = trace.get("task_id") or record["task"]["task_id"]
                    record["task"]["provider"] = trace.get("provider") or record["task"]["provider"]
                    record["task"]["kind"] = trace.get("task_kind") or record["task"]["kind"]
                    record["generation"]["model"] = trace.get("model")
                    record["generation"]["llm_raw_sha256"] = trace.get("llm_raw_sha256")
                    record["generation"]["llm_raw_preview"] = trace.get("llm_raw_preview")
                    record["generation"]["llm_raw"] = trace.get("llm_raw") if args.include_llm_raw else None
                    final_attempt = trace.get("final_attempt")
                    if isinstance(final_attempt, int):
                        record["generation"]["final_attempt"] = final_attempt
                        record["generation"]["attempts_count"] = int(final_attempt)
                    dur = trace.get("duration_s")
                    if isinstance(dur, (int, float)):
                        record["generation"]["total_duration_s"] = float(dur)
                    usage = trace.get("usage")
                    if isinstance(usage, dict):
                        record["generation"]["usage"]["prompt_tokens"] = usage.get("prompt_tokens")
                        record["generation"]["usage"]["completion_tokens"] = usage.get("completion_tokens")
                        record["generation"]["usage"]["total_tokens"] = usage.get("total_tokens")
                    if args.include_trace:
                        record["generation"]["trace"] = trace
                if args.print_prompt and isinstance(trace, dict):
                    raw = str(trace.get("llm_raw") or trace.get("llm_raw_preview") or "")
                    if not raw:
                        attempts = trace.get("attempts") or []
                        if isinstance(attempts, list) and attempts:
                            last = attempts[-1]
                            if isinstance(last, dict):
                                raw = str(last.get("llm_raw") or last.get("llm_raw_preview") or "")
                    if raw:
                        print(f"\n# llm_raw (failure)\n{raw}\n")
                summary["failed_generate"] += 1

            handle.write(json.dumps(record, default=_json_default, sort_keys=False) + "\n")

    summary["duration_s"] = time.time() - float(summary["started_at"])
    if summary.get("tokens_total_count", 0):
        summary["tokens_total_avg"] = float(summary["tokens_total_sum"]) / float(summary["tokens_total_count"])
    if summary.get("duration_total_count", 0):
        summary["duration_total_avg_s"] = float(summary["duration_total_sum"]) / float(summary["duration_total_count"])
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(
        f"taskgen_smoke: ok={summary['ok']} invalid={summary['failed_validate']} "
        f"errors={summary['failed_generate']} out={out_path}"
    )
    return 0 if summary["failed_generate"] == 0 and summary["failed_validate"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
