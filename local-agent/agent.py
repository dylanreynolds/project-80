"""
Local Desktop Agent — main entry point.

Runs as a Windows Service (via pywin32) or a long-running scheduled task.
On startup it:
  1. Registers this device with the Orchestrator
  2. Starts the Service Bus listener in a background thread
  3. Processes incoming InstallCommands

For each command it:
  1. Checks if the app is already installed
  2. If installed → attempts upgrade
  3. If not installed → installs silently via winget or SCCM
  4. Reports outcome back to Orchestrator via Service Bus
"""
import json
import logging
import os
import platform
import socket
import sys
import threading
import uuid
from typing import Optional

import requests

from bus_listener import BusListener, BusSender
from modules.app_checker import check_installed
from modules.app_installer import AppInstaller, InstallResult
from modules.plan_executor import PlanExecutor
from security.command_verifier import CommandVerifier, load_signing_secret

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.environ.get("PROGRAMDATA", "C:\\ProgramData"), "ITAgent", "agent.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Configuration — loaded from environment / registry at startup
# ------------------------------------------------------------------

class AgentConfig:
    # Set during deployment (e.g. via Intune configuration profile or Group Policy)
    # Connection string replaced by Managed Identity — only the namespace is needed now
    SERVICE_BUS_NAMESPACE: str = os.environ.get("AGENT_SERVICE_BUS_NAMESPACE", "")
    # Legacy fallback for dev environments only
    SERVICE_BUS_CONNECTION_STRING: str = os.environ.get("AGENT_SERVICE_BUS_CONN", "")
    ORCHESTRATOR_URL: str = os.environ.get("ORCHESTRATOR_URL", "")
    ORCHESTRATOR_API_KEY: str = os.environ.get("ORCHESTRATOR_API_KEY", "")
    USER_EMAIL: str = os.environ.get("AGENT_USER_EMAIL", "")   # Populated at enrolment time

    # SCCM (optional — leave empty to use winget only)
    SCCM_SERVER: str = os.environ.get("SCCM_SERVER", "")
    SCCM_SITE: str = os.environ.get("SCCM_SITE", "")
    SCCM_API_KEY: str = os.environ.get("SCCM_API_KEY", "")

    # Stable device identity — persisted to disk on first run
    DEVICE_ID_FILE: str = os.path.join(
        os.environ.get("PROGRAMDATA", "C:\\ProgramData"), "ITAgent", "device_id.txt"
    )

    @classmethod
    def device_id(cls) -> str:
        """Return (or generate-and-persist) a stable device UUID."""
        os.makedirs(os.path.dirname(cls.DEVICE_ID_FILE), exist_ok=True)
        if os.path.exists(cls.DEVICE_ID_FILE):
            return open(cls.DEVICE_ID_FILE).read().strip()
        did = str(uuid.uuid4())
        with open(cls.DEVICE_ID_FILE, "w") as f:
            f.write(did)
        return did


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

