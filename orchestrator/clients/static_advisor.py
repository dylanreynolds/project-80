"""
Static Advisor + No-Op KB Client — Gilligan's Island demo mode.

Replaces LLMAdvisor (Azure OpenAI) and KBClient (ServiceNow KB + Bing)
with zero-dependency alternatives that produce a reasonable RemediationPlan
without any cloud calls.

StaticAdvisor.build_remediation_plan() delegates to the existing
RemediationPlan.default() so the demo shows the same data structures
a production LLM response would produce — it just skips the API call.
"""
import logging
from dataclasses import dataclass, field
from typing import List

from clients.llm_advisor import RemediationPlan

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# No-op KB client
# ------------------------------------------------------------------

@dataclass
class _EmptyKBResult:
    """Minimal stand-in for KBSearchResult with no articles."""
    articles: list = field(default_factory=list)
    context_text: str = ""


class NoOpKBClient:
    """Drop-in for KBClient — returns an empty result, skipping all network calls."""

    def search(self, software_name: str, issue_context: str = "") -> _EmptyKBResult:
        logger.info(
            "KB search skipped (Gilligan's Island demo mode) for '%s'", software_name
        )
        return _EmptyKBResult()


# ------------------------------------------------------------------
# Static advisor
# ------------------------------------------------------------------

class StaticAdvisor:
    """
    Drop-in for LLMAdvisor — returns RemediationPlan.default() without
    calling Azure OpenAI.

    For the hackathon demo this is indistinguishable from the real advisor
    in terms of the data structures that flow to the agent; the difference
    is just that pre_steps / post_steps will be empty and confidence is 'low'.
    """

    def build_remediation_plan(
        self,
        software_name: str,
        winget_id: str,
        kb_result,           # accepts any KB result type — ignored in demo mode
        issue_context: str = "standard installation",
    ) -> RemediationPlan:
        logger.info(
            "Static advisor generating default plan for '%s' (winget_id=%s) — "
            "skipping LLM call (Gilligan's Island demo mode)",
            software_name,
            winget_id,
        )
        plan = RemediationPlan.default(software_name, winget_id)
        plan.advisor_notes = (
            f"Demo mode: standard silent installation of {software_name} via winget. "
            f"No KB articles consulted (Gilligan's Island environment). "
            f"In production this step queries the Subway KB and Azure OpenAI to generate "
            f"pre/post-install steps tailored to your endpoint fleet."
        )
        plan.kb_sources = ["Gilligan's Island — static advisor (demo mode)"]
        return plan
