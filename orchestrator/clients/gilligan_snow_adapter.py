"""
Gilligan's Island ServiceNow adapter — orchestrator side.

Drop-in replacement for clients/servicenow_client.py that talks to the
Gilligan's Island mock server (http://<host>:3000/api/snow/tickets) instead
of a real ServiceNow instance.

Gilligan's Island tickets are offboarding-oriented, so extra fields specific
to software requests (software_name, requester_email, device_id,
teams_conversation_ref) are held in a local in-memory dict keyed by ticket
number.  The webhook payload from our demo/approve.py carries these back to
the orchestrator so the approval_handler can proceed normally.
"""
import logging
from typing import Optional

import requests

from clients.servicenow_client import TicketState

logger = logging.getLogger(__name__)


class GilliganServiceNowAdapter:
    """Implements the same interface as ServiceNowClient, backed by Gilligan's Island."""

    def __init__(self, base_url: str):
        # e.g. "http://192.168.56.1:3000"
        self._base = base_url.rstrip("/")
        # ticket_number → extra fields not stored in Gilligan's Island
        self._extras: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Called by ApprovalHandler after a ticket is created (bot side stores
    # extras via its own adapter; orchestrator side receives them via the
    # webhook payload injected by demo/approve.py).
    # ------------------------------------------------------------------

    def register_extras(
        self,
        ticket_number: str,
        software_name: str = "",
        requester_email: str = "",
        device_id: str = "",
        teams_conversation_ref: str = "",
    ) -> None:
        """Persist fields that Gilligan's Island doesn't store natively."""
        self._extras[ticket_number] = {
            "software_name": software_name,
            "requester_email": requester_email,
            "device_id": device_id,
            "teams_conversation_ref": teams_conversation_ref,
        }
        logger.debug("Registered extras for ticket %s", ticket_number)

    # ------------------------------------------------------------------
    # ServiceNowClient interface
    # ------------------------------------------------------------------

    def get_ticket(self, sys_id: str) -> TicketState:
        """
        sys_id is the ticket number for Gilligan's Island (e.g. RITM1000001).
        Extras (software_name etc.) come from the local registry or the
        webhook payload stored there by the /webhook/servicenow handler.
        """
        try:
            r = requests.get(
                f"{self._base}/api/snow/tickets/{sys_id}",
                timeout=15,
            )
            r.raise_for_status()
            raw = r.json()
        except Exception as exc:
            logger.error("Failed to fetch Gilligan's Island ticket %s: %s", sys_id, exc)
            # Return a minimal stub so the orchestrator can still proceed
            raw = {}

        extras = self._extras.get(sys_id, {})

        # Map Gilligan's Island state to ServiceNow approval strings
        if raw.get("approvedAt"):
            approval = "approved"
        elif raw.get("state") in ("cancelled", "closed"):
            approval = "rejected"
        else:
            approval = "requested"

        return TicketState(
            sys_id=sys_id,
            number=raw.get("number", sys_id),
            approval=approval,
            state=str(raw.get("state", "new")),
            software_name=extras.get("software_name", ""),
            requester_email=extras.get("requester_email", ""),
            device_id=extras.get("device_id", ""),
            teams_conversation_ref=extras.get("teams_conversation_ref", ""),
        )

    def add_work_note(self, sys_id: str, note: str, close: bool = False) -> None:
        try:
            r = requests.post(
                f"{self._base}/api/snow/tickets/{sys_id}/work-note",
                json={"note": note},
                timeout=15,
            )
            r.raise_for_status()
            logger.info("Work note added to Gilligan's Island ticket %s", sys_id)
        except Exception as exc:
            logger.warning("Could not add work note to %s (non-fatal): %s", sys_id, exc)

        if close:
            try:
                requests.post(
                    f"{self._base}/api/snow/tickets/{sys_id}/resolve",
                    timeout=15,
                )
                logger.info("Gilligan's Island ticket %s resolved", sys_id)
            except Exception as exc:
                logger.warning("Could not resolve ticket %s (non-fatal): %s", sys_id, exc)