def register_with_orchestrator(cfg: AgentConfig, device_id: str) -> bool:
    if not cfg.ORCHESTRATOR_URL:
        logger.warning("ORCHESTRATOR_URL not set — skipping registration.")
        return False
    try:
        resp = requests.post(
            f"{cfg.ORCHESTRATOR_URL}/devices/register",
            headers={
                "X-API-Key": cfg.ORCHESTRATOR_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "device_id": device_id,
                "user_email": cfg.USER_EMAIL,
                "hostname": socket.getfqdn(),
                "platform": platform.system().lower(),
                "agent_version": "1.0.0",
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Registered with orchestrator as device %s", device_id)
        return True
    except Exception as exc:
        logger.error("Registration failed: %s", exc)
        return False


# ------------------------------------------------------------------
# Command handler — runs in the Bus listener thread
# ------------------------------------------------------------------

def handle_command(raw_command: dict, cfg: AgentConfig, sender: BusSender, device_id: str) -> None:
    """
    Process a single InstallCommand dict dispatched by the Orchestrator.
    """
    software_name = raw_command.get("software_name", "")
    winget_id = raw_command.get("winget_id", "")
    ticket_sys_id = raw_command.get("ticket_sys_id", "")
    ticket_number = raw_command.get("ticket_number", "")
    teams_conv_ref = raw_command.get("teams_conversation_ref", "")
    command_id = raw_command.get("command_id", "")

    logger.info("Command %s: install '%s' (winget_id=%s)", command_id, software_name, winget_id)

    def _emit(event_type: str, detail: str = ""):
        sender.send_event({
            "command_id": command_id,
            "device_id": device_id,
            "event_type": event_type,
            "software_name": software_name,
            "ticket_sys_id": ticket_sys_id,
            "ticket_number": ticket_number,
            "teams_conversation_ref": teams_conv_ref,
            "detail": detail,
        })

    remediation_plan: dict = raw_command.get("remediation_plan") or {}
    pre_steps: list = remediation_plan.get("pre_steps", [])
    post_steps: list = remediation_plan.get("post_steps", [])
    known_issues: list = remediation_plan.get("known_issues", [])
    advisor_notes: str = remediation_plan.get("advisor_notes", "")
    override_winget_id: str = remediation_plan.get("winget_id", winget_id)

    if advisor_notes:
        logger.info("KB Advisor notes: %s", advisor_notes)
    if known_issues:
        logger.info("Known issues to account for: %s", "; ".join(known_issues))

    executor = PlanExecutor()

    try:
        # ── Pre-steps (KB-informed: uninstall conflicts, clean registry, etc.) ──
        if pre_steps:
            logger.info("Running %d pre-install step(s)…", len(pre_steps))
            pre_results = executor.run_steps(pre_steps)
            failed_pre = [r for r in pre_results if not r.success]
            if failed_pre:
                logger.warning(
                    "%d pre-step(s) failed (non-fatal): %s",
                    len(failed_pre),
                    "; ".join(f"{r.action}:{r.target}" for r in failed_pre),
                )

        # ── Check installation state ───────────────────────────────────────────
        app_info = check_installed(software_name, override_winget_id)
        already_installed = app_info is not None
        installed_version = app_info.version if app_info else ""

        if already_installed:
            logger.info("'%s' v%s already installed — will attempt upgrade.", software_name, installed_version)
        else:
            logger.info("'%s' not found — proceeding with fresh install.", software_name)

        # ── Install / upgrade ─────────────────────────────────────────────────
        sccm_config = None
        if cfg.SCCM_SERVER and cfg.SCCM_SITE:
            sccm_config = {
                "server": cfg.SCCM_SERVER,
                "site": cfg.SCCM_SITE,
                "api_key": cfg.SCCM_API_KEY,
            }

        installer = AppInstaller(sccm_config=sccm_config)
        outcome = installer.install_or_upgrade(
            software_name=software_name,
            winget_id=override_winget_id,
            already_installed=already_installed,
            installed_version=installed_version,
        )

        if outcome.result == InstallResult.FAILED:
            logger.error("Install failed for '%s': %s", software_name, outcome.detail)
            _emit(InstallResult.FAILED.value, outcome.detail)
            return

        # ── Post-steps (KB-informed: registry tweaks, disable auto-update, etc.) ──
        if post_steps:
            logger.info("Running %d post-install step(s)…", len(post_steps))
            post_results = executor.run_steps(post_steps)
            failed_post = [r for r in post_results if not r.success]
            if failed_post:
                logger.warning(
                    "%d post-step(s) failed (non-fatal): %s",
                    len(failed_post),
                    "; ".join(f"{r.action}:{r.target}" for r in failed_post),
                )

        logger.info("Outcome for '%s': %s — %s", software_name, outcome.result, outcome.detail)
        _emit(outcome.result.value, outcome.detail)

    except Exception as exc:
        logger.exception("Unhandled error during install of '%s'", software_name)
        _emit(InstallResult.FAILED.value, str(exc))


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main():
    cfg = AgentConfig()
    device_id = cfg.device_id()

    use_http_polling = os.environ.get("USE_HTTP_POLLING", "").lower() in ("1", "true", "yes")

    if use_http_polling:
        # Gilligan's Island demo mode — HTTP polling instead of Azure Service Bus
        if not cfg.ORCHESTRATOR_URL:
            logger.critical("ORCHESTRATOR_URL must be set when USE_HTTP_POLLING=true.")
            sys.exit(1)

        logger.info(
            "IT Desktop Agent starting (HTTP polling mode) — device_id=%s, user=%s",
            device_id,
            cfg.USER_EMAIL,
        )

        signing_secret = load_signing_secret()
        verifier = CommandVerifier(signing_secret)
        register_with_orchestrator(cfg, device_id)

        from http_poller import HTTPPoller, HTTPSender
        sender = HTTPSender(cfg.ORCHESTRATOR_URL, cfg.ORCHESTRATOR_API_KEY)
        listener = HTTPPoller(cfg.ORCHESTRATOR_URL, device_id, cfg.ORCHESTRATOR_API_KEY)

    else:
        # Production mode — Azure Service Bus
        if not cfg.SERVICE_BUS_CONNECTION_STRING:
            logger.critical("AGENT_SERVICE_BUS_CONN is not set — agent cannot start.")
            sys.exit(1)

        logger.info(
            "IT Desktop Agent starting (Service Bus mode) — device_id=%s, user=%s",
            device_id,
            cfg.USER_EMAIL,
        )

        signing_secret = load_signing_secret()
        verifier = CommandVerifier(signing_secret)
        register_with_orchestrator(cfg, device_id)

        namespace = cfg.SERVICE_BUS_NAMESPACE
        sender = BusSender(namespace)
        listener = BusListener(
            fully_qualified_namespace=namespace,
            device_id=device_id,
            verifier=verifier,
            orchestrator_url=cfg.ORCHESTRATOR_URL,
            orchestrator_api_key=cfg.ORCHESTRATOR_API_KEY,
        )

    def on_command(raw: dict):
        handle_command(raw, cfg, sender, device_id)

    # Run the listener in a background thread so we can catch KeyboardInterrupt cleanly
    thread = threading.Thread(target=listener.start, args=(on_command,), daemon=True)
    thread.start()

    logger.info("Agent running — waiting for commands.")
    try:
        thread.join()
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
        listener.stop()


if __name__ == "__main__":
    main()
