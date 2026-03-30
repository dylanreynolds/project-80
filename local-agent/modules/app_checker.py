"""
App detection module (Windows).

Checks whether a given application is installed and returns the installed version.
Detection strategies (in priority order):
  1. winget list (most reliable for winget-managed apps)
  2. Windows Registry uninstall keys (HKLM + HKCU, 32-bit + 64-bit hives)
  3. WMI Win32_Product (slow — last resort)
"""
import logging
import re
import subprocess
import winreg
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

UNINSTALL_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE,  r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER,   r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]


@dataclass
class AppInfo:
    name: str
    version: str
    install_location: str = ""
    source: str = ""    # "winget" | "registry" | "wmi"


def check_installed(software_name: str, winget_id: str = "") -> Optional[AppInfo]:
    """
    Returns AppInfo if the software is installed, None otherwise.
    """
    # 1. Try winget
    if winget_id:
        result = _check_winget(winget_id)
        if result:
            return result

    # 2. Try registry (name-based fuzzy match)
    result = _check_registry(software_name)
    if result:
        return result

    logger.debug("'%s' not detected on this device.", software_name)
    return None


def get_winget_available_version(winget_id: str) -> Optional[str]:
    """Returns the latest version available in winget for this package ID."""
    try:
        proc = subprocess.run(
            ["winget", "show", "--id", winget_id, "--exact", "--accept-source-agreements"],
            capture_output=True, text=True, timeout=30,
        )
        for line in proc.stdout.splitlines():
            if line.strip().lower().startswith("version:"):
                return line.split(":", 1)[1].strip()
    except Exception as exc:
        logger.warning("winget show failed for '%s': %s", winget_id, exc)
    return None


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _check_winget(winget_id: str) -> Optional[AppInfo]:
    try:
        proc = subprocess.run(
            ["winget", "list", "--id", winget_id, "--exact", "--accept-source-agreements"],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode == 0 and winget_id.lower() in proc.stdout.lower():
            version = _parse_winget_list_version(proc.stdout, winget_id)
            logger.info("winget: '%s' found, version=%s", winget_id, version)
            return AppInfo(name=winget_id, version=version or "unknown", source="winget")
    except FileNotFoundError:
        logger.warning("winget not available on this device.")
    except subprocess.TimeoutExpired:
        logger.warning("winget list timed out.")
    except Exception as exc:
        logger.error("winget check error: %s", exc)
    return None


def _parse_winget_list_version(output: str, winget_id: str) -> str:
    """Extract version column from winget list tabular output."""
    for line in output.splitlines():
        if winget_id.lower() in line.lower():
            # winget list columns are whitespace-separated; version is usually the 3rd token
            parts = line.split()
            if len(parts) >= 3:
                return parts[2]
    return "unknown"


def _check_registry(software_name: str) -> Optional[AppInfo]:
    name_lower = software_name.lower()
    for hive, key_path in UNINSTALL_KEYS:
        try:
            with winreg.OpenKey(hive, key_path) as base_key:
                count = winreg.QueryInfoKey(base_key)[0]
                for i in range(count):
                    sub_name = winreg.EnumKey(base_key, i)
                    try:
                        with winreg.OpenKey(base_key, sub_name) as sub_key:
                            display_name = _reg_value(sub_key, "DisplayName") or ""
                            if name_lower in display_name.lower():
                                version = _reg_value(sub_key, "DisplayVersion") or "unknown"
                                location = _reg_value(sub_key, "InstallLocation") or ""
                                logger.info("Registry: '%s' found (key=%s), version=%s", software_name, sub_name, version)
                                return AppInfo(
                                    name=display_name,
                                    version=version,
                                    install_location=location,
                                    source="registry",
                                )
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _reg_value(key, name: str) -> Optional[str]:
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return str(value)
    except OSError:
        return None
