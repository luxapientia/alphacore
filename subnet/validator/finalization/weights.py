"""
Lightweight weight setting helpers for the validator.

This keeps weight setting trivial so the validator can run end-to-end
even if the scoring signal is placeholder. If no scores are provided,
all miners get uniform weight. If scores exist, they are normalized.
"""

from __future__ import annotations

from typing import Dict

import bittensor as bt
import numpy as np


def set_validator_weights(validator: "Validator", scores: Dict[int, float]) -> bool:
    """Set weights on-chain (or log) using a simple normalization scheme.

    This deliberately does the minimum required to make the validator runnable:
    - If no scores are present, assign uniform weights to all miners.
    - If scores are present, normalize them to sum to 1.0.
    - If subtensor/metagraph are unavailable, log and return False.
    """

    if validator.metagraph is None:
        bt.logging.warning("No metagraph available; skipping weight setting")
        return False

    if validator.subtensor is None:
        bt.logging.warning("No subtensor available; skipping weight setting")
        return False

    try:
        n = validator.metagraph.n
        all_uids = np.arange(n, dtype=np.int64)

        weights = np.zeros(n, dtype=np.float32)

        if scores:
            for uid, score in scores.items():
                if 0 <= int(uid) < n:
                    weights[int(uid)] = float(score)
        else:
            # Uniform fallback when no scores were produced
            weights[:] = 1.0

        total = float(weights.sum())
        if total > 0:
            weights /= total
        else:
            # All zero scores → uniform distribution
            weights[:] = 1.0 / max(n, 1)

        bt.logging.info(
            f"Setting weights | miners={n} | nonzero={np.count_nonzero(weights)} | sum={weights.sum():.4f}"
        )

        success = validator.subtensor.set_weights(
            wallet=validator.wallet,
            netuid=validator.config.netuid,
            uids=all_uids,
            weights=weights,
            wait_for_inclusion=False,
            wait_for_finalization=False,
        )

        if success:
            top = sorted(enumerate(weights.tolist()), key=lambda x: x[1], reverse=True)[:3]
            bt.logging.info(f"✓ Weights set successfully; top miners: {top}")
        else:
            bt.logging.warning("⚠️ set_weights reported failure")

        return bool(success)

    except Exception as e:
        bt.logging.error(f"✗ Error while setting weights: {e}")
        return False
