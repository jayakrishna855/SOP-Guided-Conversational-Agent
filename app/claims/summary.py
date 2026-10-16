from __future__ import annotations

import threading
from dataclasses import dataclass

from app.claims.fixtures import Claim, Policyholder
from app.claims.formatting import fmt_date, mask_email

SUBJECT = "Summary of your claims support conversation"

# How each discussed topic reads in the summary.
TOPIC_LABELS = {
    "status": "claim status",
    "denial_reason": "why the claim was denied",
    "appeal_deadline": "the appeal deadline",
    "documents": "documents needed",
    "amount": "claim amounts",
    "alternatives": "alternatives for documents you can't get",
    "missing_required_material_alternatives": "alternatives for documents you can't get",
    "submission_timing": "when to submit documents",
    "processing_time_after_submission": "processing time after submission",
    "submission_method": "how to submit documents",
    "file_format_requirements": "file format requirements",
    "receipt_confirmation": "confirming receipt of documents",
    "claim_followup_fallback": "general follow-up questions",
    "not_covered": "general follow-up questions",
}


@dataclass(frozen=True)
class ClaimSummary:
    claim: Claim
    topics: list[str]
    next_steps: list[str]


def build_summary(holder: Policyholder, claims: list[ClaimSummary]) -> str:
    lines = [f"Hello {holder.name},", "", "Here is a summary of today's conversation with claims support.", ""]
    if not claims:
        lines += ["No specific claim was discussed.", ""]
    for item in claims:
        c = item.claim
        lines.append(f"Claim {c.case_id} ({c.case_type}, opened {fmt_date(c.created_at)})")
        lines.append(f"- Status: {c.status}")
        lines.append(f"- Outcome: {c.summary}")
        if c.denial_reason:
            lines.append(f"- Denial reason: {c.denial_reason}")
        labels = list(dict.fromkeys(TOPIC_LABELS.get(t, t.replace("_", " ")) for t in item.topics))
        if labels:
            lines.append(f"- Discussed: {', '.join(labels)}")
        if item.next_steps:
            lines.append("- Next steps:")
            lines += [f"  - {step}" for step in item.next_steps]
        lines.append("")
    lines.append("This is a simulated message from a demo with synthetic data.")
    return "\n".join(lines)


@dataclass(frozen=True)
class SentEmail:
    to: str  # masked; the full address never leaves the server
    subject: str
    body: str


class Outbox:
    """Simulated sent mail, per session, shown in the UI."""

    def __init__(self):
        self._sent: dict[str, list[SentEmail]] = {}
        self._lock = threading.Lock()

    def send(self, session_id: str, email: str, body: str) -> SentEmail:
        message = SentEmail(to=mask_email(email), subject=SUBJECT, body=body)
        with self._lock:
            self._sent.setdefault(session_id, []).append(message)
        return message

    def for_session(self, session_id: str) -> list[SentEmail]:
        with self._lock:
            return list(self._sent.get(session_id, []))
