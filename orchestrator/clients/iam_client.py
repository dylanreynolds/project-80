"""
IAM / Licensing client.

For M365-connected tenants this calls:
  - Microsoft Graph API  →  assign an Azure AD group (which triggers licence assignment)
  - Or a direct licence SKU assignment via Graph /users/{id}/assignLicense

Extend this class for Okta, SailPoint, or any IdP your organisation uses.
"""
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# Map of software names → Azure AD licence SKU IDs
# Find yours at: https://learn.microsoft.com/en-us/azure/active-directory/enterprise-users/licensing-service-plan-reference
LICENCE_SKU_MAP: dict[str, str] = {
    "Adobe Acrobat Pro": "f30db892-07e9-47e9-837c-80727f46fd3d",   # placeholder — use your real SKU GUID
    "Power BI": "f8a1db68-be16-40ed-86d5-cb42ce701560",
    "Visio": "c5928f49-12ba-48f7-ada3-0d743a3601d5",
    "AutoCAD": "AUTOCAD_SKU",   # typically managed via vendor portal, not Graph
}

# Software that requires a vendor-side licence call rather than a Graph SKU
VENDOR_MANAGED: set[str] = {"AutoCAD", "Bluebeam", "Matlab", "Tableau"}


class IAMClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: Optional[str] = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def assign_licence(self, user_email: str, software_name: str) -> bool:
        """
        Assign a licence to the user.
        Returns True on success.
        """
        if software_name in VENDOR_MANAGED:
            return self._assign_vendor_licence(user_email, software_name)

        sku_id = LICENCE_SKU_MAP.get(software_name)
        if not sku_id:
            logger.warning("No SKU mapping found for '%s' — skipping licence assignment.", software_name)
            return True   # Treat as success; install can still proceed (e.g. free tool)

        return self._assign_graph_licence(user_email, sku_id, software_name)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        if self._token:
            return self._token
        resp = requests.post(
            f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=15,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def _graph_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def _get_user_id(self, email: str) -> str:
        r = requests.get(
            f"https://graph.microsoft.com/v1.0/users/{email}",
            headers=self._graph_headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["id"]

    def _assign_graph_licence(self, user_email: str, sku_id: str, software_name: str) -> bool:
        try:
            user_id = self._get_user_id(user_email)
            r = requests.post(
                f"https://graph.microsoft.com/v1.0/users/{user_id}/assignLicense",
                headers=self._graph_headers(),
                json={"addLicenses": [{"skuId": sku_id}], "removeLicenses": []},
                timeout=15,
            )
            r.raise_for_status()
            logger.info("Graph licence '%s' assigned to %s", software_name, user_email)
            return True
        except Exception as exc:
            logger.error("Graph licence assignment failed for %s: %s", user_email, exc)
            return False

    def _assign_vendor_licence(self, user_email: str, software_name: str) -> bool:
        """
        Stub for vendor-managed licence assignment (e.g. Adobe Admin Console API,
        Autodesk Manage API, etc.).  Implement per-vendor as required.
        """
        logger.info(
            "[STUB] Vendor-managed licence for '%s' — implement %s portal API here.",
            software_name,
            software_name,
        )
        # Return True to allow the pipeline to continue; real impl should confirm.
        return True
