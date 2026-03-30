"""
Gilligan's Island IAM adapter — orchestrator side.

Drop-in replacement for clients/iam_client.py.

For the demo we don't need real Azure AD licence assignment — we just confirm
that the licence is "assigned" (always True) and optionally log the user
profile fetched from Gilligan's Island's /api/users endpoint.
"""
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class GilliganIAMAdapter:
    """Implements the same interface as IAMClient, backed by Gilligan's Island."""

    def __init__(self, gilligan_base_url: str):
        self._base = gilligan_base_url.rstrip("/")

    def assign_licence(self, user_email: str, software_name: str) -> bool:
        """
        Always returns True for the demo.
        Attempts to pull and log the matching user from Gilligan's Island so
        the demo shows a realistic user-lookup step.
        """
        user = self._find_user(user_email)
        if user:
            logger.info(
                "Licence assignment [DEMO] — user: %s (%s), dept: %s, software: %s",
                user.get("displayName", user_email),
                user_email,
                user.get("department", "Unknown"),
                software_name,
            )
        else:
            logger.info(
                "Licence assignment [DEMO] — user %s not in Gilligan's Island, "
                "proceeding anyway. Software: %s",
                user_email,
                software_name,
            )
        return True

    def get_user(self, user_email: str) -> Optional[dict]:
        """Return the Gilligan's Island user profile for the given email, or None."""
        return self._find_user(user_email)

    # ------------------------------------------------------------------

    def _find_user(self, user_email: str) -> Optional[dict]:
        try:
            r = requests.get(f"{self._base}/api/users", timeout=10)
            r.raise_for_status()
            users = r.json() if isinstance(r.json(), list) else r.json().get("users", [])
            email_lower = user_email.lower()
            for u in users:
                upn = (u.get("userPrincipalName") or u.get("email") or "").lower()
                if upn == email_lower:
                    return u
        except Exception as exc:
            logger.warning("Gilligan's Island user lookup failed (non-fatal): %s", exc)
        return None
