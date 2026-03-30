"""
Orchestrator-side ServiceNow client.
Used to poll ticket state and post work notes as events progress.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


@dataclass
class TicketState:
    sys_id: str
    number: str
    approval: str            # "requested" | "approved" | "rejected"
    state: str               # ServiceNow state integer as string
    software_name: str
    requester_email: str
    device_id: str
    teams_conversation_ref: str


class ServiceNowClient:
    def __init__(self, instance: str, username: str, password: str):
        self.base_url = f"https://{instance}.service-now.com/api/now"
        self.auth = HTTPBasicAuth(username, password)
        self.headers = {"Content-Type": "application/json", "Accept": "application/json"}

    def get_ticket(self, sys_id: str) -> TicketState:
        r = requests.get(
            f"{self.base_url}/table/sc_request/{sys_id}",
            auth=self.auth,
            headers=self.headers,
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json()["result"]
        return TicketState(
            sys_id=raw["sys_id"],
            number=raw.get("number", ""),
            approval=raw.get("approval", ""),
            state=raw.get("state", ""),
            software_name=raw.get("u_software_name", ""),
            requester_email=raw.get("u_requester_email", ""),
            device_id=raw.get("u_device_id", ""),
            teams_conversation_ref=raw.get("u_teams_conversation_ref", ""),
        )

    def add_work_note(self, sys_id: str, note: str, close: bool = False) -> None:
        payload: dict = {"work_notes": note}
        if close:
            payload["state"] = "3"   # Closed Complete in most ServiceNow configs
        r = requests.patch(
            f"{self.base_url}/table/sc_request/{sys_id}",
            auth=self.auth,
            headers=self.headers,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        logger.info("Work note added to %s", sys_id)
