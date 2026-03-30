"""
HTTP Poller — replaces Azure Service Bus (BusListener / BusSender) for
Gilligan's Island demo mode.

The agent polls the orchestrator's GET /jobs/pending?device_id=xxx endpoint
every POLL_INTERVAL_SECONDS.  When a job arrives it is passed to on_command,
exactly as BusListener does.  Results are sent via HTTPSender.send_event()
which POSTs to POST /jobs/{job_id}/result.

Security: the orchestrator still signs every command with HMAC-SHA256.
The agent still verifies the signature via CommandVerifier before executing
any step.  The security model is unchanged; only the transport changes from
Service Bus to plain HTTP.
"""
import logging
import time
from typing import Callable

import requests

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5
REQUEST_TIMEOUT = 10


class HTTPPoller:
    """
    Drop-in replacement for BusListener.
    Call start(on_command) from a background thread.
    """

    def __init__(
        self,
        orchestrator_url: str,
        device_id: str,
        api_key: str,
    ):
        self._url = orchestrator_url.rstrip("/")
        self._device_id = device_id
        self._api_key = api_key
        self._running = False

    def start(self, on_command: Callable[[dict], None]) -> None:
        """Blocking poll loop.  Call from a dedicated thread."""
        self._running = True
        logger.info(
            "HTTPPoller starting — orchestrator=%s device=%s",
            self._url,
            self._device_id,
        )
        while self._running:
            try:
                job = self._poll_once()
                if job:
                    on_command(job)
                else:
                    time.sleep(POLL_INTERVAL_SECONDS)
            except Exception as exc:
                logger.error("HTTPPoller error — retrying in %ds: %s", POLL_INTERVAL_SECONDS, exc)
                time.sleep(POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False

    def _poll_once(self) -> dict | None:
        resp = requests.get(
            f"{self._url}/jobs/pending",
            params={"device_id": self._device_id},
            headers={"X-API-Key": self._api_key},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 204:
            return None   # no jobs waiting
        resp.raise_for_status()
        data = resp.json()
        return data.get("job")   # None if empty payload


class HTTPSender:
    """
    Drop-in replacement for BusSender.
    Posts agent events (install_complete, install_failed, etc.) back to the
    orchestrator via HTTP instead of Azure Service Bus.
    """

    def __init__(self, orchestrator_url: str, api_key: str):
        self._url = orchestrator_url.rstrip("/")
        self._api_key = api_key

    def send_event(self, event: dict) -> None:
        job_id = event.get("command_id", "unknown")
        try:
            resp = requests.post(
                f"{self._url}/jobs/{job_id}/result",
                headers={
                    "X-API-Key": self._api_key,
                    "Content-Type": "application/json",
                },
                json=event,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            logger.info(
                "Event '%s' sent to orchestrator for job %s (ticket %s)",
                event.get("event_type"),
                job_id,
                event.get("ticket_number"),
            )
        except Exception as exc:
            logger.error("Failed to send event for job %s: %s", job_id, exc)
