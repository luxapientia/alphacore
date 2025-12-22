"""AlphaCore Subnet - Bittensor subnet-specific code and validators.

For task generation and evaluation, import directly from `modules`:

    from modules.generation import TaskGenerationPipeline
    from modules.evaluation import Evaluator
    from modules.models import ACTaskSpec
"""

from __future__ import annotations

import sys

# Backward-compat alias for legacy imports.
sys.modules.setdefault("alphacore_subnet", sys.modules[__name__])

__version__ = "1.0.0"
__least_acceptable_version__ = "1.0.0"
version_split = __version__.split(".")
__spec_version__ = (1000 * int(version_split[0])) + (10 * int(version_split[1])) + (1 * int(version_split[2]))

__all__ = [
    "__version__",
    "__spec_version__",
]
