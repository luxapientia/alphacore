#!/usr/bin/env python3
"""
Start AlphaCore validator with FastAPI endpoint simultaneously.

Starts both the validator loop and the task generation API on separate threads/processes.
ENHANCED: Includes background task dispatch loop (every 60 seconds).
"""

import asyncio
import multiprocessing
import signal
import sys
import threading
from pathlib import Path

import bittensor as bt

# Add project root to path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from subnet.bittensor_config import config as build_config
from subnet.validator.api import run_api
from subnet.validator.config import HTTP_PORT
from neurons.validator import Validator


def run_validator_api():
    """Run validator API in main process with persistent event loop."""
    bt.logging.info("🔄 Starting AlphaCore Validator API with Auto-Dispatch Loop...")
    cfg = build_config(role="validator")
    
    try:
        validator_api = Validator(config=cfg)
        # Standard blocking run loop
        validator_api.run()
    except KeyboardInterrupt:
        bt.logging.info("Validator API shutting down.")
    except Exception as e:
        bt.logging.error(f"Validator API error: {e}", exc_info=True)


def run_task_generation_api_process():
    """Run task generation API in separate process."""
    bt.logging.info(f"🚀 Starting Task Generation API on port {HTTP_PORT}...")

    try:
        run_api()
    except KeyboardInterrupt:
        bt.logging.info("Task Generation API shutting down.")
    except Exception as e:
        bt.logging.error(f"Task Generation API error: {e}")


def main():
    """Main entry point."""
    bt.logging.info("=" * 70)
    bt.logging.info("AlphaCore Validator API with Task Generation API")
    bt.logging.info("=" * 70)
    
    # Create processes
    task_api_process = multiprocessing.Process(target=run_task_generation_api_process, daemon=True)
    
    try:
        # Start task generation API process
        bt.logging.info("Starting Task Generation API process...")
        task_api_process.start()
        
        # Give API time to start
        import time
        time.sleep(2)
        
        # Run validator API in main process
        bt.logging.info("Starting Validator API loop...")
        run_validator_api()
        
    except KeyboardInterrupt:
        bt.logging.info("\nShutting down...")
        task_api_process.terminate()
        task_api_process.join(timeout=5)
        if task_api_process.is_alive():
            task_api_process.kill()
        sys.exit(0)


if __name__ == "__main__":
    main()
