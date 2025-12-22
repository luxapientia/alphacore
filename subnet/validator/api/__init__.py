"""
FastAPI task generation endpoint.

Allows external systems to request tasks without coupling to the validator loop.
"""

from .server import create_app, run_api

__all__ = ["create_app", "run_api"]
