"""
Waterfall dialog that drives the software request conversation.

Flow:
  1. Identify software name (from initial message or prompt)
  2. Show justification Adaptive Card
  3. On submit → create ServiceNow ticket
  4. Confirm ticket to user
"""
import json
import logging
from typing import Optional

from botbuilder.core import CardFactory, MessageFactory, TurnContext
from botbuilder.dialogs import (
    ComponentDialog,
    DialogTurnResult,
    WaterfallDialog,
    WaterfallStepContext,
)
from botbuilder.dialogs.prompts import TextPrompt, PromptOptions
from botbuilder.schema import Activity

from cards.request_card import build_justification_card, build_ticket_confirmation_card
from integrations.servicenow_client import ServiceNowClient

logger = logging.getLogger(__name__)

WATERFALL_DIALOG = "SoftwareRequestWaterfall"
TEXT_PROMPT = "TextPrompt"


class SoftwareRequestDialog(ComponentDialog):
    """Collects software name + justification, then raises a ServiceNow ticket."""

    def __init__(self, dialog_id: str, snow_client: ServiceNowClient):
        super().__init__(dialog_id)
        self._snow = snow_client

        self.add_dialog(TextPrompt(TEXT_PROMPT))
        self.add_dialog(
            WaterfallDialog(
                WATERFALL_DIALOG,
                [
                    self._ask_software_step,
                    self._show_justification_card_step,
                    self._create_ticket_step,
                ],
            )
        )
        self.initial_dialog_id = WATERFALL_DIALOG

    # ------------------------------------------------------------------
    # Step 1 — identify what software the user wants
    # ------------------------------------------------------------------
    async def _ask_software_step(self, step: WaterfallStepContext) -> DialogTurnResult:
        # The main bot may pass the software name as the options argument
        if step.options and isinstance(step.options, dict):
            software = step.options.get("software_name")
            if software:
                return await step.next(software)

        return await step.prompt(
            TEXT_PROMPT,
            PromptOptions(
                prompt=MessageFactory.text(
                    "Sure, I can help with that! Which software application do you need?"
                )
            ),
        )

    # ------------------------------------------------------------------
    # Step 2 — show the Adaptive Card to collect justification
    # ------------------------------------------------------------------
    async def _show_justification_card_step(self, step: WaterfallStepContext) -> DialogTurnResult:
        software_name: str = step.result
        step.values["software_name"] = software_name

        card = build_justification_card(software_name)
        card_activity = MessageFactory.attachment(CardFactory.adaptive_card(card))
        await step.context.send_activity(card_activity)

        # We need to wait for the card submit action — handled via on_message_activity
        # Store state so the bot knows to route the next message to this dialog
        step.values["awaiting_justification"] = True
        return await step.prompt(
            TEXT_PROMPT,
            PromptOptions(prompt=MessageFactory.text("")),
        )

    # ------------------------------------------------------------------
    # Step 3 — create the ticket
    # ------------------------------------------------------------------
    async def _create_ticket_step(self, step: WaterfallStepContext) -> DialogTurnResult:
        justification: str = step.result
        software_name: str = step.values.get("software_name", "Unknown")

        ctx: TurnContext = step.context
        activity = ctx.activity

        requester_email: str = _extract_email(activity)
        requester_name: str = activity.from_property.name or requester_email
        device_id: Optional[str] = _extract_device_id(activity)

        # Serialise the conversation reference so the orchestrator can send
        # proactive messages back to this exact Teams thread.
        from botbuilder.core import TurnContext as TC
        conv_ref_json = json.dumps(TC.get_conversation_reference(activity).serialize())

        await step.context.send_activity(
            MessageFactory.text(f"Creating your request for **{software_name}**…")
        )

        try:
            ticket = self._snow.create_software_request(
                requester_email=requester_email,
                requester_name=requester_name,
                software_name=software_name,
                justification=justification,
                device_id=device_id,
                teams_conversation_ref=conv_ref_json,
            )
        except Exception as exc:
            logger.error("ServiceNow ticket creation failed: %s", exc)
            await step.context.send_activity(
                MessageFactory.text(
                    "Sorry, I couldn't create your ticket right now. "
                    "Please try again or contact the helpdesk directly."
                )
            )
            return await step.end_dialog()

        card = build_ticket_confirmation_card(ticket.number, software_name)
        await step.context.send_activity(
            MessageFactory.attachment(CardFactory.adaptive_card(card))
        )

        logger.info("Created ticket %s for %s", ticket.number, requester_email)
        return await step.end_dialog(ticket)


# ------------------------------------------------------------------
# Utility helpers
# ------------------------------------------------------------------

def _extract_email(activity: Activity) -> str:
    """
    In Teams, the user's UPN (email) is available on activity.from_property.aad_object_id
    via Graph, but the simplest approach is to use the channel account name which
    Teams populates as the UPN for AAD-backed tenants.
    """
    return getattr(activity.from_property, "name", "") or "unknown@company.com"


def _extract_device_id(activity: Activity) -> Optional[str]:
    """
    The local agent registers devices with the orchestrator; the device is looked up
    by the user's AAD object ID at installation time.  We store nothing here —
    device resolution happens in the orchestrator.
    """
    return getattr(activity.from_property, "aad_object_id", None)
