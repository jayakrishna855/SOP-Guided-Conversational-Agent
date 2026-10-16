from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.claims.fixtures import Policyholder
from app.verification import normalize as n

IDENTITY_FIELDS = ("full_name", "dob", "phone", "email", "id_last4")
REQUIRED_MATCHES = 3

Outcome = Literal["verified", "insufficient", "failed"]
FieldStatus = Literal["matched", "mismatched"]

_NORMALIZERS: dict[str, Callable[[str], str | None]] = {
    "full_name": n.normalize_name,
    "dob": n.normalize_dob,
    "phone": n.normalize_phone,
    "email": n.normalize_email,
    "id_last4": n.normalize_last4,
    "policy_number": n.normalize_policy_number,
}


def _record_values(record: Policyholder, fieldname: str) -> set[str]:
    """Normalized values on file, aliases included. ssn_last4 and national_id_last4
    are both stored as id_last4, so they compare as the same field."""
    raw: Iterable[str] = {
        "full_name": (record.name, *record.name_aliases),
        "dob": (record.dob.isoformat(),),
        "phone": (record.phone, *record.phone_aliases),
        "email": (record.email, *record.email_aliases),
        "id_last4": (record.id_last4,),
        "policy_number": (record.policy_number,),
    }[fieldname]
    return {v for v in (_NORMALIZERS[fieldname](r) for r in raw) if v is not None}


@dataclass(frozen=True)
class MatchResult:
    outcome: Outcome
    # Verified party, or the single record still consistent with everything given.
    candidate_party_id: str | None
    # Per provided field, relative to the closest record. Absent means unchecked.
    field_status: dict[str, FieldStatus] = field(default_factory=dict)
    # Permitted fields not yet provided and not refused, in asking order.
    missing_fields: tuple[str, ...] = ()
    # Provided values that couldn't be parsed (treated as not provided).
    unreadable_fields: tuple[str, ...] = ()


@dataclass
class _Score:
    record: Policyholder
    matched: set[str]
    contradicted: set[str]

    @property
    def identity_matches(self) -> int:
        return len(self.matched & set(IDENTITY_FIELDS))


def match_identity(
    policyholders: Iterable[Policyholder],
    provided: Mapping[str, str | None],
    refused: Iterable[str] = (),
) -> MatchResult:
    unknown = set(provided) - set(_NORMALIZERS)
    if unknown:
        raise ValueError(f"unknown identity field(s): {sorted(unknown)}")

    values: dict[str, str] = {}
    unreadable: list[str] = []
    for name, raw in provided.items():
        if raw is None or not str(raw).strip():
            continue
        norm = _NORMALIZERS[name](str(raw))
        if norm is None:
            unreadable.append(name)
        else:
            values[name] = norm

    refused_set = set(refused)
    missing = tuple(f for f in IDENTITY_FIELDS if f not in values and f not in refused_set)
    given_identity = [f for f in values if f in IDENTITY_FIELDS]

    scores = []
    for record in policyholders:
        matched = {f for f, v in values.items() if v in _record_values(record, f)}
        scores.append(_Score(record, matched, set(given_identity) - matched))

    consistent = [s for s in scores if not s.contradicted and s.matched]
    qualified = [s for s in consistent if s.identity_matches >= REQUIRED_MATCHES]

    best_count = max((len(s.matched) for s in scores), default=0)
    closest = [s for s in scores if best_count and len(s.matched) == best_count]
    status: dict[str, FieldStatus] = {}
    if len(closest) == 1:
        status = {f: ("matched" if f in closest[0].matched else "mismatched") for f in values}

    def result(outcome: Outcome, candidate: _Score | None = None) -> MatchResult:
        return MatchResult(
            outcome=outcome,
            candidate_party_id=candidate.record.party_id if candidate else None,
            field_status=status,
            missing_fields=missing if outcome == "insufficient" else (),
            unreadable_fields=tuple(unreadable),
        )

    if len(qualified) == 1:
        return result("verified", qualified[0])
    if len(qualified) > 1:
        return result("failed")  # ambiguous
    if consistent:
        # Still on track, just short of 3 fields. Several consistent records
        # means more fields are needed to tell them apart.
        return result("insufficient", consistent[0] if len(consistent) == 1 else None)
    if best_count > 0:
        # Something matched a record, but something else contradicted it.
        return result("failed")
    if len(given_identity) >= REQUIRED_MATCHES:
        return result("failed")  # no record matches
    return result("insufficient")
