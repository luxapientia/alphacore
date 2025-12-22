from __future__ import annotations

from modules.generation.terraform.providers.gcp import compositions
from modules.generation.terraform.providers.gcp.task_bank import (
    GCPDynamicTaskBank,
)

_BANK = GCPDynamicTaskBank(
    min_resources=1,
    max_resources=1,
    families=compositions.SINGLE_RESOURCE_FAMILIES,
)


def build_task(validator_sa: str):
    return _BANK.build_task(validator_sa)
