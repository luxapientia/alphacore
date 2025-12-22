"""
Round state checkpoint and recovery system.

Enables crash recovery and round state persistence.
"""

from .manager import CheckpointManager

__all__ = ["CheckpointManager"]
