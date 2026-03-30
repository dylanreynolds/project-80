"""
Gilligan's Island ServiceNow adapter — teams-bot side.

Drop-in replacement for integrations/servicenow_client.py.
Creates software request tickets in the Gilligan's Island mock server
(http://<host>:3000/api/snow/tickets) and maps the response back to the
TicketResult shape that the bot dialogs expect.

Extra fields (software_name, justification, device_id, teams_conversation_ref)
are written to EXTRAS_FILE so that demo/approve.py can include them in the
orchestrator webhook payload when the presenter approves during the demo.
"""
import json
import logging
import os
from typing import Optional

import requests

from integrations.servicenow_client import TicketResult

logger = logging.getLogger(__name__)


class GilliganBotServiceNowAdapter:
    """Implements the same interface as the bot's ServiceNowClient."""

    def __init__(
        self,
        base_url: str,
        extras_file: str = "/tmp/gilligan_ticket_extras.json",
    ):
        self._base = base_url.rstrip("/")
        self._extras_file = extras_file
        self._extras: dict[str, dict] = self._load_extras()

    # ------------------------------------------------------------------
    # ServiceNowClient interface
    # ------------------------------------------------------------------

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
        Repurposes the 'reason' field so the ticket shows meaningful text
        in the Gilligan's Island dashboard.
        """
        payload = {
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

        # Persist extras to disk so demo/approve.py (running in a separate
        # process or terminal) can read them without any shared state.
        self._extras[ticket_number] = {
            "software_name": software_name,
            "requester_email": requester_email,
            "device_id": device_id or "",
            "teams_conversation_ref": teams_conversation_ref or "",
            "justification": justification,
        }
        self._save_extras()

        logger.info(
            "Gilligan's Island ticket %s created for '%s' (%s)",
            ticket_number,
            software_name,
            requester_email,
        )

        return TicketResult(
            sys_id=ticket_number,
            number=ticket_number,
            state="new",
            approval="requested",
        )

    # ------------------------------------------------------------------
    # Extras persistence — shared with demo/approve.py via a JSON file
    # ------------------------------------------------------------------

    def _load_extras(self) -> dict:
        try:
            with open(self._extras_file) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.warning("Could not load extras file %s: %s", self._extras_file, exc)
            return {}

    def _save_extras(self) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self._extras_file)), exist_ok=True)
            with open(self._extras_file, "w") as f:
                json.dump(self._extras, f, indent=2)
        except Exception as exc:
            logger.warning("Could not save extras file %s: %s", self._extras_file, exc)
