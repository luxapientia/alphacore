import json
from pathlib import Path

from modules.generation.terraform.registry import terraform_task_registry

# Randomly select a GCP task builder based on actual files
task_name, builder = terraform_task_registry.pick_random_task("gcp")
task = builder(validator_sa="alpha-core-validator@alph-478521.iam.gserviceaccount.com")

# Print the generated prompt
print(f"\n{'='*70}")
print(f"Task: {task_name}")
print(f"{'='*70}")
print(f"PROMPT:\n{task.spec.prompt or task.instructions}")
print(f"{'='*70}\n")


# Persist to files
base_dir = Path(__file__).resolve().parent
out_dir = base_dir / "tasks" / (task.spec.nonce or "unknown-nonce")
out_dir.mkdir(parents=True, exist_ok=True)

miner_payload = {
    "prompt": (task.spec.prompt or task.instructions or "").strip(),
}
with open(out_dir / "miner.json", "w", encoding="utf-8") as f:
    json.dump(miner_payload, f, indent=2, ensure_ascii=False)

with open(out_dir / "validator.json", "w", encoding="utf-8") as f:
    json.dump(task.to_dict(), f, indent=2, ensure_ascii=False)

print(f"✅ Wrote miner.json and validator.json to: {out_dir}")
