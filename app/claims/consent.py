from __future__ import annotations

from typing import Literal

from app.claims.fixtures import Fixtures, Representative

ConsentStatus = Literal["pending", "approved", "timeout"]


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


class ConsentSimulator:
    def __init__(self, fixtures: Fixtures, scenario: str):
        if scenario not in fixtures.consent_scenarios:
            raise ValueError(
                f"CONSENT_SCENARIO={scenario!r} is not in consent_scenarios.json "
                f"(choose from {sorted(fixtures.consent_scenarios)})"
            )
        self.scenario = scenario
        self._sequence = fixtures.consent_scenarios[scenario].status_sequence
        self._representatives = fixtures.representatives

    def find_representative(
        self, rep_name: str, party_id: str, relationship: str | None = None
    ) -> Representative | None:
        """The relationship record for this rep and this (verified-identity) policyholder.
        A stated relationship must agree with the record when given."""
        for rep in self._representatives:
            if rep.buyer_party_id != party_id or _norm(rep.rep_name) != _norm(rep_name):
                continue
            if relationship is not None and _norm(relationship) != _norm(rep.relationship):
                continue
            return rep
        return None

    def poll(self, index: int) -> ConsentStatus:
        """Status at poll `index` (0 is the poll made when the request is sent).
        Approval is final; a sequence that ends pending means timeout."""
        if index < 0:
            raise ValueError("poll index must be >= 0")
        for status in self._sequence[: index + 1]:
            if status != "pending":
                return status
        return "pending" if index < len(self._sequence) else "timeout"
