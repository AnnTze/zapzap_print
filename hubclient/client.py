"""Talks to the hub. Never raises into the caller.

The hub is strictly optional: a booth with no HUB_URL set, or one whose hub is
asleep, must print exactly as it does today. So every failure here is logged
and swallowed, and the heartbeat loop keeps its own counsel about how noisy to
be when the hub is away.

httpx rather than aiohttp on purpose — python-telegram-bot already depends on
it, so booths gain no new dependency.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from . import watermark

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 10
REQUEST_TIMEOUT = 8.0
# The hub being away is normal (asleep, venue wifi, being restarted). Log the
# transition and then stay quiet rather than filling bot.log with one warning
# every ten seconds all evening.
LOG_EVERY_N_FAILURES = 30


class HubClient:
    def __init__(self, hub_url: str, api_key: str):
        self.hub_url = hub_url.rstrip("/")
        self.api_key = api_key
        self._client: httpx.AsyncClient | None = None
        self._failures = 0
        self._was_connected = False

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_asset(self, digest: str) -> bytes | None:
        """Download a watermark PNG by hash. Returns None on any failure."""
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.hub_url}/assets/{digest}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            logger.warning("Fetching asset %s failed: %s", digest[:12], exc)
            return None

    async def heartbeat(self, payload: dict) -> dict | None:
        """Send a snapshot, return the hub's config response, or None on failure."""
        try:
            client = await self._get_client()
            resp = await client.post(
                f"{self.hub_url}/ingest/heartbeat",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if resp.status_code in (401, 403):
                self._note_failure(f"hub rejected our API key ({resp.status_code})", always=True)
                return None
            resp.raise_for_status()
            if not self._was_connected:
                logger.info("Hub connected: %s", self.hub_url)
                self._was_connected = True
            self._failures = 0
            return resp.json()
        except Exception as exc:
            self._note_failure(f"{type(exc).__name__}: {exc}")
            return None

    def _note_failure(self, detail: str, always: bool = False) -> None:
        if self._was_connected:
            logger.warning("Lost contact with hub — %s", detail)
            self._was_connected = False
            self._failures = 1
            return
        self._failures += 1
        if always or self._failures == 1 or self._failures % LOG_EVERY_N_FAILURES == 0:
            logger.warning("Hub unreachable (attempt %d) — %s", self._failures, detail)


async def heartbeat_loop(client: HubClient, build_payload,
                         watermark_path: str | None = None) -> None:
    """Forever: snapshot this booth, send it, apply whatever config came back.

    `build_payload` is a plain sync callable; it shells out to lpstat, so it
    runs in a thread to keep the bot's event loop free.
    """
    while True:
        try:
            payload = await asyncio.to_thread(build_payload)
            config = await client.heartbeat(payload)
            if config and watermark_path:
                await watermark.apply_config(
                    client, config, watermark_path=watermark_path)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A bug in payload building must never kill the loop, and must
            # never touch the print path.
            logger.exception("Heartbeat iteration failed")
        await asyncio.sleep(HEARTBEAT_SECONDS)


def from_env() -> HubClient | None:
    """Build a client from HUB_URL / PRINTER_API_KEY, or None if not configured."""
    hub_url = os.getenv("HUB_URL", "").strip()
    api_key = os.getenv("PRINTER_API_KEY", "").strip()
    if not hub_url:
        return None
    if not api_key:
        logger.warning("HUB_URL is set but PRINTER_API_KEY is not — telemetry disabled")
        return None
    return HubClient(hub_url, api_key)
