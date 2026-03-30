"""
Handles ServiceNow 'approved' webhook events.

Enriched flow (updated with KB advisory step):

  1. Assign licence via IAM
  2. Query KB Advisor (Subway ServiceNow KB + internet)
  3. LLM synthesises findings → RemediationPlan
  4. Resolve target device
  5. Dispatch enriched InstallCommand to local agent via Service Bus
  6. Notify user in Teams
  7. Update ServiceNow ticket with work note (including advisor summary)
"""
import logging
import uuid
from typing import Optional

import requests

from clients.agent_bus_client import AgentBusClient, InstallCommand
from clients.iam_client import IAMClient
from clients.kb_client import KBClient
from clients.llm_advisor import LLMAdvisor, RemediationPlan
from clients.servicenow_client import ServiceNowClient, TicketState
from config import OrchestratorConfig

logger = logging.getLogger(__name__)

# Winget package ID lookup — extend as software catalogue grows
WINGET_ID_MAP: dict[str, str] = {
    "Adobe Acrobat Pro": "Adobe.Acrobat.Pro.64-bit",
    "Adobe Acrobat": "Adobe.Acrobat.Reader.64-bit",
    "Power BI": "Microsoft.PowerBI",
    "Visio": "Microsoft.VisioViewer",
    "AutoCAD": "Autodesk.AutoCAD",
    "Bluebeam": "Bluebeam.Revu",
    "Snagit": "TechSmith.Snagit",
    "Zoom": "Zoom.Zoom",
    "Slack": "SlackTechnologies.Slack",
    "Tableau": "Tableau.Desktop",
}


class ApprovalHandler:
    def __init__(
        self,
        snow: ServiceNowClient,
        iam: IAMClient,
        bus: AgentBusClient,
        kb: KBClient,
        advisor: LLMAdvisor,
        config: OrchestratorConfig,
    ):
        self._snow = snow
        self._iam = iam
        self._bus = bus
        self._kb = kb
        self._advisor = advisor
        self._cfg = config

    def handle(self, ticket: TicketState) -> None:
        logger.info("Processing approval for ticket %s (%s)", ticket.number, ticket.software_name)

        # ── Step 1: Assign licence ─────────────────────────────────────
        licence_ok = self._iam.assign_licence(ticket.requester_email, ticket.software_name)
        if not licence_ok:
            self._snow.add_work_note(
                ticket.sys_id,
                "[Orchestrator] ⚠️ Licence assignment failed — please assign manually and re-trigger.",
            )
            return

        self._snow.add_work_note(
            ticket.sys_id,
            f"[Orchestrator] ✅ Licence assigned to {ticket.requester_email}.",
        )

        # ── Step 2: KB Advisory lookup ─────────────────────────────────
        logger.info("Running KB advisory search for '%s'…", ticket.software_name)
        self._snow.add_work_note(
            ticket.sys_id,
            "[Orchestrator] 🔍 Querying Subway knowledge base and vendor documentation…",
        )

        kb_result = self._kb.search(
            software_name=ticket.software_name,
            issue_context="silent install enterprise deployment",
        )

        # ── Step 3: LLM synthesis → RemediationPlan ───────────────────
        winget_id = WINGET_ID_MAP.get(ticket.software_name, "")
        plan: RemediationPlan = self._advisor.build_remediation_plan(
            software_name=ticket.software_name,
            winget_id=winget_id,
            kb_result=kb_result,
            issue_context="silent enterprise install, Azure AD-joined Windows 11",
        )

        # Post the advisor summary to the ticket so it's visible to helpdesk
        kb_note_lines = [
            f"[Orchestrator / KB Advisor] Remediation plan generated (confidence: {plan.confidence.upper()})",
            "",
            f"📋 {plan.advisor_notes}",
        ]
        if plan.known_issues:
            kb_note_lines.append("\nKnown issues accounted for:")
            kb_note_lines.extend(f"  • {issue}" for issue in plan.known_issues)
        if plan.pre_steps:
            kb_note_lines.append("\nPre-install steps:")
            kb_note_lines.extend(f"  • {s.description or s.action + ': ' + s.target}" for s in plan.pre_steps)
        if plan.post_steps:
            kb_note_lines.append("\nPost-install steps:")
            kb_note_lines.extend(f"  • {s.description or s.action + ': ' + s.target}" for s in plan.post_steps)
        if plan.kb_sources:
            kb_note_lines.append("\nSources consulted:")
            kb_note_lines.extend(f"  • {src}" for src in plan.kb_sources)

        self._snow.add_work_note(ticket.sys_id, "\n".join(kb_note_lines))

        # ── Step 4: Resolve device ─────────────────────────────────────
        device_id = self._resolve_device(ticket.requester_email, ticket.device_id)
        if not device_id:
            self._snow.add_work_note(
                ticket.sys_id,
                "[Orchestrator] ⚠️ No registered device found — cannot auto-install. Manual installation required.",
            )
            return

        # ── Step 5: Notify user in Teams ──────────────────────────────
        self._notify_teams(ticket, "approved")

        # ── Step 6: Dispatch enriched InstallCommand ───────────────────
        command = InstallCommand(
            command_id=str(uuid.uuid4()),
            device_id=device_id,
            user_email=ticket.requester_email,
            software_name=ticket.software_name,
            winget_id=plan.winget_id or winget_id,
            ticket_sys_id=ticket.sys_id,
            ticket_number=ticket.number,
            teams_conversation_ref=ticket.teams_conversation_ref,
            remediation_plan=plan.to_dict(),   # ← enriched plan passed to agent
        )
        self._bus.dispatch_install(command)

        self._snow.add_work_note(
            ticket.sys_id,
            f"[Orchestrator] 📦 Enriched install command dispatched to device {device_id}.",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_device(self, user_email: str, hint_device_id: str) -> Optional[str]:
        if hint_device_id:
            return hint_device_id
        try:
            resp = requests.get(
                f"{self._cfg.DEVICE_REGISTRY_URL}/devices",
                params={"user_email": user_email},
                headers={"X-API-Key": self._cfg.ORCHESTRATOR_API_KEY},
                timeout=10,
            )
            resp.raise_for_status()
            devices = resp.json().get("devices", [])
            if devices:
                return devices[0]["device_id"]
        except Exception as exc:
            logger.error("Device registry lookup failed: %s", exc)
        return None

    def _notify_teams(self, ticket: TicketState, event_type: str) -> None:
        if not ticket.teams_conversation_ref:
            return
        try:
            requests.post(
                f"{self._cfg.TEAMS_BOT_URL}/api/proactive",
                headers={
                    "X-API-Key": self._cfg.ORCHESTRATOR_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "conversation_ref": ticket.teams_conversation_ref,
                    "event_type": event_type,
                    "payload": {
                        "software_name": ticket.software_name,
                        "ticket_number": ticket.number,
                    },
                },
                timeout=10,
            )
        except Exception as exc:
            logger.error("Failed to send proactive Teams message: %s", exc)
