"""Lightweight mock primitives for offline validator/miner runs.

These mocks avoid network/chain access while preserving the minimal
attributes and call semantics the neurons expect.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, List, Optional


class MockAxon:
    def __init__(self, ip: str = "127.0.0.1", port: int = 8091, hotkey: str = "mock-hotkey") -> None:
        self.ip = ip
        self.port = port
        self.hotkey = hotkey


class MockMetagraph:
    def __init__(self, netuid: int = 1, n: int = 0, subtensor: "MockSubtensor | None" = None) -> None:
        self.netuid = netuid
        self.n = n
        self.subtensor = subtensor
        self.uids = list(range(n))
        self.axons: List[MockAxon | None] = [MockAxon(port=8091 + i) for i in range(n)]
        self.hotkeys = [axon.hotkey for axon in self.axons if axon is not None]

    def sync(self, subtensor: "MockSubtensor | None" = None) -> None:
        if subtensor is not None:
            self.subtensor = subtensor


class MockSubtensor:
    def __init__(self, netuid: int = 1, n: int = 0) -> None:
        self.netuid = netuid
        self._metagraph = MockMetagraph(netuid=netuid, n=n, subtensor=self)

    def metagraph(self, netuid: int) -> MockMetagraph:  # noqa: D401
        _ = netuid  # unused; a single metagraph is maintained
        return self._metagraph

    def get_current_block(self) -> int:
        return int(time.time())

    def set_weights(self, **_: Any) -> tuple[bool, str]:
        # Allow callers to run without chain interaction.
        return True, "mock-set-weights"


class MockDendrite:
    def __init__(self, wallet: Any = None) -> None:
        self.wallet = wallet

    async def __call__(
        self,
        axons: List[Any],
        synapse: Any,
        deserialize: bool = True,
        timeout: float = 60,  # seconds
        **_: Any,
    ) -> List[Optional[Any]]:
        # Return one placeholder per axon; callers treat None as no-response.
        await asyncio.sleep(0)  # ensure async signature mirrors real dendrite
        return [None for _ in axons]


# Backwards-compatible aliases
StubAxon = MockAxon
StubMetagraph = MockMetagraph
StubSubtensor = MockSubtensor
StubDendrite = MockDendrite


__all__ = [
    "MockAxon",
    "MockMetagraph",
    "MockSubtensor",
    "MockDendrite",
    "StubAxon",
    "StubMetagraph",
    "StubSubtensor",
    "StubDendrite",
]
