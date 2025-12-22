from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


@dataclass
class TokenStatus:
    token: Optional[str]
    expires_at: Optional[datetime]
    last_error: Optional[str]


class GcpAccessTokenManager:
    """Mint/refresh a short-lived access token from a service-account JSON key."""

    def __init__(
        self,
        *,
        creds_file: Optional[Path],
        scopes: tuple[str, ...] = DEFAULT_SCOPES,
        refresh_skew_s: int = 300,
    ) -> None:
        self._creds_file = creds_file
        self._scopes = scopes
        self._refresh_skew_s = max(30, int(refresh_skew_s))

        self._lock = asyncio.Lock()
        self._token: Optional[str] = None
        self._expires_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._task: Optional[asyncio.Task] = None

    def status(self) -> TokenStatus:
        return TokenStatus(token=self._token, expires_at=self._expires_at, last_error=self._last_error)

    async def start(self) -> None:
        if self._task is not None:
            return

        # For local debugging you can still provide a token directly (no refresh).
        env_token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
        if env_token:
            async with self._lock:
                self._token = env_token
                self._expires_at = None
                self._last_error = None
            return

        if self._creds_file is None:
            async with self._lock:
                self._last_error = "Missing ALPHACORE_GCP_CREDS_FILE and GOOGLE_OAUTH_ACCESS_TOKEN"
            return

        await self._refresh_once()
        self._task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def get_token(self) -> str:
        await self.start()
        async with self._lock:
            if self._token:
                return self._token
            err = self._last_error or "Token not available"
        raise RuntimeError(err)

    async def _refresh_once(self) -> None:
        if self._creds_file is None:
            raise RuntimeError("No creds_file configured for refresh")

        creds_path = self._creds_file
        if not creds_path.exists() or not creds_path.is_file():
            raise RuntimeError(f"Creds file not found: {creds_path}")

        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("google-auth is required to mint access tokens") from exc

        def _mint() -> tuple[str, Optional[datetime]]:
            credentials = service_account.Credentials.from_service_account_file(
                str(creds_path),
                scopes=list(self._scopes),
            )
            credentials.refresh(Request())
            token = getattr(credentials, "token", None)
            expiry = getattr(credentials, "expiry", None)
            if not token:
                raise RuntimeError("Token refresh succeeded but token is empty")
            return str(token), expiry

        token, expiry = await asyncio.to_thread(_mint)
        async with self._lock:
            self._token = token
            self._expires_at = expiry
            self._last_error = None

    async def _refresh_loop(self) -> None:
        backoff_s = 5.0
        while True:
            try:
                async with self._lock:
                    expires_at = self._expires_at

                if expires_at is None:
                    sleep_s = 1800.0
                else:
                    now = datetime.now(timezone.utc)
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    sleep_s = (expires_at - now).total_seconds() - float(self._refresh_skew_s)
                    sleep_s = max(30.0, sleep_s)

                await asyncio.sleep(sleep_s)
                await self._refresh_once()
                backoff_s = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self._lock:
                    self._last_error = str(exc)
                await asyncio.sleep(backoff_s)
                backoff_s = min(300.0, backoff_s * 2.0)

