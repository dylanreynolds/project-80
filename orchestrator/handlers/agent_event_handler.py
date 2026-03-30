"""
Processes completion/failure events sent back from local desktop agents.

On install_complete:
  - Update ServiceNow ticket (close it)
  - Notify user in Teams

On install_failed:
  - Update ServiceNow ticket with error detail
  - Notify helpdesk (optional — could open a child incident)
"""
import logging

import requests

from clients.agent_bus_client import AgentEvent
from clients.servicenow_client import ServiceNowClient
from config import OrchestratorConfig

logger = logging.getLogger(__name__)


class AgentEventHandler:
    def __init__(self, snow: ServiceNowClient, config: OrchestratorConfig):
        self._snow = snow
        self._cfg = config

    def handle(self, event: AgentEvent) -> None:
        logger.info(
            "Agent event '%s' received for ticket %s on device %s",
            event.event_type,
            event.ticket_number,
            event.device_id,
        )

        if event.event_type == "install_complete":
            self._snow.add_work_note(
                event.ticket_sys_id,
                f"[Local Agent] ✅ {event.software_name} installed successfully on {event.device_id}.",
                close=True,
            )
            self._notify_teams(event, "install_complete")

        elif event.event_type == "upgraded":
            self._snow.add_work_note(
                event.ticket_sys_id,
                f"[Local Agent] ⬆️ {event.software_name} was already installed — upgraded to latest version on {event.device_id}.",
                close=True,
            )
            self._notify_teams(event, "install_complete")   # user-facing message is the same

        elif event.event_type == "already_installed":
            self._snow.add_work_note(
                event.ticket_sys_id,
                f"[Local Agent] ℹ️ {event.software_name} is already installed and up to date on {event.device_id}.",
                close=True,
            )
            self._notify_teams(event, "install_complete")

        elif event.event_type == "install_failed":
            self._snow.add_work_note(
                event.ticket_sys_id,
                f"[Local Agent] ❌ Installation failed on {event.device_id}. Detail: {event.detail}\n\n"
                "Please assign to L2 for manual remediation.",
            )
            # Don't send install_complete to user — leave ticket open for helpdesk
            logger.error("Install failed for %s on %s: %s", event.software_name, event.device_id, event.detail)

        else:
            logger.warning("Unknown agent event type: %s", event.event_type)

    def _notify_teams(self, event: AgentEvent, event_type: str) -> None:
        if not event.teams_conversation_ref:
            return
        try:
            requests.post(
                f"{self._cfg.TEAMS_BOT_URL}/api/proactive",
                headers={
                    "X-API-Key": self._cfg.ORCHESTRATOR_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "conversation_ref": event.teams_conversation_ref,
                    "event_type": event_type,
                    "payload": {
                        "software_name": event.software_name,
                        "ticket_number": event.ticket_number,
                    },
                },
                timeout=10,
            )
        except Exception as exc:
            logger.error("Failed to send proactive Teams message: %s", exc)
