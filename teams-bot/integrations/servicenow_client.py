"""
ServiceNow REST API client.
Creates and updates software request tickets (sc_request / sc_req_item).
"""
import logging
from dataclasses import dataclass
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


@dataclass
class TicketResult:
    sys_id: str
    number: str          # e.g. RITM0001234
    state: str
    approval: str        # "requested" | "approved" | "rejected"


class ServiceNowClient:
    def __init__(self, instance: str, username: str, password: str):
        self.base_url = f"https://{instance}.service-now.com/api/now"
        self.auth = HTTPBasicAuth(username, password)
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Public helpers
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
        Open a Catalog Request (sc_request) for a software installation.
        Custom fields (u_*) must exist on your ServiceNow instance — adjust as needed.
        """
        payload = {
            "short_description": f"Software Request: {software_name}",
            "description": (
                f"Software Requested: {software_name}\n"
                f"Requester: {requester_name} ({requester_email})\n"
                f"Device: {device_id or 'Unknown'}\n\n"
                f"Business Justification:\n{justification}"
            ),
            "category": "Software",
            "subcategory": "New Software Installation",
            "caller_id": requester_email,
            "impact": "3",   # Low
            "urgency": "3",  # Low
            # Custom fields — add these to your sc_request table in ServiceNow
            "u_software_name": software_name,
            "u_requester_email": requester_email,
            "u_device_id": device_id or "",
            "u_teams_conversation_ref": teams_conversation_ref or "",
            "approval": "requested",
        }

        resp = self._post("/table/sc_request", payload)
        return self._parse_ticket(resp["result"])

    def get_ticket(self, sys_id: str) -> TicketResult:
        resp = self._get(f"/table/sc_request/{sys_id}")
        return self._parse_ticket(resp["result"])

    def update_ticket(self, sys_id: str, work_notes: str, state: Optional[str] = None) -> TicketResult:
        payload: dict = {"work_notes": work_notes}
        if state:
            payload["state"] = state
        resp = self._patch(f"/table/sc_request/{sys_id}", payload)
        return self._parse_ticket(resp["result"])

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        r = requests.post(
            self.base_url + path, auth=self.auth, headers=self.headers, json=payload, timeout=15
        )
        r.raise_for_status()
        return r.json()

    def _get(self, path: str) -> dict:
        r = requests.get(
            self.base_url + path, auth=self.auth, headers=self.headers, timeout=15
        )
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, payload: dict) -> dict:
        r = requests.patch(
            self.base_url + path, auth=self.auth, headers=self.headers, json=payload, timeout=15
        )
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _parse_ticket(raw: dict) -> TicketResult:
        return TicketResult(
            sys_id=raw.get("sys_id", ""),
            number=raw.get("number", ""),
            state=raw.get("state", ""),
            approval=raw.get("approval", ""),
        )
