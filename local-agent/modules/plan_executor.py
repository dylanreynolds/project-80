"""
Plan Executor — runs the pre/post steps from a validated RemediationPlan.

Security model:
  This module is the LAST line of defence before a step touches the OS.
  Even though the orchestrator's plan_validator.py already checked the plan,
  the executor enforces its own independent allowlist (defence in depth).
  If a step arrives here with a forbidden action it is hard-rejected and logged
  as a security event — it should never happen if the pipeline is intact, so
  if it does it means something in the chain has been bypassed.

  run_script is PERMANENTLY BLOCKED here regardless of any flag or override.
  Arbitrary code execution from LLM-derived plans will never be permitted.

Supported actions (allowlist enforced at runtime):
  Pre-install:
    uninstall       — silently uninstall a conflicting app via winget or msiexec
    registry_clean  — delete a registry key/value known to cause install failures
    kill_process    — terminate a running process that locks installer files
    disable_service — stop + disable a Windows service before install

  Post-install:
    registry_set    — write a registry value (e.g. disable auto-update)
    disable_service — stop + disable a service created by the installer
    verify_path     — assert an executable exists at a path (install smoke test)
    reboot_schedule — schedule a reboot via Task Scheduler at a quiet time

  PERMANENTLY BLOCKED:
    run_script      — arbitrary code execution is never permitted from LLM plans
"""
import logging
import subprocess
import winreg
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    action: str
    target: str
    success: bool
    detail: str = ""


# Actions the executor will run. run_script is absent — permanently blocked.
_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "uninstall",
    "registry_clean",
    "registry_set",
    "kill_process",
    "disable_service",
    "verify_path",
    "reboot_schedule",
})


