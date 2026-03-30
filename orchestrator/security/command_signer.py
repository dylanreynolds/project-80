"""
Command Signer — orchestrator side.

Every InstallCommand is signed with HMAC-SHA256 before it is placed on the
Service Bus.  The local agent re-derives the same signature and rejects any
message where it doesn't match, making it impossible to inject a forged command
even if an attacker gains read access to the Service Bus topic.

Key management:
  - The signing secret lives ONLY in Azure Key Vault.
  - The orchestrator retrieves it at startup via Managed Identity (no secret in
    env vars or config files).
  - The local agent retrieves its copy at enrolment time and stores it in the
    Windows DPAPI-encrypted credential store — never in plain text on disk.
  - Secret rotation: Key Vault supports versioned secrets; rotate every 90 days.
    Both services must fetch the current + previous version during the rotation window.

Signing scope:
  The signature covers the canonical JSON of the command body (fields sorted,
  excluding the '_sig' field itself).  This prevents field-reordering attacks
  and ensures the signature is stable across serialisation libraries.
"""
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Timestamp tolerance — reject messages older than this many seconds.
# Prevents replay attacks where a captured message is re-sent later.
TIMESTAMP_TOLERANCE_SECONDS = 300   # 5 minutes


class CommandSigner:
    def __init__(self, signing_secret: str):
        if not signing_secret:
            raise ValueError("CommandSigner: signing_secret must not be empty.")
        self._secret = signing_secret.encode("utf-8")

    # ------------------------------------------------------------------
    # Sign
    # ------------------------------------------------------------------

    def sign(self, command: dict) -> dict:
        """
        Returns a new dict with '_sig' and '_ts' fields added.
        Original dict is not mutated.
        """
        signed = dict(command)
        signed["_ts"] = int(time.time())   # Unix timestamp — included in signed body
        signed.pop("_sig", None)           # Remove any existing sig before signing

        body_bytes = _canonical_json(signed)
        sig = hmac.new(self._secret, body_bytes, hashlib.sha256).hexdigest()
        signed["_sig"] = sig
        return signed

    # ------------------------------------------------------------------
    # Verify (shared logic — used by both orchestrator tests and agent)
    # ------------------------------------------------------------------

    def verify(self, command: dict) -> tuple[bool, str]:
        """
        Returns (True, "") if the signature is valid and the timestamp is fresh.
        Returns (False, reason) otherwise.  Always use constant-time comparison.
        """
        sig_received = command.get("_sig")
        ts = command.get("_ts")

        if not sig_received:
            return False, "Missing _sig field"

        if not ts:
            return False, "Missing _ts field"

        # Timestamp freshness check
        age = int(time.time()) - int(ts)
        if age > TIMESTAMP_TOLERANCE_SECONDS or age < -60:
            return False, f"Timestamp out of tolerance: age={age}s"

        # Recompute signature over everything except _sig
        to_verify = {k: v for k, v in command.items() if k != "_sig"}
        body_bytes = _canonical_json(to_verify)
        expected_sig = hmac.new(self._secret, body_bytes, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_sig, sig_received):
            return False, "Signature mismatch — command rejected"

        return True, ""


# ------------------------------------------------------------------
# Key Vault retrieval helper
# ------------------------------------------------------------------

def load_signing_secret_from_keyvault(vault_url: str, secret_name: str) -> str:
    """
    Fetch the signing secret from Azure Key Vault using Managed Identity.
    Falls back to COMMAND_SIGNING_SECRET env var for local development only.
    """
    # Local dev fallback — NEVER set this in production
    env_secret = os.environ.get("COMMAND_SIGNING_SECRET", "")
    if env_secret:
        logger.warning(
            "Using COMMAND_SIGNING_SECRET from environment — "
            "this is only acceptable in local development."
        )
        return env_secret

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_url, credential=credential)
        secret = client.get_secret(secret_name)
        logger.info("Signing secret loaded from Key Vault: %s", secret_name)
        return secret.value
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load signing secret from Key Vault ({vault_url}/{secret_name}): {exc}"
        ) from exc


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _canonical_json(obj: dict) -> bytes:
    """Stable, sorted JSON encoding — same output regardless of insertion order."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
