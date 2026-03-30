"""
Gilligan's Island ServiceNow adapter — teams-bot side.

Drop-in replacement for integrations/servicenow_client.py.
Creates software request tickets in the Gilligan's Island mock server
(http://<host>:3000/api/snow/tickets) and maps the response back to the
TicketResult shape that the bot dialogs expect.

Extra fields (software_name, justification, device_id, teams_conversation_ref)
are stored locally so the orchestrator can retrieve them via the
GilliganServiceNowAdapter.register_extras() call made from the
/webhook/servicenow payload injected by demo/approve.py.
"""
import logging
from typing import Optional

import requests

from integrations.servicenow_client import TicketResult

logger = logging.getLogger(__name__)


class GilliganBotServiceNowAdapter:
    """Implements the same interface as the bot's ServiceNowClient."""

    def __init__(self, base_url: str):
        # e.g. "http://192.168.56.1:3000"
        self._base = base_url.rstrip("/")
        # ticket_number → full extras dict (needed by demo/approve.py)
        self._extras: dict[str, dict] = {}

    def create_software_request(
        self,
        requester_email: str,
        requester_name: str,
        software_name: str,
        justification: str,
        device_id: Optional[str] = None,
        teams_conversation_ref: Optional[str] = None,
    ) -> TicketResult:
        """
        Creates a ticket in Gilligan's Island and returns a TicketResult.

        Gilligan's Island expects an offboarding-style payload; we repurpose the
        'reason' field to carry the software request description so the ticket
        shows meaningful text in the dashboard.
        """
        payload = {
            # Use the first mock user as a placeholder employee — the real
            # requester identity is in the extras dict below.
            "employeeId": "usr-demo",
            "reason": f"Software Request: {software_name} — {justification[:120]}",
            "requestedBy": requester_name or requester_email,
        }

        try:
            r = requests.post(
                f"{self._base}/api/snow/tickets",
                json=payload,
                timeout=15,
            )
            r.raise_for_status()
            raw = r.json()
        except Exception as exc:
            logger.error("Gilligan's Island ticket creation failed: %s", exc)
            raise

        ticket_number = raw.get("number", raw.get("id", "RITM-UNKNOWN"))

        # Persist extras locally so demo/approve.py can include them in the
        # orchestrator webhook payload.
        self._extras[ticket_number] = {
            "software_name": software_name,
            "requester_email": requester_email,
            "device_id": device_id or "",
            "teams_conversation_ref": teams_conversation_ref or "",
            "justification": justification,
        }

        logger.info(
            "Gilligan's Island ticket %s created for %s (%s)",
            ticket_number,
            software_name,
            requester_email,
        )

        return TicketResult(
            sys_id=ticket_number,    # Gilligan's Island uses number as its ID
            number=ticket_number,
            state="new",
            approval="requested",
        )

    def get_extras(self, ticket_number: str) -> dict:
        """Returns the extras dict for a ticket (used by demo helpers)."""
        return self._extras.get(ticket_number, {})

    def list_tickets(self) -> list[dict]:
        """Returns all open tickets from Gilligan's Island — useful for demo CLI."""
        try:
            r = requests.get(f"{self._base}/api/snow/tickets", timeout=10)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else data.get("tickets", [])
        except Exception as exc:
            logger.warning("Could not list Gilligan's Island tickets: %s", exc)
            return []
