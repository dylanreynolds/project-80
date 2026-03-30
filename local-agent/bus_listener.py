"""
Azure Service Bus listener — local desktop agent (Autobot).

Security changes vs original:
  - Uses Azure Managed Identity (device identity, no connection strings on disk).
  - Every received message is verified with CommandVerifier before any action is taken.
  - Messages that fail signature, timestamp, or device-binding checks are dead-lettered
    and an alert is raised — repeated failures trigger a security alert to the orchestrator.
"""
import json
import logging
from typing import Callable

from azure.identity import ManagedIdentityCredential, DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.servicebus.exceptions import ServiceBusConnectionError, ServiceBusError

from security.command_verifier import CommandVerifier

logger = logging.getLogger(__name__)

TOPIC_NAME = "it-automation"
SUBSCRIPTION_NAME = "agent-commands"

# How many consecutive security failures before we alert the orchestrator
SECURITY_ALERT_THRESHOLD = 3


class BusListener:
    def __init__(
        self,
        fully_qualified_namespace: str,
        device_id: str,
        verifier: CommandVerifier,
        orchestrator_url: str = "",
        orchestrator_api_key: str = "",
    ):
        """
        Args:
            fully_qualified_namespace: e.g. "yournamespace.servicebus.windows.net"
            device_id: This agent's stable device UUID
            verifier: CommandVerifier initialised with the DPAPI-stored signing secret
            orchestrator_url / orchestrator_api_key: for security alerts
        """
        self._namespace = fully_qualified_namespace
        self._device_id = device_id
        self._verifier = verifier
        self._orchestrator_url = orchestrator_url
        self._orchestrator_api_key = orchestrator_api_key
        self._running = False
        self._consecutive_security_failures = 0

    def start(self, on_command: Callable[[dict], None]) -> None:
        """Blocking loop. Call from a dedicated thread."""
        self._running = True
        # Use ManagedIdentityCredential on AAD-joined devices (runs as SYSTEM)
        # Falls back to DefaultAzureCredential for dev environments
        try:
            credential = ManagedIdentityCredential()
        except Exception:
            credential = DefaultAzureCredential()

        logger.info(
            "BusListener starting (Managed Identity) — namespace=%s device=%s",
            self._namespace,
            self._device_id,
        )

        while self._running:
            try:
                with ServiceBusClient(self._namespace, credential) as client:
                    receiver = client.get_subscription_receiver(
                        topic_name=TOPIC_NAME,
                        subscription_name=SUBSCRIPTION_NAME,
                        max_wait_time=30,
                    )
                    with receiver:
                        while self._running:
                            messages = receiver.receive_messages(
                                max_message_count=1, max_wait_time=10
                            )
                            for msg in messages:
                                self._handle_message(msg, receiver, on_command)

            except (ServiceBusConnectionError, ServiceBusError) as exc:
                logger.error("Service Bus error — reconnecting in 15s: %s", exc)
                import time; time.sleep(15)
            except Exception as exc:
                logger.critical("Unexpected BusListener error: %s", exc, exc_info=True)
                import time; time.sleep(30)

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Message handling with security verification
    # ------------------------------------------------------------------

    def _handle_message(self, msg, receiver, on_command: Callable[[dict], None]) -> None:
        try:
            raw = json.loads(str(msg))
        except json.JSONDecodeError as exc:
            logger.error("Malformed JSON in Service Bus message: %s", exc)
            receiver.dead_letter_message(msg, reason="Malformed JSON")
            return

        # ── Security gate: verify before touching anything ──────────────
        valid, reason = self._verifier.verify(raw, self._device_id)
        if not valid:
            self._consecutive_security_failures += 1
            logger.error(
                "SECURITY FAILURE #%d: %s | command_id=%s",
                self._consecutive_security_failures,
                reason,
                raw.get("command_id", "unknown"),
            )
            receiver.dead_letter_message(msg, reason=reason)

            if self._consecutive_security_failures >= SECURITY_ALERT_THRESHOLD:
                self._send_security_alert(reason, raw.get("command_id", ""))
                self._consecutive_security_failures = 0  # Reset after alert
            return

        # Passed — reset failure counter
        self._consecutive_security_failures = 0
        logger.info(
            "Command %s verified ✓ — routing to handler",
            raw.get("command_id", "unknown"),
        )

        try:
            on_command(raw)
            receiver.complete_message(msg)
        except Exception as exc:
            logger.error("Command handling failed: %s", exc)
            receiver.dead_letter_message(msg, reason=str(exc))

    def _send_security_alert(self, reason: str, command_id: str) -> None:
        """
        POST a security alert to the orchestrator when repeated verification
        failures suggest an active attack on this device's Service Bus subscription.
        """
        if not self._orchestrator_url:
            logger.warning("No orchestrator URL — cannot send security alert.")
            return
        try:
            import requests
            requests.post(
                f"{self._orchestrator_url}/security/alert",
                headers={
                    "X-API-Key": self._orchestrator_api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "device_id": self._device_id,
                    "alert_type": "repeated_signature_failure",
                    "command_id": command_id,
                    "reason": reason,
                    "threshold": SECURITY_ALERT_THRESHOLD,
                },
                timeout=10,
            )
            logger.warning("Security alert sent to orchestrator for device %s", self._device_id)
        except Exception as exc:
            logger.error("Failed to send security alert: %s", exc)


class BusSender:
    """Sends AgentEvent messages back to the orchestrator via Managed Identity."""

    def __init__(self, fully_qualified_namespace: str):
        self._namespace = fully_qualified_namespace
        try:
            self._credential = ManagedIdentityCredential()
        except Exception:
            self._credential = DefaultAzureCredential()

    def send_event(self, event: dict) -> None:
        with ServiceBusClient(self._namespace, self._credential) as client:
            sender = client.get_topic_sender(topic_name=TOPIC_NAME)
            with sender:
                msg = ServiceBusMessage(
                    body=json.dumps(event),
                    content_type="application/json",
                    application_properties={
                        "event_type": event.get("event_type", ""),
                        "subscription_target": "agent-events",
                    },
                )
                sender.send_messages(msg)
                logger.info(
                    "AgentEvent '%s' sent for ticket %s",
                    event.get("event_type"),
                    event.get("ticket_number"),
                )
