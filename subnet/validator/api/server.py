"""
FastAPI endpoint for independent task generation.

Allows external systems to request tasks without coupling to the validator loop.
Runs on separate port (default 8000) and can operate independently.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from uvicorn import run as uvicorn_run

import os
import bittensor as bt

from modules.generation import TaskGenerationPipeline
from modules.models import ACTaskSpec
from subnet.validator.config import HTTP_HOST, HTTP_PORT


# ================================================================== #
# REQUEST/RESPONSE MODELS
# ================================================================== #


class TaskGenerationRequest(BaseModel):
    """Request model for task generation."""

    count: int = 1
    """Number of tasks to generate"""
    include_details: bool = True
    """Include full task details in response"""


class TaskGenerationResponse(BaseModel):
    """Response model for generated tasks."""

    success: bool
    message: str
    tasks: List[dict]
    count: int


class HealthCheckResponse(BaseModel):
    """Health check response."""

    status: str
    pipeline_ready: bool
    timestamp: str


# ================================================================== #
# FASTAPI APPLICATION
# ================================================================== #


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title="AlphaCore Task Generation API",
        description="Generate tasks for AlphaCore validators",
        version="1.0.0",
    )

    # Global pipeline instance
    _pipeline_cache: dict = {"pipeline": None}

    # ================================================================ #
    # ENDPOINTS
    # ================================================================ #

    @app.on_event("startup")
    async def startup_event():
        """Initialize pipeline on startup."""
        try:
            validator_sa = (os.getenv("ALPHACORE_VALIDATOR_SA") or "").strip()
            env_profile = (os.getenv("ALPHACORE_ENV") or "local").strip().lower()
            if not validator_sa:
                if env_profile != "local":
                    raise RuntimeError("ALPHACORE_VALIDATOR_SA is required for task generation API.")
                validator_sa = "task-api@alphacore.local"
            if env_profile != "local" and validator_sa.lower() == "your-validator-sa@project.iam.gserviceaccount.com":
                raise RuntimeError("ALPHACORE_VALIDATOR_SA is a placeholder; set a real service account email.")
            _pipeline_cache["pipeline"] = TaskGenerationPipeline(
                validator_sa=validator_sa
            )
            bt.logging.info(f"✓ Task generation API ready (validator_sa={validator_sa})")
        except Exception as e:
            bt.logging.error(f"✗ Failed to initialize pipeline: {e}")

    @app.get("/health", response_model=HealthCheckResponse)
    async def health_check() -> HealthCheckResponse:
        """Check API health and pipeline status."""
        from datetime import datetime

        return HealthCheckResponse(
            status="healthy",
            pipeline_ready=_pipeline_cache["pipeline"] is not None,
            timestamp=datetime.utcnow().isoformat(),
        )

    @app.post("/generate-tasks", response_model=TaskGenerationResponse)
    async def generate_tasks(request: TaskGenerationRequest) -> TaskGenerationResponse:
        """
        Generate tasks for validator.

        Args:
            request: TaskGenerationRequest with count and options

        Returns:
            TaskGenerationResponse with generated tasks

        Raises:
            HTTPException: If task generation fails
        """
        if _pipeline_cache["pipeline"] is None:
            raise HTTPException(status_code=503, detail="Pipeline not initialized")

        if request.count < 1 or request.count > 100:
            raise HTTPException(status_code=400, detail="Count must be between 1 and 100")

        try:
            tasks: List[ACTaskSpec] = []

            for i in range(request.count):
                task = _pipeline_cache["pipeline"].generate()
                tasks.append(task)

            task_dicts = [
                asdict(task) if request.include_details else {"task_id": task.task_id}
                for task in tasks
            ]

            return TaskGenerationResponse(
                success=True,
                message=f"Generated {len(tasks)} tasks",
                tasks=task_dicts,
                count=len(tasks),
            )

        except Exception as e:
            bt.logging.error(f"✗ Task generation failed: {e}")
            raise HTTPException(status_code=500, detail=f"Task generation failed: {str(e)}")

    @app.get("/generate-task")
    async def generate_single_task() -> dict:
        """
        Generate a single task.

        Returns:
            Single ACTaskSpec as dictionary
        """
        if _pipeline_cache["pipeline"] is None:
            raise HTTPException(status_code=503, detail="Pipeline not initialized")

        try:
            task = _pipeline_cache["pipeline"].generate()
            return asdict(task)
        except Exception as e:
            bt.logging.error(f"✗ Single task generation failed: {e}")
            raise HTTPException(status_code=500, detail=f"Task generation failed: {str(e)}")

    @app.get("/tasks/batch")
    async def get_batch_tasks(batch_size: int = 5) -> TaskGenerationResponse:
        """
        Generate a batch of tasks quickly.

        Args:
            batch_size: Number of tasks to generate (max 50)

        Returns:
            TaskGenerationResponse with task batch
        """
        if batch_size < 1 or batch_size > 50:
            raise HTTPException(status_code=400, detail="batch_size must be between 1 and 50")

        request = TaskGenerationRequest(count=batch_size, include_details=True)
        return await generate_tasks(request)

    return app


# ================================================================== #
# MAIN
# ================================================================== #


def run_api(host: str = HTTP_HOST, port: int = HTTP_PORT) -> None:
    """
    Run the task generation API server.

    Args:
        host: Host to bind to (default 0.0.0.0)
        port: Port to bind to (default 8000)
    """
    app = create_app()

    bt.logging.info(f"🚀 Starting AlphaCore Task Generation API on {host}:{port}")

    uvicorn_run(
        app,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    run_api()
