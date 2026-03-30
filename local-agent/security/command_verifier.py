"""
Command Verifier — local agent side.

Before the agent acts on ANY InstallCommand it must pass three checks:

  1. HMAC-SHA256 signature verification  — proves the message came from the
     legitimate orchestrator and has not been tampered with in transit.

  2. Timestamp freshness check           — rejects replayed messages older than
     5 minutes, preventing an attacker from re-sending a captured command.

  3. Device ID binding                   — the command must target THIS device.
     Even if an attacker forges a valid command (impossible without the secret,
     but belt-and-braces), it cannot be replayed on a different machine.

Secret storage on the endpoint:
  The signing secret is stored in the Windows Credential Manager (DPAPI-encrypted)
  under the target "ITAgentSigningSecret".  It is written there at Intune enrolment
  time by a provisioning script that fetches it from Key Vault using the device's
  Azure AD identity.  It is NEVER stored in plain text, the registry, or a file.

  For local development only, set COMMAND_SIGNING_SECRET in the environment.
"""
import hashlib
import hmac
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

TIMESTAMP_TOLERANCE_SECONDS = 300
CREDENTIAL_TARGET = "ITAgentSigningSecret"


class CommandVerifier:
    def __init__(self, signing_secret: str):
        if not signing_secret:
            raise ValueError("CommandVerifier: signing_secret must not be empty.")
        self._secret = signing_secret.encode("utf-8")

    def verify(self, command: dict, expected_device_id: str) -> tuple[bool, str]:
        """
        Full verification pipeline.
        Returns (True, "") on success, (False, reason) on any failure.

        The agent MUST call this before executing any step in the command.
        """
        # 1. Presence checks
        sig_received = command.get("_sig")
        ts = command.get("_ts")

        if not sig_received:
            return False, "SECURITY: Missing _sig — command rejected"
        if not ts:
            return False, "SECURITY: Missing _ts — command rejected"

        # 2. Timestamp freshness (replay protection)
        try:
            age_seconds = int(time.time()) - int(ts)
        except (ValueError, TypeError):
            return False, "SECURITY: Invalid _ts format — command rejected"

        if age_seconds > TIMESTAMP_TOLERANCE_SECONDS:
            return False, f"SECURITY: Command is {age_seconds}s old — replay attack rejected"
        if age_seconds < -60:
            return False, f"SECURITY: Command timestamp is in the future ({age_seconds}s) — rejected"

        # 3. Device ID binding
        cmd_device_id = command.get("device_id", "")
        if cmd_device_id != expected_device_id:
            return False, (
                f"SECURITY: Command targets device '{cmd_device_id}' "
                f"but this agent is '{expected_device_id}' — rejected"
            )

        # 4. HMAC verification (constant-time)
        to_verify = {k: v for k, v in command.items() if k != "_sig"}
        body_bytes = _canonical_json(to_verify)
        expected_sig = hmac.new(self._secret, body_bytes, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_sig, sig_received):
            return False, "SECURITY: Signature mismatch — command rejected (possible tampering or spoofed source)"

        return True, ""


# ------------------------------------------------------------------
# Secret loading — Windows Credential Manager (DPAPI)
# ------------------------------------------------------------------

def load_signing_secret() -> str:
    """
    Load the signing secret from Windows Credential Manager.
    Falls back to environment variable for local dev only.
    """
    # Dev fallback
    env_secret = os.environ.get("COMMAND_SIGNING_SECRET", "")
    if env_secret:
        logger.warning(
            "COMMAND_SIGNING_SECRET loaded from environment variable. "
            "This is ONLY acceptable in local development — never in production."
        )
        return env_secret

    # Production: Windows Credential Manager via keyring
    try:
        import keyring
        secret = keyring.get_password(CREDENTIAL_TARGET, "agent")
        if secret:
            logger.info("Signing secret loaded from Windows Credential Manager.")
            return secret
        raise ValueError("Secret not found in Credential Manager")
    except ImportError:
        raise RuntimeError(
            "keyring package not installed. "
            "Install it with: pip install keyring"
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load signing secret from Credential Manager "
            f"(target='{CREDENTIAL_TARGET}'): {exc}\n\n"
            "Run the Intune enrolment provisioning script to store the secret."
        ) from exc


def store_signing_secret(secret: str) -> None:
    """
    Called once by the Intune enrolment script after fetching the secret from Key Vault.
    Stores it in DPAPI-encrypted Windows Credential Manager.
    """
    import keyring
    keyring.set_password(CREDENTIAL_TARGET, "agent", secret)
    logger.info("Signing secret stored in Windows Credential Manager.")


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _canonical_json(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
