"""
Reward calculation and burning mechanism for AlphaCore validators.

Implements:
1. Winner-takes-all (WTA) reward distribution
2. Burning mechanism - send % of rewards to burn UID, rest to winner
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from subnet.validator.config import BURN_AMOUNT_PERCENTAGE, BURN_UID


def wta_rewards(avg_rewards: NDArray[np.float32]) -> NDArray[np.float32]:
    """
    Winner-takes-all transform used for final weight selection.
    
    Selects the miner with the highest average reward and assigns it 1.0,
    all others get 0.0.
    
    Args:
        avg_rewards: Array of average rewards per miner
        
    Returns:
        Array with 1.0 for winner, 0.0 for all others
    """
    if avg_rewards.size == 0:
        return avg_rewards

    arr = np.asarray(avg_rewards, dtype=np.float32)
    mask_nan = ~np.isfinite(arr)
    
    if np.any(mask_nan):
        temp = arr.copy()
        temp[mask_nan] = -np.inf
        winner = int(np.argmax(temp))
    else:
        winner = int(np.argmax(arr))

    out = np.zeros_like(arr, dtype=np.float32)
    out[winner] = 1.0
    return out


def apply_burn_mechanism(
    rewards: NDArray[np.float32],
    burn_uid: int,
    burn_percentage: float = BURN_AMOUNT_PERCENTAGE,
) -> NDArray[np.float32]:
    """
    Apply burning mechanism to rewards.
    
    Sends `burn_percentage` of total rewards to the burn UID, and distributes
    the remainder (1 - burn_percentage) to the winning miner.
    
    Example:
        If total rewards = 1.0, burn_percentage = 0.925, burn_uid = 5:
        - Burn UID gets: 0.925
        - Winner gets: 0.075
    
    Args:
        rewards: WTA rewards (should have single winner with value 1.0)
        burn_uid: UID that receives burned tokens
        burn_percentage: Fraction to burn (default 0.925 = 92.5% burn)
        
    Returns:
        Adjusted rewards array with burning applied
    """
    if rewards.size == 0:
        return rewards
    
    arr = np.asarray(rewards, dtype=np.float32)
    
    # Find the winner (highest reward)
    winner_uid = int(np.argmax(arr))
    
    # Calculate burn and winner amounts
    total_rewards = np.sum(arr)
    if total_rewards <= 0:
        return arr
    
    burn_amount = total_rewards * burn_percentage
    winner_amount = total_rewards * (1.0 - burn_percentage)
    
    # Create new reward distribution
    burned_rewards = np.zeros_like(arr, dtype=np.float32)
    
    # Assign to burn UID (if within bounds)
    if 0 <= burn_uid < len(burned_rewards):
        burned_rewards[burn_uid] = burn_amount
    
    # Assign to winner (if different from burn UID)
    if 0 <= winner_uid < len(burned_rewards) and winner_uid != burn_uid:
        burned_rewards[winner_uid] = winner_amount
    elif winner_uid == burn_uid:
        # If winner is burn UID, combine both amounts
        burned_rewards[winner_uid] += winner_amount
    
    return burned_rewards
