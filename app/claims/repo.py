from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from app.claims.fixtures import Claim, Fixtures

_CLAIM_ID_RE = re.compile(r"^(?:CL)?[\s-]*(\d+)$", re.IGNORECASE)


def normalize_claim_id(value: str) -> str | None:
    """'cl-2048', 'CL 2048' and '2048' all become 'CL-2048'."""
    match = _CLAIM_ID_RE.match(value.strip())
    return f"CL-{match.group(1)}" if match else None


@dataclass(frozen=True)
class DeadlineStatus:
    deadline: dt.date
    passed: bool
    days_remaining: int  # negative once passed


class ClaimsRepo:
    def __init__(self, fixtures: Fixtures):
        self._claims = fixtures.claims

    def claims_for_party(self, party_id: str) -> list[Claim]:
        return [c for c in self._claims if c.party_id == party_id]

    def get_claim(self, party_id: str, case_id: str) -> Claim | None:
        """Returns the claim only if it belongs to party_id."""
        case_id = normalize_claim_id(case_id) or ""
        return next((c for c in self.claims_for_party(party_id) if c.case_id == case_id), None)

    def resolve_cases(
        self,
        party_id: str,
        *,
        claim_id: str | None = None,
        case_type: str | None = None,
        status: str | None = None,
        month: int | None = None,
        year: int | None = None,
    ) -> list[Claim]:
        """Filters only party_id's claims by every hint that was given.

        No hints returns all of the party's claims; contradictory hints return [].
        Month and year are compared with the claim's created_at.
        """
        candidates = self.claims_for_party(party_id)
        if claim_id is not None:
            wanted = normalize_claim_id(claim_id)
            candidates = [c for c in candidates if c.case_id == wanted]
        if case_type is not None:
            candidates = [c for c in candidates if c.case_type == case_type]
        if status is not None:
            candidates = [c for c in candidates if c.status == status]
        if month is not None:
            candidates = [c for c in candidates if c.created_at.month == month]
        if year is not None:
            candidates = [c for c in candidates if c.created_at.year == year]
        return candidates


def appeal_deadline_status(claim: Claim, today: dt.date) -> DeadlineStatus | None:
    """The deadline day itself still counts as open."""
    if claim.appeal_deadline is None:
        return None
    days = (claim.appeal_deadline - today).days
    return DeadlineStatus(deadline=claim.appeal_deadline, passed=days < 0, days_remaining=days)
