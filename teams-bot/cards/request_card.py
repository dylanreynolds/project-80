"""
Adaptive Card definitions for the software request flow.
Cards are rendered natively inside Microsoft Teams.
"""
from typing import Any


def build_justification_card(software_name: str) -> dict[str, Any]:
    """
    Shown to the user after they name the software they need.
    Collects a written business justification before creating the ticket.
    """
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "text": f"📋 Software Request — {software_name}",
                "weight": "Bolder",
                "size": "Medium",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": (
                    "This software requires **manager approval** because it carries a "
                    "per-seat licence cost. Please provide a detailed business justification "
                    "so your manager can make an informed decision."
                ),
                "wrap": True,
                "spacing": "Medium",
            },
            {
                "type": "Input.Text",
                "id": "justification",
                "placeholder": "e.g. I need Adobe Acrobat Pro to review, annotate, and e-sign "
                               "vendor contracts — our current PDF reader cannot handle these workflows.",
                "isMultiline": True,
                "maxLength": 1000,
                "label": "Business Justification",
                "isRequired": True,
                "errorMessage": "Please provide a justification before submitting.",
                "style": "text",
            },
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Submit Request",
                "style": "positive",
                "data": {"action": "submit_request", "software_name": software_name},
            },
            {
                "type": "Action.Submit",
                "title": "Cancel",
                "data": {"action": "cancel_request"},
            },
        ],
    }


def build_ticket_confirmation_card(ticket_number: str, software_name: str) -> dict[str, Any]:
    """Shown immediately after the ServiceNow ticket is created."""
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "text": "✅ Request Submitted",
                "weight": "Bolder",
                "size": "Medium",
                "color": "Good",
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Software", "value": software_name},
                    {"title": "Ticket", "value": ticket_number},
                    {"title": "Status", "value": "Pending Manager Approval"},
                ],
            },
            {
                "type": "TextBlock",
                "text": (
                    "Your manager has been notified via ServiceNow. "
                    "I'll message you here as soon as a decision is made — "
                    "no need to follow up manually."
                ),
                "wrap": True,
                "spacing": "Medium",
                "isSubtle": True,
            },
        ],
    }


def build_approval_notification_card(ticket_number: str, software_name: str) -> dict[str, Any]:
    """Proactive card sent to the user when the ticket is approved."""
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "text": "🎉 Request Approved!",
                "weight": "Bolder",
                "size": "Medium",
                "color": "Good",
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Software", "value": software_name},
                    {"title": "Ticket", "value": ticket_number},
                    {"title": "Status", "value": "Approved — Installation Starting"},
                ],
            },
            {
                "type": "TextBlock",
                "text": (
                    "Your licence has been assigned and the installation agent is running on your machine. "
                    "You'll receive another message when it's ready to use. "
                    "If you're prompted to authenticate, please sign in with your corporate account."
                ),
                "wrap": True,
                "spacing": "Medium",
                "isSubtle": True,
            },
        ],
    }


def build_install_complete_card(software_name: str) -> dict[str, Any]:
    """Proactive card sent when installation finishes successfully."""
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "text": f"✅ {software_name} is ready!",
                "weight": "Bolder",
                "size": "Medium",
                "color": "Good",
            },
            {
                "type": "TextBlock",
                "text": (
                    "The software has been installed on your device. "
                    "Look for it in your Start Menu or desktop. "
                    "If you run into any issues, reply here and I'll raise a support ticket."
                ),
                "wrap": True,
                "isSubtle": True,
            },
        ],
    }


def build_rejection_card(ticket_number: str, software_name: str, reason: str = "") -> dict[str, Any]:
    """Proactive card sent when the ticket is rejected."""
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "text": "❌ Request Not Approved",
                "weight": "Bolder",
                "size": "Medium",
                "color": "Attention",
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Software", "value": software_name},
                    {"title": "Ticket", "value": ticket_number},
                    {"title": "Reason", "value": reason or "No reason provided"},
                ],
            },
            {
                "type": "TextBlock",
                "text": "If you believe this was declined in error, please speak to your manager directly.",
                "wrap": True,
                "isSubtle": True,
            },
        ],
    }
