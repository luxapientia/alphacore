"""Settlement phase: consensus, reward distribution, and weight finalization."""

from .mixin import SettlementMixin
from .rewards import wta_rewards, apply_burn_mechanism

__all__ = [
    "SettlementMixin",
    "wta_rewards",
    "apply_burn_mechanism",
]
