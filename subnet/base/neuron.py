# The MIT License (MIT)
# Copyright © 2023 Yuma Rao

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import copy
import threading
import bittensor as bt
from abc import ABC, abstractmethod

# Sync calls set weights and also resyncs the metagraph.
from subnet.base.utils.config import check_config, add_args, config
from subnet.base.utils.misc import ttl_get_block
import time
import traceback
import requests
import re
from subnet import __version__, __least_acceptable_version__, __spec_version__


class BaseNeuron(ABC):
    """
    Base class for Bittensor miners. This class is abstract and should be inherited by a subclass. It contains the core logic for all neurons; validators and miners.

    In addition to creating a wallet, subtensor, and metagraph, this class also handles the synchronization of the network state via a basic checkpointing mechanism based on epoch length.
    """

    neuron_type: str = "BaseNeuron"

    @classmethod
    def check_config(cls, config: "bt.Config"):
        check_config(cls, config)

    @classmethod
    def add_args(cls, parser):
        add_args(cls, parser)

    @classmethod
    def config(cls):
        return config(cls)

    subtensor: "bt.Subtensor"
    wallet: "bt.wallet"
    metagraph: "bt.metagraph"
    spec_version: int = __spec_version__

    @property
    def block(self):
        return ttl_get_block(self)

    def __init__(self, config=None):
        base_config = copy.deepcopy(config or BaseNeuron.config())
        self.config = self.config()
        self.config.merge(base_config)
        self.check_config(self.config) 

        neuron_name = str(getattr(getattr(self.config, "neuron", None), "name", "") or "").lower()
        # Avoid relying on `self.neuron_type` string equality because subclasses may override it.
        # Miner configs include a `blacklist.*` group; validator configs typically do not.
        self._is_miner_role = neuron_name.startswith("miner") or hasattr(self.config, "blacklist")

        # Version check
        self.parse_versions()

        # Set up logging with the provided configuration.
        bt.logging.set_config(config=self.config.logging)

        # Filter out noisy dendrite connection errors without changing global log level
        # Note: bt.logging uses its own logger; standard logging.Filter may not catch it.
        # We install both: (1) a stdlib logging Filter for modules that use logging;
        # (2) a lightweight monkey-patch on bt.logging.debug to drop matching messages.
        import logging
        import re

        class DendriteNoiseFilter(logging.Filter):
            """Filter to block noisy dendrite connection errors (stdlib logging)."""
            NOISE_PATTERNS = [
                r"ClientConnectorError.*Cannot connect to host",
                r"TimeoutError#[a-f0-9-]+:",
                r"Cannot connect to host 0\.0\.0\.0:(?:0|8091)",
            ]

            def filter(self, record):
                try:
                    msg = record.getMessage()
                except Exception:
                    return True
                for pattern in self.NOISE_PATTERNS:
                    if re.search(pattern, msg):
                        return False
                return True

        # Attach stdlib filter (harmless if unused)
        logging.getLogger("bittensor.dendrite").addFilter(DendriteNoiseFilter())

        # Optional bt.logging debug filter (covers loguru-style logger used by bittensor)
        if getattr(self.config, "logging", None) is None or \
           getattr(self.config.logging, "suppress_dendrite_noise", True):
            patterns = [
                re.compile(r"ClientConnectorError.*Cannot connect to host"),
                re.compile(r"TimeoutError#[a-f0-9-]+:"),
                re.compile(r"Cannot connect to host 0\.0\.0\.0:(?:0|8091)"),
            ]

            _orig_debug = bt.logging.debug

            def _filtered_debug(message, *args, **kwargs):
                try:
                    text = message if isinstance(message, str) else str(message)
                except Exception:
                    text = ""
                for rgx in patterns:
                    if rgx.search(text):
                        return  # swallow noisy debug line
                return _orig_debug(message, *args, **kwargs)

            # Install once per process
            if not hasattr(bt.logging, "_dendrite_noise_filter_installed"):
                bt.logging.debug = _filtered_debug
                bt.logging._dendrite_noise_filter_installed = True

        # If a gpu is required, set the device to cuda:N (e.g. cuda:0)
        self.device = self.config.neuron.device

        # Log the configuration for reference.
        bt.logging.info(self.config)

        # Build Bittensor objects
        # These are core Bittensor classes to interact with the network.
        bt.logging.info("Setting up bittensor objects.")

        # The wallet holds the cryptographic key pairs for the miner.
        # bittensor >=10 exposes this as bt.Wallet (uppercase).
        self.wallet = bt.Wallet(config=self.config)

        # Subtensor calls are not thread-safe; serialize all RPCs behind a lock
        # to mirror the stable autoppia pattern.
        self._subtensor_lock = threading.Lock()
        self._rpc_call_count = 0
        self._rpc_last_log = time.time()
        self._rpc_last_fn = None

        while True:
            try:
                bt.logging.info("Initializing subtensor and metagraph")
                self.subtensor = bt.Subtensor(config=self.config)
                self.metagraph = self.subtensor.metagraph(self.config.netuid)
                break
            except Exception as e:
                bt.logging.error(
                    "Couldn't init subtensor and metagraph with error: {}".format(e)
                )
                bt.logging.error(
                    "If you use public RPC endpoint try to move to local node"
                )
                time.sleep(5)

        bt.logging.info(f"Wallet: {self.wallet}")
        bt.logging.info(f"Subtensor: {self.subtensor}")
        bt.logging.info(f"Metagraph: {self.metagraph}")

        # Check if the miner is registered on the Bittensor network before proceeding further.
        self.check_registered()

        # Each miner gets a unique identity (UID) in the network for differentiation.
        self.uid = self.metagraph.hotkeys.index(self.wallet.hotkey.ss58_address)
        bt.logging.info(
            f"Running neuron on subnet: {self.config.netuid} with uid {self.uid} using network: {self.subtensor.chain_endpoint}"
        )
        self.step = 0
        self.last_update = 0

    @abstractmethod
    async def forward(self, synapse: bt.Synapse) -> bt.Synapse: ...

    @abstractmethod
    def run(self): ...

    @abstractmethod
    def resync_metagraph(self):
        """
        Abstract method that forces subclasses to implement resync_metagraph.
        This ensures that all subclasses define their own way of resynchronizing
        the metagraph.
        """
        pass

    @abstractmethod
    def set_weights(self):

        pass

    def sync(self):
        """
        Wrapper for synchronizing the state of the network for the given miner or validator.
        """
        # Ensure miner or validator hotkey is still registered on the network.
        self.check_registered()

        try:
            if self.should_sync_metagraph():
                self.last_update = self.block
                self.resync_metagraph()

            if self.should_set_weights():
                self.set_weights()

            # Always save state.
            self.save_state()
        except Exception as e:
            bt.logging.error(
                "Coundn't sync metagraph or set weights: {}".format(
                    traceback.format_exc()
                )
            )
            bt.logging.error("If you use public RPC endpoint try to move to local node")
            time.sleep(5)

    def check_registered(self):
        """
        Check registration status once, then back off to avoid hammering RPC.
        """
        now = time.time()
        last_check = getattr(self, "_last_reg_check", None)
        # Always allow the very first check; afterwards wait at least 60s.
        if last_check is not None and now - last_check < 60:
            return

        self._last_reg_check = now

        try:
            registered = self._with_subtensor(
                self.subtensor.is_hotkey_registered,
                netuid=self.config.netuid,
                hotkey_ss58=self.wallet.hotkey.ss58_address,
            )
        except Exception as e:
            bt.logging.error(f"Hotkey registration check failed: {e}")
            return

        if not registered:
            bt.logging.error(
                f"Wallet: {self.wallet} is not registered on netuid {self.config.netuid}."
                f" Please register the hotkey using `btcli subnets register` before trying again"
            )
            return

    # Subtensor RPC serialization helper
    def _with_subtensor(self, fn, *args, **kwargs):
        start = time.time()
        with self._subtensor_lock:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                bt.logging.error(
                    f"[RPC] {getattr(fn, '__name__', str(fn))} failed after "
                    f"{time.time() - start:.2f}s (call #{self._rpc_call_count}): {exc}"
                )
                raise
            finally:
                self._rpc_call_count += 1
                self._rpc_last_fn = getattr(fn, "__name__", str(fn))
                elapsed = time.time() - start
                # Log occasionally to trace RPC usage without spamming.
                if elapsed > 2:
                    bt.logging.debug(
                        f"[RPC] {self._rpc_last_fn} took {elapsed:.2f}s "
                        f"(call #{self._rpc_call_count})"
                    )
                now = time.time()
                if now - self._rpc_last_log > 60:
                    bt.logging.debug(
                        f"[RPC] total calls: {self._rpc_call_count} "
                        f"last fn: {self._rpc_last_fn} (last {elapsed:.2f}s)"
                    )
                    self._rpc_last_log = now

    def should_sync_metagraph(self):
        """
        Check if enough epoch blocks have elapsed since the last checkpoint to sync.

        """
        if getattr(self, "_is_miner_role", False):
            last_update = self.last_update
        else:
            last_update = self.metagraph.last_update[self.uid]

        return (self.block - last_update) > self.config.neuron.epoch_length

    def should_set_weights(self) -> bool:
        # Don't set weights on initialization.
        if self.step == 0:
            return False

        # Check if enough epoch blocks have elapsed since the last epoch.
        if self.config.neuron.disable_set_weights:
            return False

        # Define appropriate logic for when set weights.
        return (
            self.block - self.metagraph.last_update[self.uid]
        ) > self.config.neuron.epoch_length and not getattr(self, "_is_miner_role", False)  # don't set weights if you're a miner

    def save_state(self):
        bt.logging.trace(
            "save_state() not implemented for this neuron. You can implement this function to save model checkpoints or other useful data."
        )

    def load_state(self):
        bt.logging.trace(
            "load_state() not implemented for this neuron. You can implement this function to load model checkpoints or other useful data."
        )

    def parse_versions(self):
        """
        Populate version fields from packaged constants.

        The previous implementation attempted to fetch remote sources via an
        undefined `version_url`, which crashed local runs and depended on
        network access. For local/PM2 deployments we rely on the baked-in
        module versions instead.
        """
        self.version = __version__
        self.least_acceptable_version = __least_acceptable_version__
        bt.logging.info(f"Version: {self.version}, Minimum: {self.least_acceptable_version}")
        return
