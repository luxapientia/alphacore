# The MIT License (MIT)
# (c) 2023 Yuma Rao — modified for Autoppia Web Agents Subnet

import time
import asyncio
import threading
import traceback
import typing
import os

import bittensor as bt

from subnet.base.neuron import BaseNeuron
from subnet.protocol import (
    TaskSynapse,
    TaskFeedbackSynapse,
    StartRoundSynapse,
    TaskCleanupSynapse,
)


class BaseMinerNeuron(BaseNeuron):
    """
    Base class for Bittensor miners in the Autoppia Web Agents subnet.

    Exposes three endpoints:
      - forward(TaskSynapse)            → miners return actions to solve the task
      - forward_feedback(TaskFeedbackSynapse) → validator sends per-task feedback
      - forward_start_round(StartRoundSynapse) → round handshake / miner metadata
      - forward_cleanup(TaskCleanupSynapse) → post-validation cleanup hook
    """

    neuron_type: str = "MinerNeuron"

    def __init__(self, config=None):
        super().__init__(config=config)

        # Warn if allowing incoming requests from anyone.
        if not self.config.blacklist.force_validator_permit:
            bt.logging.warning(
                "You are allowing non-validators to send requests to your miner. This is a security risk."
            )
        if self.config.blacklist.allow_non_registered:
            bt.logging.warning(
                "You are allowing non-registered entities to send requests to your miner. This is a security risk."
            )

        # The axon handles request processing, allowing validators to send this miner requests.
        self.axon = bt.Axon(
            wallet=self.wallet,
            config=self.config() if callable(self.config) else self.config,
        )

        # Attach RPCs
        bt.logging.info("Attaching forward function to miner axon.")
        self.axon.attach(
            forward_fn=self.forward,
            blacklist_fn=self.blacklist,
            priority_fn=self.priority,
        )
        self.axon.attach(
            forward_fn=self.forward_feedback,
            blacklist_fn=self.blacklist_feedback,
            priority_fn=self.priority_feedback,
        )
        self.axon.attach(
            forward_fn=self.forward_start_round,
            blacklist_fn=self.blacklist_start_round,
            priority_fn=self.priority_start_round,
        )
        self.axon.attach(
            forward_fn=self.forward_cleanup,
            blacklist_fn=self.blacklist_cleanup,
            priority_fn=self.priority_cleanup,
        )
        bt.logging.info(f"Axon created: {self.axon}")

        # Runtime flags / threading
        self.should_exit: bool = False
        self.is_running: bool = False
        self.thread: typing.Optional[threading.Thread] = None
        self.lock = asyncio.Lock()

    # ─────────────────────────── Runner ───────────────────────────

    def run(self):
        """
        Main loop:
          1) Ensure registration / sync.
          2) Serve & start axon.
          3) Epoch loop: sync metagraph periodically.
        """
        self.sync()

        bt.logging.info(
            f"Serving miner axon {self.axon} on network: {self.config.subtensor.chain_endpoint} with netuid: {self.config.netuid}"
        )

        try:
            resp = self.subtensor.serve_axon(
                netuid=self.config.netuid,
                axon=self.axon,
            )
            if hasattr(resp, "success") and resp.success is False:
                bt.logging.warning(f"serve_axon failed: {getattr(resp, 'message', 'unknown error')}")
        except Exception as e:
            bt.logging.error(f"Failed to serve axon: {e}")

        bt.logging.info("🔧 Axon configured, about to start listening...")
        self.axon.start()
        bt.logging.success(f"✅ Miner axon STARTED and LISTENING at block: {self.block}")
        bt.logging.success(f"✅ Miner IP: {self.axon.external_ip}:{self.axon.external_port}")
        bt.logging.success(f"✅ Miner hotkey: {self.wallet.hotkey.ss58_address}")

        # Periodic heartbeat so operators can confirm the miner is still alive even
        # when epochs are long and there are no incoming tasks.
        heartbeat_s = 0.0
        try:
            heartbeat_s = float(os.getenv("ALPHACORE_MINER_HEARTBEAT_SECONDS", "60") or "60")
        except Exception:
            heartbeat_s = 60.0
        heartbeat_s = max(0.0, float(heartbeat_s))
        last_heartbeat_at = 0.0

        # Best-effort tempo lookup (blocks per epoch) for progress logs.
        tempo = 360
        try:
            if hasattr(self.subtensor, "tempo") and callable(self.subtensor.tempo):
                tempo = int(self._with_subtensor(self.subtensor.tempo, netuid=self.config.netuid))
        except Exception:
            tempo = 360
        tempo = max(1, int(tempo))

        def _maybe_heartbeat(reason: str) -> None:
            nonlocal last_heartbeat_at
            if heartbeat_s <= 0:
                return
            now = time.time()
            if (now - last_heartbeat_at) < heartbeat_s:
                return
            last_heartbeat_at = now
            try:
                block = int(self.block)
                epoch = block // tempo
                blocks_into_epoch = block % tempo
                progress = float(blocks_into_epoch) / float(tempo)
                tasks_handled = int(getattr(self, "_tasks_handled", 0) or 0)
                last_task_at = getattr(self, "_last_task_at", None)
                last_task_id = getattr(self, "_last_task_id", None)
                last_task_age_s = None
                try:
                    if isinstance(last_task_at, (int, float)) and last_task_at > 0:
                        last_task_age_s = float(now - float(last_task_at))
                except Exception:
                    last_task_age_s = None
                # NOTE: bittensor's bt.logging prefixes messages with the local UID/hotkey.
                # Avoid %-format args to prevent bt.logging from stripping args and breaking formatting.
                hotkey = str(self.wallet.hotkey.ss58_address)
                ax_ip = str(getattr(self.axon, "external_ip", None))
                ax_port = str(getattr(self.axon, "external_port", None))
                age_str = f"{last_task_age_s:.1f}" if isinstance(last_task_age_s, float) else "null"
                last_id_str = str(last_task_id) if last_task_id else "null"
                bt.logging.info(
                    f"MINER_ALIVE uid={int(self.uid)} hotkey={hotkey} "
                    f"block={int(block)} epoch={int(epoch)} progress={float(progress):.3f} "
                    f"axon={ax_ip}:{ax_port} tasks={int(tasks_handled)} "
                    f"last_task_age_s={age_str} last_task_id={last_id_str} reason={str(reason)}"
                )
            except Exception:
                return

        _maybe_heartbeat("axon_started")

        try:
            while not self.should_exit:
                # Use local `self.last_update` rather than `metagraph.last_update`.
                # Miner metagraph `last_update` can remain stale (e.g., if axon serving fails),
                # causing a tight loop and log spam.
                while (self.block - self.last_update) < self.config.neuron.epoch_length:
                    time.sleep(1)
                    _maybe_heartbeat("waiting_for_sync")
                    if self.should_exit:
                        break
                self.sync()
                _maybe_heartbeat("synced")
                self.step += 1

        except KeyboardInterrupt:
            self.axon.stop()
            bt.logging.success("Miner killed by keyboard interrupt.")
            exit()

        except Exception:
            bt.logging.error(traceback.format_exc())

    def run_in_background_thread(self):
        if not self.is_running:
            bt.logging.debug("Starting miner in background thread.")
            self.should_exit = False
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
            self.is_running = True
            bt.logging.debug("Started")

    def stop_run_thread(self):
        if self.is_running:
            bt.logging.debug("Stopping miner in background thread.")
            self.should_exit = True
            if self.thread is not None:
                self.thread.join(5)
            self.is_running = False
            bt.logging.debug("Stopped")

    def __enter__(self):
        self.run_in_background_thread()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop_run_thread()

    def resync_metagraph(self):
        """Resyncs the metagraph and updates the hotkeys and moving averages based on the new metagraph."""
        bt.logging.debug("resync_metagraph()")
        self._with_subtensor(self.metagraph.sync, subtensor=self.subtensor)

    # Overriding the abstract method from BaseNeuron to avoid instantiation error
    def set_weights(self):
        pass

    # ─────────────────────── Blacklists ───────────────────────

    async def blacklist(self, synapse: TaskSynapse) -> typing.Tuple[bool, str]:
        return await self._common_blacklist(synapse)

    async def blacklist_feedback(
        self, synapse: TaskFeedbackSynapse
    ) -> typing.Tuple[bool, str]:
        return await self._common_blacklist(synapse)

    async def blacklist_start_round(
        self, synapse: StartRoundSynapse
    ) -> typing.Tuple[bool, str]:
        bt.logging.info(" blacklist_start_round called - synapse received!")
        result = await self._common_blacklist(synapse)
        bt.logging.info(f" blacklist_start_round result: blacklisted={result[0]}, reason='{result[1]}'")
        return result

    async def blacklist_cleanup(
        self, synapse: TaskCleanupSynapse
    ) -> typing.Tuple[bool, str]:
        return await self._common_blacklist(synapse)

    async def _common_blacklist(
        self,
        synapse: typing.Union[
            TaskSynapse,
            TaskFeedbackSynapse,
            StartRoundSynapse,
            TaskCleanupSynapse,
        ],
    ) -> typing.Tuple[bool, str]:
        """
        Shared blacklist logic used by forward, feedback, and start_round.
        Returns a tuple: (bool, str).
        """
        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            bt.logging.warning("Received a request without a dendrite or hotkey.")
            return True, "Missing dendrite or hotkey"

        validator_hotkey = synapse.dendrite.hotkey

        # Ensure hotkey is recognized.
        if (
            not self.config.blacklist.allow_non_registered
            and validator_hotkey not in self.metagraph.hotkeys
        ):
            bt.logging.warning(f"Unrecognized hotkey: {validator_hotkey}")
            return True, f"Unrecognized hotkey: {validator_hotkey}"

        uid = self.metagraph.hotkeys.index(validator_hotkey)

        # Optionally force only validators
        if self.config.blacklist.force_validator_permit:
            if not self.metagraph.validator_permit[uid]:
                bt.logging.warning(f"Blacklisted Non-Validator {validator_hotkey}")
                return True, f"Non-validator hotkey: {validator_hotkey}"

        # Check minimum stake
        stake = self.metagraph.S[uid]
        min_stake = self.config.blacklist.minimum_stake_requirement
        if stake < min_stake:
            bt.logging.warning(f"Blacklisted insufficient stake: {validator_hotkey}")
            return (
                True,
                f"Insufficient stake ({stake} < {min_stake}) for {validator_hotkey}",
            )

        return False, f"Hotkey recognized: {validator_hotkey}"

    # ─────────────────────── Priority ───────────────────────

    async def priority(self, synapse: TaskSynapse) -> float:
        return await self._common_priority(synapse)

    async def priority_feedback(self, synapse: TaskFeedbackSynapse) -> float:
        return await self._common_priority(synapse)

    async def priority_cleanup(self, synapse: TaskCleanupSynapse) -> float:
        return await self._common_priority(synapse)

    # ─────────────────────── Cleanup endpoint ───────────────────────

    async def forward_cleanup(self, synapse: TaskCleanupSynapse) -> TaskCleanupSynapse:
        """Default cleanup handler; subclasses may override with real cleanup."""
        synapse.acknowledged = True
        synapse.cleanup_ok = True
        synapse.error_message = None
        return synapse

    async def priority_start_round(self, synapse: StartRoundSynapse) -> float:
        bt.logging.info(" priority_start_round called - calculating priority")
        priority = await self._common_priority(synapse)
        bt.logging.info(f" priority_start_round result: priority={priority}")
        return priority

    async def _common_priority(
        self,
        synapse: typing.Union[TaskSynapse, TaskFeedbackSynapse, StartRoundSynapse],
    ) -> float:
        """
        Shared priority logic used by forward, feedback, and start_round.
        Returns a float indicating the priority value.
        """
        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            bt.logging.warning("Received a request without a dendrite or hotkey.")
            return 0.0

        validator_hotkey = synapse.dendrite.hotkey
        if validator_hotkey not in self.metagraph.hotkeys:
            return 0.0

        caller_uid = self.metagraph.hotkeys.index(validator_hotkey)
        return float(self.metagraph.S[caller_uid])
