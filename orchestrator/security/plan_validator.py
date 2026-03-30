"""
Plan Validator — orchestrator side.

Validates every RemediationPlan the LLM produces BEFORE it is signed and
placed on the Service Bus.  Acts as a hard security gate between the LLM
(which processed untrusted KB content) and the agent (which runs as SYSTEM).

Defence-in-depth layers applied here:
  1. Strict Pydantic schema — unknown fields are rejected.
  2. Action allowlist — only pre-approved action types are permitted.
  3. run_script BLOCKED — arbitrary code execution is never allowed from LLM output.
  4. Registry path prefix allowlist — prevents writes to sensitive system hives.
  5. Uninstall target allowlist — can only uninstall from the approved software catalogue.
  6. Process name validation — kill_process target must look like a .exe name, not a path.
  7. Step count cap — LLM cannot generate an unbounded number of steps.
  8. String length limits — prevents embedded payloads in long strings.

If any validation fails the plan is REPLACED with the safe default (standard
silent install, no pre/post steps).  The failure is logged and added to the
ServiceNow ticket so a human can review it.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Allowlists — edit these as your software catalogue grows
# ------------------------------------------------------------------

ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "uninstall",
    "registry_clean",
    "registry_set",
    "kill_process",
    "disable_service",
    "verify_path",
    "reboot_schedule",
    # "run_script" is intentionally ABSENT — never allowed from LLM output
})

# Only these registry root paths may be targeted by registry_clean / registry_set.
# This prevents the LLM from being tricked into touching security-critical hives.
ALLOWED_REGISTRY_PREFIXES: tuple[str, ...] = (
    "HKLM\\SOFTWARE\\Adobe\\",
    "HKLM\\SOFTWARE\\Autodesk\\",
    "HKLM\\SOFTWARE\\Microsoft\\Office\\",
    "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\",
    "HKLM\\SOFTWARE\\Policies\\Adobe\\",
    "HKLM\\SOFTWARE\\Policies\\Microsoft\\",
    "HKLM\\SOFTWARE\\Tableau\\",
    "HKLM\\SOFTWARE\\TechSmith\\",
    "HKLM\\SOFTWARE\\Zoom\\",
    "HKCU\\SOFTWARE\\Adobe\\",
    "HKCU\\SOFTWARE\\Microsoft\\Office\\",
    "HKCU\\SOFTWARE\\Zoom\\",
)

# Uninstall is only permitted for software in the approved catalogue
APPROVED_SOFTWARE_WINGET_IDS: frozenset[str] = frozenset({
    "Adobe.Acrobat.Pro.64-bit",
    "Adobe.Acrobat.Reader.64-bit",
    "Adobe.CreativeCloud",
    "Autodesk.AutoCAD",
    "Bluebeam.Revu",
    "Microsoft.PowerBI",
    "Microsoft.VisioViewer",
    "Microsoft.Visio.Professional",
    "SlackTechnologies.Slack",
    "TechSmith.Snagit",
    "TechSmith.Camtasia",
    "Tableau.Desktop",
    "Zoom.Zoom",
})

MAX_STEPS = 8          # Total pre + post steps
MAX_STRING_LEN = 512   # Max length for any string field


# ------------------------------------------------------------------
# Pydantic models — strict schema
# ------------------------------------------------------------------

class StepModel(BaseModel):
    model_config = {"extra": "forbid"}   # Reject unknown fields

    action: str = Field(max_length=50)
    target: str = Field(max_length=MAX_STRING_LEN)
    args: dict = Field(default_factory=dict)
    description: str = Field(default="", max_length=MAX_STRING_LEN)

    @field_validator("action")
    @classmethod
    def action_must_be_allowed(cls, v: str) -> str:
        if v not in ALLOWED_ACTIONS:
            raise ValueError(
                f"Action '{v}' is not on the allowed list. "
                f"run_script and unknown actions are never permitted from LLM output."
            )
        return v

    @field_validator("args")
    @classmethod
    def args_must_be_safe(cls, v: dict) -> dict:
        # Prevent deeply nested or very large args dicts
        if len(str(v)) > 1024:
            raise ValueError("args dict exceeds maximum allowed size")
        return v


class PlanModel(BaseModel):
    model_config = {"extra": "forbid"}

    software_name: str = Field(max_length=200)
    strategy: Literal["winget", "sccm", "manual"]
    winget_id: str = Field(default="", max_length=200)
    winget_override_flags: list[str] = Field(default_factory=list, max_length=10)
    pre_steps: list[StepModel] = Field(default_factory=list)
    post_steps: list[StepModel] = Field(default_factory=list)
    known_issues: list[str] = Field(default_factory=list, max_length=20)
    kb_sources: list[str] = Field(default_factory=list, max_length=20)
    confidence: Literal["high", "medium", "low"] = "medium"
    advisor_notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_step_count(self) -> "PlanModel":
        total = len(self.pre_steps) + len(self.post_steps)
        if total > MAX_STEPS:
            raise ValueError(f"Total steps ({total}) exceeds maximum allowed ({MAX_STEPS})")
        return self

    @field_validator("winget_override_flags")
    @classmethod
    def flags_must_be_safe(cls, flags: list[str]) -> list[str]:
        # Prevent injection through winget flags
        for flag in flags:
            if len(flag) > 100 or any(c in flag for c in [";", "&", "|", "`", "$", "("]):
                raise ValueError(f"Unsafe winget flag: {flag!r}")
        return flags


# ------------------------------------------------------------------
# Post-schema semantic validation
# ------------------------------------------------------------------

@dataclass
class ValidationResult:
    valid: bool
    violations: list[str] = field(default_factory=list)


def validate_plan(plan_dict: dict, software_name: str) -> ValidationResult:
    """
    Full validation pipeline.  Returns ValidationResult with all violations listed.
    Callers should replace the plan with the safe default if valid=False.
    """
    violations: list[str] = []

    # Layer 1: Pydantic schema
    try:
        model = PlanModel(**plan_dict)
    except Exception as exc:
        violations.append(f"Schema validation failed: {exc}")
        return ValidationResult(valid=False, violations=violations)

    # Layer 2: Semantic checks on each step
    all_steps = list(model.pre_steps) + list(model.post_steps)
    for i, step in enumerate(all_steps):
        label = f"Step[{i}] {step.action}:{step.target[:60]}"

        # Registry path checks
        if step.action in ("registry_clean", "registry_set"):
            if not _is_allowed_registry_path(step.target):
                violations.append(
                    f"{label} — registry target is outside the approved path list. "
                    f"Allowed prefixes: {ALLOWED_REGISTRY_PREFIXES}"
                )

        # Uninstall target must be in approved catalogue
        if step.action == "uninstall":
            winget_target = step.args.get("winget_id", step.target)
            if winget_target not in APPROVED_SOFTWARE_WINGET_IDS:
                violations.append(
                    f"{label} — uninstall target '{winget_target}' is not in the "
                    f"approved software catalogue."
                )

        # kill_process — must look like a plain executable name, not a path or command
        if step.action == "kill_process":
            if not re.fullmatch(r"[A-Za-z0-9_\-\.]{1,64}\.exe", step.target, re.IGNORECASE):
                violations.append(
                    f"{label} — kill_process target must be a plain .exe filename, "
                    f"not a path or command: {step.target!r}"
                )

        # disable_service — service names are alphanumeric + underscores/hyphens only
        if step.action == "disable_service":
            if not re.fullmatch(r"[A-Za-z0-9_\-]{1,256}", step.target):
                violations.append(
                    f"{label} — disable_service target is not a valid service name: {step.target!r}"
                )

        # Scan all string values for common prompt-injection residue
        _check_for_injection_residue(step.target, label, violations)
        _check_for_injection_residue(step.description, label, violations)

    # Layer 3: Ensure winget_id hasn't been swapped for something off-catalogue
    if model.winget_id and model.winget_id not in APPROVED_SOFTWARE_WINGET_IDS:
        violations.append(
            f"winget_id '{model.winget_id}' is not in the approved software catalogue. "
            f"The LLM may have substituted a different package."
        )

    if violations:
        logger.warning(
            "Plan validation FAILED for '%s' (%d violation(s)):\n  %s",
            software_name,
            len(violations),
            "\n  ".join(violations),
        )
        return ValidationResult(valid=False, violations=violations)

    logger.info("Plan validation passed for '%s' (%d steps).", software_name, len(all_steps))
    return ValidationResult(valid=True)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _is_allowed_registry_path(target: str) -> bool:
    target_upper = target.upper()
    return any(target_upper.startswith(prefix.upper()) for prefix in ALLOWED_REGISTRY_PREFIXES)


# Patterns that suggest a prompt injection attempt survived into the LLM output
_INJECTION_PATTERNS = [
    r"ignore\s+(previous|above|prior)\s+instructions",
    r"disregard\s+(all|previous)",
    r"system\s*prompt",
    r"<\s*script",
    r"eval\s*\(",
    r"base64_decode",
    r"invoke-expression",
    r"iex\s*\(",
    r"downloadstring",
    r"webclient",
    r"net\.webclient",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def _check_for_injection_residue(text: str, label: str, violations: list[str]) -> None:
    if _INJECTION_RE.search(text):
        violations.append(
            f"{label} — possible prompt injection residue detected in field value: {text[:120]!r}"
        )
