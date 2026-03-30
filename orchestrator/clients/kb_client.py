"""
KB Client — searches two knowledge sources and returns ranked, deduplicated articles.

Sources:
  1. Subway ServiceNow KB  (internal — kb_knowledge table via REST API)
  2. Internet              (Bing Search API — vendor docs, release notes, community fixes)

Results are passed to LLMAdvisor for synthesis into a remediation plan.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


@dataclass
class KBArticle:
    source: str             # "servicenow" | "internet"
    title: str
    url: str
    snippet: str
    number: str = ""        # ServiceNow article number e.g. KB0012345
    relevance_score: float = 0.0


@dataclass
class KBSearchResult:
    query: str
    articles: list[KBArticle] = field(default_factory=list)

    @property
    def context_text(self) -> str:
        """
        Flattened text block ready to be injected into an LLM prompt.
        """
        if not self.articles:
            return "No knowledge base articles found."
        lines = [f"Knowledge base results for: '{self.query}'\n"]
        for i, a in enumerate(self.articles, 1):
            lines.append(
                f"[{i}] [{a.source.upper()}] {a.title}\n"
                f"    URL/Ref: {a.url or a.number}\n"
                f"    {a.snippet}\n"
            )
        return "\n".join(lines)


class KBClient:
    def __init__(
        self,
        snow_instance: str,
        snow_username: str,
        snow_password: str,
        bing_api_key: str,
        max_snow_results: int = 5,
        max_internet_results: int = 8,
    ):
        self._snow_base = f"https://{snow_instance}.service-now.com/api/now"
        self._snow_auth = HTTPBasicAuth(snow_username, snow_password)
        self._bing_key = bing_api_key
        self._max_snow = max_snow_results
        self._max_internet = max_internet_results

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def search(
        self,
        software_name: str,
        issue_context: str = "",
        platform: str = "Windows 11",
    ) -> KBSearchResult:
        """
        Run both searches in sequence and return a merged, ranked result set.

        Args:
            software_name:  e.g. "Adobe Acrobat Pro"
            issue_context:  optional extra context, e.g. "silent install fails" or "upgrade"
            platform:       OS context to narrow internet results
        """
        query = self._build_query(software_name, issue_context, platform)
        result = KBSearchResult(query=query)

        # 1. Internal Subway KB
        snow_articles = self._search_servicenow(software_name, issue_context)
        result.articles.extend(snow_articles)
        logger.info("ServiceNow KB: %d articles found for '%s'", len(snow_articles), software_name)

        # 2. Internet
        internet_articles = self._search_internet(query)
        result.articles.extend(internet_articles)
        logger.info("Internet: %d results found for '%s'", len(internet_articles), software_name)

        return result

    # ------------------------------------------------------------------
    # ServiceNow KB search
    # ------------------------------------------------------------------

    def _search_servicenow(self, software_name: str, issue_context: str) -> list[KBArticle]:
        """
        Searches the kb_knowledge table using ServiceNow's text search.
        Prioritises articles with matching short_description, falls back to body text.
        """
        search_term = f"{software_name} {issue_context}".strip()
        articles: list[KBArticle] = []

        try:
            # Full-text search via the ServiceNow Table API
            params = {
                "sysparm_query": (
                    f"active=true^workflow_state=published^"
                    f"short_descriptionLIKE{software_name}^"
                    f"ORtextLIKE{software_name}"
                ),
                "sysparm_fields": "number,short_description,text,sys_id,kb_category",
                "sysparm_limit": self._max_snow,
                "sysparm_display_value": "true",
            }
            resp = requests.get(
                f"{self._snow_base}/table/kb_knowledge",
                auth=self._snow_auth,
                headers={"Accept": "application/json"},
                params=params,
                timeout=15,
            )
            resp.raise_for_status()

            for raw in resp.json().get("result", []):
                # Strip HTML tags from body text for a clean snippet
                snippet = _strip_html(raw.get("text", ""))[:400]
                articles.append(
                    KBArticle(
                        source="servicenow",
                        title=raw.get("short_description", "Untitled"),
                        url=f"https://{self._snow_base.split('/')[2]}/kb?id={raw.get('sys_id','')}",
                        snippet=snippet,
                        number=raw.get("number", ""),
                    )
                )

            # Also search past resolved incidents for this software
            # (pattern mining — what fixed it last time?)
            past_fixes = self._search_past_incidents(software_name)
            articles.extend(past_fixes)

        except Exception as exc:
            logger.error("ServiceNow KB search failed: %s", exc)

        return articles

    def _search_past_incidents(self, software_name: str) -> list[KBArticle]:
        """
        Mines closed incidents where the same software was involved
        and extracts the resolution notes. Limits to 3 most recent.
        """
        articles: list[KBArticle] = []
        try:
            params = {
                "sysparm_query": (
                    f"short_descriptionLIKE{software_name}^"
                    f"state=6^"      # Resolved
                    f"ORDERBYDESCsys_created_on"
                ),
                "sysparm_fields": "number,short_description,close_notes,resolved_at",
                "sysparm_limit": 3,
            }
            resp = requests.get(
                f"{self._snow_base}/table/incident",
                auth=self._snow_auth,
                headers={"Accept": "application/json"},
                params=params,
                timeout=15,
            )
            resp.raise_for_status()

            for raw in resp.json().get("result", []):
                notes = raw.get("close_notes", "").strip()
                if not notes:
                    continue
                articles.append(
                    KBArticle(
                        source="servicenow",
                        title=f"Past resolved incident: {raw.get('short_description', '')}",
                        url="",
                        snippet=notes[:400],
                        number=raw.get("number", ""),
                    )
                )
        except Exception as exc:
            logger.warning("Past incident search failed: %s", exc)

        return articles

    # ------------------------------------------------------------------
    # Internet search (Bing Search API)
    # ------------------------------------------------------------------

    def _search_internet(self, query: str) -> list[KBArticle]:
        """
        Queries Bing Web Search API.  Targets vendor docs, release notes,
        IT community forums (reddit.com/r/sysadmin, community.spiceworks.com, etc.)
        """
        if not self._bing_key:
            logger.warning("BING_API_KEY not configured — internet KB search skipped.")
            return []

        articles: list[KBArticle] = []
        try:
            headers = {"Ocp-Apim-Subscription-Key": self._bing_key}
            params = {
                "q": query,
                "count": self._max_internet,
                "responseFilter": "Webpages",
                "safeSearch": "Strict",
                # Bias toward authoritative IT sources
                "site": (
                    "helpx.adobe.com OR "
                    "learn.microsoft.com OR "
                    "community.spiceworks.com OR "
                    "reddit.com/r/sysadmin OR "
                    "reddit.com/r/intune OR "
                    "support.microsoft.com OR "
                    "github.com/microsoft/winget-pkgs"
                ),
            }
            resp = requests.get(
                "https://api.bing.microsoft.com/v7.0/search",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()

            pages = resp.json().get("webPages", {}).get("value", [])
            for page in pages:
                articles.append(
                    KBArticle(
                        source="internet",
                        title=page.get("name", ""),
                        url=page.get("url", ""),
                        snippet=page.get("snippet", "")[:400],
                    )
                )

        except Exception as exc:
            logger.error("Bing search failed: %s", exc)

        return articles

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_query(software_name: str, issue_context: str, platform: str) -> str:
        parts = [software_name, "silent install", platform]
        if issue_context:
            parts.append(issue_context)
        parts.append("enterprise deployment known issues")
        return " ".join(parts)


def _strip_html(text: str) -> str:
    """Minimal HTML tag stripper — avoids a BeautifulSoup dependency."""
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()
