"""
Validation API client for scoring miner submissions.

This module provides a client to communicate with the external Validation API
for scoring task submissions. The API runs as a separate service that validates
miner responses in a sandboxed environment.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import bittensor as bt

# Try to import httpx, fallback to urllib
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    import urllib.request
    import urllib.error

logger = logging.getLogger(__name__)


class ValidationSubmitResponse:
    """Represents a validation API response."""

    def __init__(self, response_dict: Dict[str, Any]):
        """Initialize from API response dictionary."""
        self.job_id: str = response_dict.get("job_id", "")
        self.task_id: Optional[str] = response_dict.get("task_id")
        self.result: Dict[str, Any] = response_dict.get("result", {})
        self.log_url: str = response_dict.get("log_url", "")
        self.log_path: str = response_dict.get("log_path", "")
        self.submission_path: str = response_dict.get("submission_path", "")
        self.tap: Optional[str] = response_dict.get("tap")  # Test Access Point

        # Extract score from result
        self.status: str = str(self.result.get("status", "fail")).lower()
        self.score: float = float(self.result.get("score", 0.0))
        self.message: str = str(self.result.get("msg", ""))

    def __repr__(self) -> str:
        return (
            f"ValidationSubmitResponse("
            f"job_id={self.job_id}, "
            f"task_id={self.task_id}, "
            f"status={self.status}, "
            f"score={self.score})"
        )


class ValidationAPIClient:
    """Client for interacting with the Validation API service."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        timeout: int = 300,  # seconds
        max_retries: int = 2,
    ):
        """
        Initialize the Validation API client.

        Args:
            endpoint: Base URL of the validation API (e.g., http://127.0.0.1:8888)
            timeout: Request timeout in seconds (default: 300 = 5 minutes)
            max_retries: Maximum number of retries on failure (default: 2)
        """
        self.endpoint = endpoint or "http://127.0.0.1:8888"
        self.timeout = timeout
        self.max_retries = max_retries
        self.session: Optional[httpx.AsyncClient] = None if HAS_HTTPX else None

    async def __aenter__(self) -> ValidationAPIClient:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.disconnect()

    async def connect(self) -> None:
        """Create an HTTP session."""
        if HAS_HTTPX and self.session is None:
            self.session = httpx.AsyncClient(timeout=self.timeout)
            logger.debug(f"Connected to validation API at {self.endpoint}")
        elif not HAS_HTTPX:
            logger.debug(f"Using urllib for validation API calls to {self.endpoint}")

    async def disconnect(self) -> None:
        """Close the HTTP session."""
        if HAS_HTTPX and self.session:
            await self.session.aclose()
            self.session = None
            logger.debug("Disconnected from validation API")

    async def health_check(self) -> bool:
        """
        Check if the validation API is healthy and ready.

        Returns:
            True if API is healthy, False otherwise
        """
        try:
            if HAS_HTTPX:
                if self.session is None:
                    await self.connect()
                response = await self.session.get(
                    f"{self.endpoint}/health", timeout=10  # seconds
                )
                if response.status_code == 200:
                    # httpx.Response.json() is synchronous.
                    data = response.json()
                    sandbox_ready = data.get("sandbox_ready", False)
                    token_ready = data.get("token_ready", False)
                    if sandbox_ready and token_ready:
                        logger.debug("Validation API health check: OK")
                        return True
                    logger.warning(
                        f"Validation API not fully ready: "
                        f"sandbox_ready={sandbox_ready}, token_ready={token_ready}"
                    )
                    return False
                logger.warning(f"Validation API health check failed: {response.status_code}")
                return False
            else:
                # Fallback with urllib
                req = urllib.request.Request(f"{self.endpoint}/health")
                try:
                    status, body, _headers = await self._urllib_fetch(req, timeout=10)  # seconds
                    if status == 200:
                        data = json.loads(body.decode())
                        sandbox_ready = data.get("sandbox_ready", False)
                        token_ready = data.get("token_ready", False)
                        if sandbox_ready and token_ready:
                            logger.debug("Validation API health check: OK")
                            return True
                        logger.warning(
                            f"Validation API not fully ready: "
                            f"sandbox_ready={sandbox_ready}, token_ready={token_ready}"
                        )
                        return False
                except Exception as e:
                    logger.warning(f"Validation API health check error: {e}")
                    return False

        except Exception as e:
            logger.error(f"Validation API health check error: {e}")
            return False

    async def submit_validation(
        self,
        workspace_zip_path: str,
        task_json: Dict[str, Any],
        task_id: Optional[str] = None,
        timeout_s: int = 120,  # seconds
        net_checks: bool = False,
        stream_log: bool = False,
    ) -> Optional[ValidationSubmitResponse]:
        """
        Submit a validation job to the API.

        Args:
            workspace_zip_path: Path to the workspace zip file containing miner submission
            task_json: The task specification as a dictionary
            task_id: Optional task ID for tracking
            timeout_s: Validation timeout in seconds (default: 120)
            net_checks: Enable network checks during validation
            stream_log: Stream logs during validation

        Returns:
            ValidationSubmitResponse if successful, None if failed
        """
        # Validate the zip file exists
        zip_path = Path(workspace_zip_path)
        if not zip_path.exists():
            logger.error(f"Workspace zip file not found: {workspace_zip_path}")
            return None

        request_payload = {
            "workspace_zip_path": str(workspace_zip_path),
            "task_json": task_json,
            "timeout_s": max(1, int(timeout_s)),
            "net_checks": bool(net_checks),
            "stream_log": bool(stream_log),
            "quiet_kernel": True,
        }

        for attempt in range(self.max_retries + 1):
            try:
                logger.info(
                    f"Submitting validation job (attempt {attempt + 1}/{self.max_retries + 1}): "
                    f"task_id={task_id}, zip={zip_path.name}"
                )

                if HAS_HTTPX:
                    return await self._submit_with_httpx(request_payload, attempt)
                else:
                    return await self._submit_with_urllib(request_payload, attempt)

            except Exception as e:
                logger.error(f"Unexpected error during validation submission: {e}", exc_info=True)
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    continue
                return None

        logger.error(f"Validation submission failed after {self.max_retries + 1} attempts")
        return None

    async def _submit_with_httpx(
        self, request_payload: Dict[str, Any], attempt: int
    ) -> Optional[ValidationSubmitResponse]:
        """Submit validation using httpx."""
        if self.session is None:
            await self.connect()

        timeout = httpx.Timeout(self.timeout)  # seconds
        try:
            response = await self.session.post(
                f"{self.endpoint}/validate",
                json=request_payload,
                timeout=timeout,
            )

            if response.status_code == 200:
                response_data = response.json()
                validation_response = ValidationSubmitResponse(response_data)
                logger.info(f"Validation job submitted successfully: {validation_response}")
                return validation_response
            elif response.status_code == 429:
                # Queue full, retry after delay
                retry_after = int(response.headers.get("Retry-After", 1))
                logger.warning(f"Validation API queue full. Retrying in {retry_after}s...")
                if attempt < self.max_retries:
                    await asyncio.sleep(retry_after)
                    return None
                return None
            elif response.status_code == 503:
                logger.warning(f"Validation API unavailable (status 503). Retrying...")
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                    return None
                return None
            else:
                logger.error(f"Validation API error (status {response.status_code}): {response.text}")
                return None

        except httpx.TimeoutException:
            logger.error(f"Validation API request timeout (attempt {attempt + 1})")
            if attempt < self.max_retries:
                await asyncio.sleep(2 ** attempt)
                return None
            return None
        except httpx.RequestError as e:
            logger.error(f"Validation API client error (attempt {attempt + 1}): {e}")
            if attempt < self.max_retries:
                await asyncio.sleep(2 ** attempt)
                return None
            return None

    async def _submit_with_urllib(
        self, request_payload: Dict[str, Any], attempt: int
    ) -> Optional[ValidationSubmitResponse]:
        """Submit validation using urllib (run in a background thread to avoid blocking the event loop)."""
        try:
            req = urllib.request.Request(
                f"{self.endpoint}/validate",
                data=json.dumps(request_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            status, body, _headers = await self._urllib_fetch(req, timeout=self.timeout)  # seconds
            if status == 200:
                response_data = json.loads(body.decode())
                validation_response = ValidationSubmitResponse(response_data)
                logger.info(f"Validation job submitted successfully: {validation_response}")
                return validation_response
            logger.error(f"Validation API error (status {status})")
            return None

        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = 1
                try:
                    retry_after = int(getattr(e, "headers", {}).get("Retry-After", 1))
                except Exception:
                    retry_after = 1
                logger.warning(f"Validation API queue full. Retrying in {retry_after}s...")
                if attempt < self.max_retries:
                    await asyncio.sleep(retry_after)
                    return None
            elif e.code == 503:
                logger.warning(f"Validation API unavailable. Retrying...")
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                    return None
            else:
                logger.error(f"Validation API error (status {e.code})")
            return None
        except Exception as e:
            logger.error(f"Validation API client error: {e}")
            if attempt < self.max_retries:
                await asyncio.sleep(2 ** attempt)
                return None
            return None

    async def get_validation_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the status of a validation job.

        Args:
            job_id: The job ID returned from submit_validation

        Returns:
            Status dict if found, None otherwise
        """
        try:
            if HAS_HTTPX:
                if self.session is None:
                    await self.connect()
                response = await self.session.get(
                    f"{self.endpoint}/validate/{job_id}", timeout=10  # seconds
                )
                if response.status_code == 200:
                    return response.json()
            else:
                req = urllib.request.Request(f"{self.endpoint}/validate/{job_id}")
                status, body, _headers = await self._urllib_fetch(req, timeout=10)  # seconds
                if status == 200:
                    return json.loads(body.decode())

            logger.warning(f"Failed to get validation status for job {job_id}")
            return None
        except Exception as e:
            logger.error(f"Error getting validation status: {e}")
            return None

    async def get_validation_log(
        self, job_id: str, tail: int = 200
    ) -> Optional[str]:
        """
        Get the validation job log.

        Args:
            job_id: The job ID
            tail: Number of lines to retrieve from the end (default: 200)

        Returns:
            Log content if found, None otherwise
        """
        try:
            if HAS_HTTPX:
                if self.session is None:
                    await self.connect()
                response = await self.session.get(
                    f"{self.endpoint}/validate/{job_id}/log",
                    params={"tail": tail},
                    timeout=10,  # seconds
                )
                if response.status_code == 200:
                    return response.text
            else:
                url = f"{self.endpoint}/validate/{job_id}/log?tail={tail}"
                req = urllib.request.Request(url)
                status, body, _headers = await self._urllib_fetch(req, timeout=10)  # seconds
                if status == 200:
                    return body.decode()

            logger.warning(f"Failed to get validation log for job {job_id}")
            return None
        except Exception as e:
            logger.error(f"Error getting validation log: {e}")
            return None

    async def _urllib_fetch(self, req: "urllib.request.Request", *, timeout: int) -> tuple[int, bytes, dict]:
        """
        Run urllib I/O in a thread to avoid blocking the asyncio event loop.

        Returns: (status_code, body_bytes, headers_dict)
        """

        def _do() -> tuple[int, bytes, dict]:
            with urllib.request.urlopen(req, timeout=timeout) as response:  # seconds
                body = response.read()
                headers = dict(getattr(response, "headers", {}) or {})
                return int(getattr(response, "status", 0)), body, headers

        return await asyncio.to_thread(_do)


class ValidationAPIClientPool:
    """
    Pool of validation API clients for concurrent requests.

    This is useful when the validator needs to submit multiple validation
    jobs in parallel to the same API endpoint.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        timeout: int = 300,  # seconds
        max_retries: int = 2,
        pool_size: int = 4,
    ):
        """
        Initialize the client pool.

        Args:
            endpoint: Base URL of the validation API
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries
            pool_size: Number of concurrent clients to maintain
        """
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_retries = max_retries
        self.pool_size = max(1, pool_size)
        self.clients: list[ValidationAPIClient] = []
        self._available: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> ValidationAPIClientPool:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.stop()

    async def start(self) -> None:
        """Initialize the pool of clients."""
        for _ in range(self.pool_size):
            client = ValidationAPIClient(
                endpoint=self.endpoint,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )
            await client.connect()
            self.clients.append(client)
            self._available.put_nowait(client)
        logger.info(f"Validation API client pool started with {self.pool_size} clients")

    async def stop(self) -> None:
        """Stop all clients in the pool."""
        for client in self.clients:
            await client.disconnect()
        self.clients.clear()
        logger.info("Validation API client pool stopped")

    async def submit_validation(
        self,
        workspace_zip_path: str,
        task_json: Dict[str, Any],
        task_id: Optional[str] = None,
        timeout_s: int = 120,  # seconds
        net_checks: bool = False,
        stream_log: bool = False,
    ) -> Optional[ValidationSubmitResponse]:
        """
        Submit validation job using an available client from the pool.

        Args:
            workspace_zip_path: Path to workspace zip
            task_json: Task specification
            task_id: Optional task ID for tracking
            timeout_s: Validation timeout in seconds
            net_checks: Enable network checks
            stream_log: Stream logs

        Returns:
            ValidationSubmitResponse if successful, None otherwise
        """
        if not self.clients:
            logger.error("Validation API client pool not initialized")
            return None

        client = await self._available.get()
        try:
            return await client.submit_validation(
                workspace_zip_path=workspace_zip_path,
                task_json=task_json,
                task_id=task_id,
                timeout_s=timeout_s,
                net_checks=net_checks,
                stream_log=stream_log,
            )
        finally:
            self._available.put_nowait(client)
