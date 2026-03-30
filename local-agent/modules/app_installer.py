"""
App installation and upgrade module (Windows).

Strategy (in order):
  1. winget install / upgrade  — preferred for consumer/commercial software
  2. SCCM (ConfigMgr) AdminService — trigger an existing SCCM application deployment
  3. Fallback: raise InstallError for manual remediation

Touchless principles:
  - All installs use --silent / --quiet flags
  - User-facing UAC prompts are avoided by running the agent as SYSTEM or
    via a scheduled task with elevated privileges (see deployment notes)
  - SSO / Azure AD token pre-seeding handles licence activation where possible
"""
import logging
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class InstallResult(str, Enum):
    SUCCESS = "install_complete"
    UPGRADED = "upgraded"
    ALREADY_CURRENT = "already_installed"
    FAILED = "install_failed"


@dataclass
class InstallOutcome:
    result: InstallResult
    detail: str = ""


class AppInstaller:
    def __init__(self, sccm_config: Optional[dict] = None):
        """
        sccm_config: dict with keys:
          server   — FQDN of the SCCM site server
          site     — Site code, e.g. "PS1"
          api_key  — API key for the ConfigMgr AdminService
        """
        self._sccm = sccm_config

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def install_or_upgrade(
        self,
        software_name: str,
        winget_id: str,
        already_installed: bool = False,
        installed_version: str = "",
    ) -> InstallOutcome:
        """
        Install if not present, upgrade if outdated, skip if current.
        """
        if already_installed:
            return self._try_upgrade(software_name, winget_id, installed_version)
        else:
            return self._try_install(software_name, winget_id)

    # ------------------------------------------------------------------
    # Install path
    # ------------------------------------------------------------------

    def _try_install(self, software_name: str, winget_id: str) -> InstallOutcome:
        if winget_id:
            outcome = self._winget_install(winget_id)
            if outcome.result != InstallResult.FAILED:
                return outcome

        if self._sccm:
            outcome = self._sccm_deploy(software_name)
            if outcome.result != InstallResult.FAILED:
                return outcome

        return InstallOutcome(
            result=InstallResult.FAILED,
            detail=f"All install strategies exhausted for '{software_name}'.",
        )

    # ------------------------------------------------------------------
    # Upgrade path
    # ------------------------------------------------------------------

    def _try_upgrade(self, software_name: str, winget_id: str, current_version: str) -> InstallOutcome:
        if winget_id:
            outcome = self._winget_upgrade(winget_id, current_version)
            if outcome.result != InstallResult.FAILED:
                return outcome
        # If upgrade fails, that's still non-critical — app is already installed
        return InstallOutcome(
            result=InstallResult.ALREADY_CURRENT,
            detail=f"{software_name} is installed (v{current_version}); upgrade attempted.",
        )

    # ------------------------------------------------------------------
    # winget helpers
    # ------------------------------------------------------------------

    def _winget_install(self, winget_id: str) -> InstallOutcome:
        logger.info("winget install: %s", winget_id)
        cmd = [
            "winget", "install",
            "--id", winget_id,
            "--exact",
            "--silent",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--disable-interactivity",
        ]
        return self._run_winget(cmd, winget_id, success_result=InstallResult.SUCCESS)

    def _winget_upgrade(self, winget_id: str, current_version: str) -> InstallOutcome:
        logger.info("winget upgrade: %s (currently %s)", winget_id, current_version)
        cmd = [
            "winget", "upgrade",
            "--id", winget_id,
            "--exact",
            "--silent",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--disable-interactivity",
        ]
        return self._run_winget(cmd, winget_id, success_result=InstallResult.UPGRADED)

    def _run_winget(self, cmd: list[str], winget_id: str, success_result: InstallResult) -> InstallOutcome:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,   # 10 min max for large installers
            )
            stdout = proc.stdout + proc.stderr
            logger.debug("winget output: %s", stdout[:2000])

            if proc.returncode == 0:
                return InstallOutcome(result=success_result)

            # winget exit codes
            # 0x8A15002B = no applicable upgrade (already current)
            if proc.returncode in (0x8A15002B, -1978335189):
                return InstallOutcome(result=InstallResult.ALREADY_CURRENT, detail="Already at latest version.")

            return InstallOutcome(
                result=InstallResult.FAILED,
                detail=f"winget exited {proc.returncode}: {stdout[:500]}",
            )
        except FileNotFoundError:
            return InstallOutcome(result=InstallResult.FAILED, detail="winget not found on this device.")
        except subprocess.TimeoutExpired:
            return InstallOutcome(result=InstallResult.FAILED, detail="winget install timed out.")
        except Exception as exc:
            return InstallOutcome(result=InstallResult.FAILED, detail=str(exc))

    # ------------------------------------------------------------------
    # SCCM (ConfigMgr AdminService) fallback
    # ------------------------------------------------------------------

    def _sccm_deploy(self, software_name: str) -> InstallOutcome:
        """
        Trigger an existing SCCM application deployment via the AdminService REST API.
        The application must already exist in SCCM and be targeted to the device/collection.
        """
        if not self._sccm:
            return InstallOutcome(result=InstallResult.FAILED, detail="No SCCM config provided.")

        server = self._sccm["server"]
        site = self._sccm["site"]
        api_key = self._sccm.get("api_key", "")

        try:
            # 1. Find the application CI_ID by name
            search_url = (
                f"https://{server}/AdminService/wmi/SMS_Application"
                f"?$filter=LocalizedDisplayName eq '{software_name}' and IsLatest eq true"
            )
            resp = requests.get(
                search_url,
                headers={"Authorization": f"Bearer {api_key}"},
                verify=False,   # Internal CA — configure properly in production
                timeout=30,
            )
            resp.raise_for_status()
            apps = resp.json().get("value", [])
            if not apps:
                return InstallOutcome(
                    result=InstallResult.FAILED,
                    detail=f"No SCCM application found matching '{software_name}'.",
                )
            ci_id = apps[0]["CI_ID"]

            # 2. Trigger install on this device
            install_url = f"https://{server}/AdminService/v1.0/Device"
            resp = requests.post(
                install_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"Action": "InstallApplication", "ApplicationId": ci_id},
                verify=False,
                timeout=30,
            )
            resp.raise_for_status()
            logger.info("SCCM deployment triggered for '%s' (CI_ID=%s)", software_name, ci_id)

            # 3. Poll for completion (up to 20 minutes)
            return self._poll_sccm_install(server, api_key, ci_id, software_name)

        except Exception as exc:
            return InstallOutcome(result=InstallResult.FAILED, detail=f"SCCM error: {exc}")

    def _poll_sccm_install(self, server: str, api_key: str, ci_id: int, software_name: str) -> InstallOutcome:
        """Poll SCCM for install state changes (max 20 min, 30 s interval)."""
        deadline = time.time() + 1200
        while time.time() < deadline:
            time.sleep(30)
            try:
                url = f"https://{server}/AdminService/wmi/SMS_AppDeploymentAssetDetails?$filter=CIID eq {ci_id}"
                resp = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, verify=False, timeout=20)
                for record in resp.json().get("value", []):
                    enforcement_state = record.get("EnforcementState", 0)
                    if enforcement_state == 1000:   # Success
                        return InstallOutcome(result=InstallResult.SUCCESS)
                    if enforcement_state in (1001, 1002, 1003):   # Error states
                        return InstallOutcome(
                            result=InstallResult.FAILED,
                            detail=f"SCCM enforcement state: {enforcement_state}",
                        )
            except Exception as exc:
                logger.warning("SCCM poll error: %s", exc)

        return InstallOutcome(result=InstallResult.FAILED, detail="SCCM install timed out after 20 minutes.")
