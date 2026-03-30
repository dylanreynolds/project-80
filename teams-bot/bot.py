"""
IT Helpdesk Teams Bot.

Responsibilities:
  - Greet users and detect software-request intent from free-text messages.
  - Route to SoftwareRequestDialog to collect justification and raise a ServiceNow ticket.
  - Expose a /proactive endpoint so the Orchestrator can push status updates
    (approval, install-complete, rejection) back into the Teams thread.
"""
import json
import logging
import re
from typing import List

from botbuilder.core import (
    ActivityHandler,
    CardFactory,
    ConversationState,
    MessageFactory,
    TurnContext,
    UserState,
)
from botbuilder.dialogs import Dialog, DialogSet, DialogTurnStatus
from botbuilder.schema import Activity, ChannelAccount

from cards.request_card import (
    build_approval_notification_card,
    build_install_complete_card,
    build_rejection_card,
)
from config import BotConfig
from dialogs.software_request_dialog import SoftwareRequestDialog
from integrations.servicenow_client import ServiceNowClient

logger = logging.getLogger(__name__)

# Basic keyword heuristic — replace with Azure CLU / LUIS for production
_SOFTWARE_REQUEST_PATTERNS = [
    r"\b(need|want|get|install|request|can i (get|have))\b.{0,60}\b(software|app|application|tool|license|licence|adobe|office|power\s?bi|visio|autocad|slack|zoom|teams)\b",
    r"\b(adobe|autocad|power\s?bi|visio|bluebeam|snagit|camtasia|matlab|tableau)\b",
]


class ITHelpdeskBot(ActivityHandler):
    def __init__(
        self,
        conversation_state: ConversationState,
        user_state: UserState,
        snow_client: ServiceNowClient,
    ):
        super().__init__()
        self._conversation_state = conversation_state
        self._user_state = user_state
        self._snow = snow_client

        self._dialog = SoftwareRequestDialog("SoftwareRequestDialog", snow_client)
        self._dialog_state_accessor = conversation_state.create_property("DialogState")

    # ------------------------------------------------------------------
    # Incoming message
    # ------------------------------------------------------------------

    async def on_message_activity(self, turn_context: TurnContext):
        # Handle Adaptive Card submit actions
        if turn_context.activity.value:
            await self._handle_card_action(turn_context)
            return

        text = (turn_context.activity.text or "").strip()

        if self._is_software_request(text):
            software_name = self._extract_software_name(text)
            await self._run_dialog(
                turn_context,
                options={"software_name": software_name} if software_name else None,
            )
        else:
            await turn_context.send_activity(
                MessageFactory.text(
                    "Hi! I can help you request software licences. "
                    "Just tell me what you need — for example: *\"I need Adobe Acrobat Pro\"*."
                )
            )

        await self._conversation_state.save_changes(turn_context)
        await self._user_state.save_changes(turn_context)

    # ------------------------------------------------------------------
    # Card action handler
    # ------------------------------------------------------------------

    async def _handle_card_action(self, turn_context: TurnContext):
        value: dict = turn_context.activity.value or {}
        action = value.get("action")

        if action == "submit_request":
            # User submitted the justification card — inject the justification as text
            # so the active waterfall dialog can continue
            justification = value.get("justification", "").strip()
            if not justification:
                await turn_context.send_activity(
                    MessageFactory.text("Please provide a justification before submitting.")
                )
                return
            turn_context.activity.text = justification
            await self._run_dialog(turn_context)

        elif action == "cancel_request":
            await turn_context.send_activity(
                MessageFactory.text(
                    "No problem — your request has been cancelled. Let me know if you change your mind."
                )
            )
            # Clear any in-progress dialog
            dialog_state = await self._dialog_state_accessor.get(turn_context, lambda: {})
            dialog_state.clear()

    # ------------------------------------------------------------------
    # Proactive message ingress (called by app.py /api/proactive endpoint)
    # ------------------------------------------------------------------

    @staticmethod
    async def send_proactive_message(
        turn_context: TurnContext,
        event_type: str,
        payload: dict,
    ):
        """
        Sends a proactive card to the user based on an orchestrator event.
        Called from app.py after restoring the conversation reference.
        """
        software = payload.get("software_name", "the software")
        ticket = payload.get("ticket_number", "")
        reason = payload.get("reason", "")

        if event_type == "approved":
            card = build_approval_notification_card(ticket, software)
        elif event_type == "install_complete":
            card = build_install_complete_card(software)
        elif event_type == "rejected":
            card = build_rejection_card(ticket, software, reason)
        else:
            await turn_context.send_activity(
                MessageFactory.text(f"Update on your {software} request: {event_type}")
            )
            return

        await turn_context.send_activity(
            MessageFactory.attachment(CardFactory.adaptive_card(card))
        )

    # ------------------------------------------------------------------
    # Welcome message
    # ------------------------------------------------------------------

    async def on_members_added_activity(self, members_added: List[ChannelAccount], turn_context: TurnContext):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(
                    MessageFactory.text(
                        "👋 Hi! I'm the IT Helpdesk assistant. "
                        "I can help you request software, track your tickets, and more. "
                        "What do you need today?"
                    )
                )

    # ------------------------------------------------------------------
    # Dialog runner
    # ------------------------------------------------------------------

    async def _run_dialog(self, turn_context: TurnContext, options: dict = None):
        dialog_set = DialogSet(self._dialog_state_accessor)
        dialog_set.add(self._dialog)

        dialog_context = await dialog_set.create_context(turn_context)
        results = await dialog_context.continue_dialog()

        if results.status == DialogTurnStatus.Empty:
            await dialog_context.begin_dialog(self._dialog.id, options)

    # ------------------------------------------------------------------
    # Intent detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_software_request(text: str) -> bool:
        lower = text.lower()
        for pattern in _SOFTWARE_REQUEST_PATTERNS:
            if re.search(pattern, lower):
                return True
        return False

    @staticmethod
    def _extract_software_name(text: str) -> str | None:
        """
        Very lightweight extraction — pulls the first capitalised product noun.
        In production, replace with CLU entity extraction.
        """
        known = [
            "Adobe Acrobat Pro", "Adobe Acrobat", "Adobe", "Power BI", "PowerBI",
            "Visio", "AutoCAD", "Bluebeam", "Snagit", "Camtasia", "Matlab",
            "Tableau", "Slack", "Zoom", "Teams",
        ]
        for name in known:
            if name.lower() in text.lower():
                return name
        return None