class PlanExecutor:

    def run_steps(self, steps: list[dict]) -> list[StepResult]:
        """
        Execute a list of step dicts (from RemediationPlan.pre_steps / post_steps).
        Each step is checked against the local allowlist before execution.
        """
        results: list[StepResult] = []
        for step in steps:
            action = step.get("action", "")
            target = step.get("target", "")
            args: dict = step.get("args", {})
            description = step.get("description", f"{action}: {target}")

            # ── Local security gate (independent of orchestrator validation) ──
            if action == "run_script":
                logger.critical(
                    "SECURITY: run_script step reached the executor — this should never happen. "
                    "Step blocked. Possible pipeline bypass. target=%r", target[:120]
                )
                results.append(StepResult(
                    action=action, target=target, success=False,
                    detail="SECURITY BLOCK: run_script is permanently forbidden in plan executor."
                ))
                continue

            if action not in _ALLOWED_ACTIONS:
                logger.error(
                    "SECURITY: Unknown/blocked action '%s' in plan step — rejected.", action
                )
                results.append(StepResult(
                    action=action, target=target, success=False,
                    detail=f"SECURITY BLOCK: action '{action}' is not on the executor allowlist."
                ))
                continue

            logger.info("Executing step: %s", description)
            result = self._dispatch(action, target, args)
            results.append(result)

            if result.success:
                logger.info("  ✅ %s", description)
            else:
                logger.warning("  ⚠️ Step failed (non-fatal): %s — %s", description, result.detail)

        return results

    # ------------------------------------------------------------------
    # Dispatcher — only routes to allowed handlers
    # ------------------------------------------------------------------

    def _dispatch(self, action: str, target: str, args: dict) -> StepResult:
        handlers = {
            "uninstall": self._uninstall,
            "registry_clean": self._registry_clean,
            "registry_set": self._registry_set,
            "kill_process": self._kill_process,
            "disable_service": self._disable_service,
            "verify_path": self._verify_path,
            "reboot_schedule": self._reboot_schedule,
            # run_script is intentionally absent
        }
        handler = handlers.get(action)
        if not handler:
            return StepResult(action=action, target=target, success=False, detail=f"No handler for action: {action}")
        try:
            return handler(target, args)
        except Exception as exc:
            return StepResult(action=action, target=target, success=False, detail=str(exc))

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------

    def _uninstall(self, target: str, args: dict) -> StepResult:
        """Silently uninstall an app by winget ID or display name."""
        winget_id = args.get("winget_id", target)
        try:
            proc = subprocess.run(
                [
                    "winget", "uninstall",
                    "--id", winget_id,
                    "--exact",
                    "--silent",
                    "--accept-source-agreements",
                    "--disable-interactivity",
                ],
                capture_output=True, text=True, timeout=300,
            )
            if proc.returncode == 0:
                return StepResult(action="uninstall", target=target, success=True)
            # Exit code 0x8A15002B = not found — treat as success (already gone)
            if proc.returncode in (0x8A15002B, -1978335189):
                return StepResult(action="uninstall", target=target, success=True,
                                  detail="Not present — skipped.")
            return StepResult(action="uninstall", target=target, success=False,
                              detail=f"winget exit {proc.returncode}: {proc.stderr[:200]}")
        except FileNotFoundError:
            # Fall back to msiexec if winget not available
            return self._msiexec_uninstall(target, args)

    def _msiexec_uninstall(self, target: str, args: dict) -> StepResult:
        product_code = args.get("product_code", "")
        if not product_code:
            return StepResult(action="uninstall", target=target, success=False,
                              detail="No product_code provided for msiexec fallback.")
        proc = subprocess.run(
            ["msiexec", "/x", product_code, "/qn", "/norestart"],
            capture_output=True, timeout=300,
        )
        ok = proc.returncode in (0, 1605)   # 1605 = product not installed
        return StepResult(action="uninstall", target=target, success=ok,
                          detail="" if ok else f"msiexec exit {proc.returncode}")

    def _registry_clean(self, target: str, args: dict) -> StepResult:
        """Delete a registry key or value known to block installation."""
        hive_map = {
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKCU": winreg.HKEY_CURRENT_USER,
            "HKCR": winreg.HKEY_CLASSES_ROOT,
        }
        # target format: "HKLM\\SOFTWARE\\Adobe\\Acrobat Reader DC"
        parts = target.split("\\", 1)
        hive_str = parts[0].upper()
        key_path = parts[1] if len(parts) > 1 else ""
        value_name = args.get("value_name")   # if None, delete the whole key

        hive = hive_map.get(hive_str)
        if not hive:
            return StepResult(action="registry_clean", target=target, success=False,
                              detail=f"Unknown hive: {hive_str}")
        try:
            if value_name:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, value_name)
            else:
                _delete_registry_key_recursive(hive, key_path)
            return StepResult(action="registry_clean", target=target, success=True)
        except FileNotFoundError:
            return StepResult(action="registry_clean", target=target, success=True,
                              detail="Key not present — nothing to clean.")
        except Exception as exc:
            return StepResult(action="registry_clean", target=target, success=False, detail=str(exc))

    def _registry_set(self, target: str, args: dict) -> StepResult:
        """Write a registry value (e.g. to disable auto-update)."""
        hive_map = {
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKCU": winreg.HKEY_CURRENT_USER,
        }
        parts = target.split("\\", 1)
        hive = hive_map.get(parts[0].upper(), winreg.HKEY_LOCAL_MACHINE)
        key_path = parts[1] if len(parts) > 1 else ""
        value_name = args.get("value_name", "")
        value_data = args.get("value_data", 0)
        value_type_str = args.get("value_type", "DWORD").upper()
        type_map = {
            "DWORD": winreg.REG_DWORD,
            "SZ": winreg.REG_SZ,
            "EXPAND_SZ": winreg.REG_EXPAND_SZ,
            "BINARY": winreg.REG_BINARY,
        }
        reg_type = type_map.get(value_type_str, winreg.REG_DWORD)
        try:
            with winreg.CreateKeyEx(hive, key_path, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, value_name, 0, reg_type, value_data)
            return StepResult(action="registry_set", target=target, success=True)
        except Exception as exc:
            return StepResult(action="registry_set", target=target, success=False, detail=str(exc))

    def _kill_process(self, target: str, args: dict) -> StepResult:
        """Terminate a process by name (e.g. 'AcroRd32.exe')."""
        proc = subprocess.run(
            ["taskkill", "/F", "/IM", target],
            capture_output=True, text=True,
        )
        # taskkill returns 128 if process not found — that's fine
        ok = proc.returncode in (0, 128)
        return StepResult(action="kill_process", target=target, success=ok,
                          detail="" if ok else proc.stderr[:200])

    def _disable_service(self, target: str, args: dict) -> StepResult:
        """Stop and disable a Windows service."""
        try:
            subprocess.run(["sc", "stop", target], capture_output=True, timeout=30)
            proc = subprocess.run(["sc", "config", target, "start=", "disabled"],
                                  capture_output=True, text=True, timeout=15)
            return StepResult(action="disable_service", target=target,
                              success=proc.returncode == 0,
                              detail="" if proc.returncode == 0 else proc.stderr[:200])
        except Exception as exc:
            return StepResult(action="disable_service", target=target, success=False, detail=str(exc))

    def _verify_path(self, target: str, args: dict) -> StepResult:
        """Assert that an executable exists at the expected path after install."""
        import os
        exists = os.path.exists(target)
        return StepResult(action="verify_path", target=target, success=exists,
                          detail="" if exists else f"Path not found: {target}")

    def _reboot_schedule(self, target: str, args: dict) -> StepResult:
        """
        Schedule a one-time reboot via Task Scheduler.
        `target` = task name, args['time'] = HH:MM (24h, local time), default 23:00.
        """
        time_str = args.get("time", "23:00")
        try:
            proc = subprocess.run(
                [
                    "schtasks", "/Create", "/F",
                    "/TN", target,
                    "/TR", "shutdown /r /t 60 /c \"IT scheduled reboot for software update\"",
                    "/SC", "ONCE",
                    "/ST", time_str,
                    "/RU", "SYSTEM",
                ],
                capture_output=True, text=True, timeout=15,
            )
            return StepResult(action="reboot_schedule", target=target,
                              success=proc.returncode == 0,
                              detail=f"Reboot scheduled for {time_str}" if proc.returncode == 0 else proc.stderr[:200])
        except Exception as exc:
            return StepResult(action="reboot_schedule", target=target, success=False, detail=str(exc))


# ------------------------------------------------------------------
# Registry key recursive delete helper
# ------------------------------------------------------------------

def _delete_registry_key_recursive(hive, key_path: str) -> None:
    """Recursively delete a registry key and all its subkeys."""
    try:
        with winreg.OpenKey(hive, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    subkey = winreg.EnumKey(key, 0)
                    _delete_registry_key_recursive(hive, f"{key_path}\\{subkey}")
                except OSError:
                    break
        winreg.DeleteKey(hive, key_path)
    except FileNotFoundError:
        pass
