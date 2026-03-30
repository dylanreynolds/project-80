"""
Azure Service Bus client — dispatches signed installation commands to local desktop agents.

Security changes vs original:
  - Uses Managed Identity (DefaultAzureCredential) — no connection strings stored anywhere.
  - Every outbound InstallCommand is HMAC-SHA256 signed by CommandSigner.
  - Signing secret is fetched from Azure Key Vault at startup, not from env vars.

Topic model:
  - Topic: "it-automation"
  - Subscription "agent-commands"  →  local agent subscribes (filtered by device_id)
  - Subscription "agent-events"    →  orchestrator subscribes for completion callbacks
"""
import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Callable, Optional

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.servicebus.exceptions import ServiceBusError

from security.command_signer import CommandSigner

logger = logging.getLogger(__name__)

TOPIC_NAME = "it-automation"
COMMANDS_SUBSCRIPTION = "agent-commands"
EVENTS_SUBSCRIPTION = "agent-events"


@dataclass
class InstallCommand:
    command_id: str
    device_id: str                # Unique identifier registered by the local agent at startup
    user_email: str
    software_name: str            # Human-readable
    winget_id: str                # e.g. "Adobe.Acrobat.Pro.64-bit"
    ticket_sys_id: str
    ticket_number: str
    teams_conversation_ref: str   # JSON-serialised; forwarded so agent can call /api/proactive
    remediation_plan: dict = None # Validated RemediationPlan dict — None = standard install
    # _sig and _ts are injected by CommandSigner.sign() — do not set manually


@dataclass
class AgentEvent:
    command_id: str
    device_id: str
    event_type: str   # "install_complete" | "install_failed" | "already_installed" | "upgraded"
    software_name: str
    ticket_sys_id: str
    ticket_number: str
    teams_conversation_ref: str
    detail: str = ""


class AgentBusClient:
    def __init__(self, fully_qualified_namespace: str, signer: CommandSigner):
        """
        Args:
            fully_qualified_namespace: e.g. "yournamespace.servicebus.windows.net"
                                       (NOT a connection string)
            signer: CommandSigner instance initialised with the Key Vault secret
        """
        self._namespace = fully_qualified_namespace
        self._credential = DefaultAzureCredential()
        self._signer = signer

    # ------------------------------------------------------------------
    # Send a signed command to a specific device's local agent
    # ------------------------------------------------------------------

    def dispatch_install(self, command: InstallCommand) -> None:
        """
        Signs and publishes an InstallCommand to the Service Bus topic.
        The local agent verifies the signature before executing any step.
        """
        raw_dict = asdict(command)

        # Remove None values — cleaner wire format and avoids signing nulls
        raw_dict = {k: v for k, v in raw_dict.items() if v is not None}

        # Sign the command — adds _sig and _ts fields
        signed_dict = self._signer.sign(raw_dict)

        with ServiceBusClient(self._namespace, self._credential) as client:
            sender = client.get_topic_sender(topic_name=TOPIC_NAME)
            with sender:
                msg = ServiceBusMessage(
                    body=json.dumps(signed_dict),
                    content_type="application/json",
                    message_id=command.command_id,
                    # Service Bus SQL filter on this property routes to the right device subscription
                    application_properties={"device_id": command.device_id},
                )
                sender.send_messages(msg)
                logger.info(
                    "Signed install command %s dispatched → device %s for '%s'",
                    command.command_id,
                    command.device_id,
                    command.software_name,
                )

    # ------------------------------------------------------------------
    # Receive completion events from agents
    # ------------------------------------------------------------------

    def listen_for_events(self, callback: Callable[[AgentEvent], None]) -> None:
        """
        Blocking loop — call from a background thread.
        Invokes `callback` for each AgentEvent received from any local agent.
        """
        logger.info("Listening for agent events on Service Bus (Managed Identity)…")
        with ServiceBusClient(self._namespace, self._credential) as client:
            receiver = client.get_subscription_receiver(
                topic_name=TOPIC_NAME,
                subscription_name=EVENTS_SUBSCRIPTION,
                max_wait_time=30,
            )
            with receiver:
                while True:
                    try:
                        messages = receiver.receive_messages(max_message_count=10, max_wait_time=5)
                        for msg in messages:
                            try:
                                raw = json.loads(str(msg))
                                event = AgentEvent(**raw)
                                callback(event)
                                receiver.complete_message(msg)
                            except Exception as exc:
                                logger.error("Failed to process agent event: %s", exc)
                                receiver.abandon_message(msg)
                    except ServiceBusError as exc:
                        logger.error("Service Bus error: %s — retrying…", exc)
