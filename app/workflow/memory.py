from __future__ import annotations

from dataclasses import dataclass, field

from app.claims.repo import normalize_claim_id
from app.model.extraction import Extraction
from app.workflow.state import IdentitySlot, Session


@dataclass
class MergeInfo:
    new_identity_fields: set[str] = field(default_factory=set)
    new_refusals: set[str] = field(default_factory=set)
    new_hints: bool = False
    # This turn's own case hints (normalized), apart from what memory already held.
    turn_hints: dict[str, str | int] = field(default_factory=dict)


def merge(session: Session, ex: Extraction) -> MergeInfo:
    info = MergeInfo()
    identity_open = not session.verified and session.consent_status == "none"

    if identity_open:
        given = ex.identity.given()
        if "full_name" not in given and ex.rep_info.policyholder_name:
            given["full_name"] = ex.rep_info.policyholder_name
        for name, value in given.items():
            slot = session.identity_slots.get(name)
            if slot is None or slot.value != value:
                session.identity_slots[name] = IdentitySlot(value=value)
                info.new_identity_fields.add(name)
            if name in session.refused_fields:
                session.refused_fields.remove(name)
        for name in ex.refused_fields:
            if name in given or name in session.refused_fields:
                continue
            session.refused_fields.append(name)
            session.identity_slots.pop(name, None)
            info.new_refusals.add(name)

        if ex.caller_role != "unknown":
            session.caller_role = ex.caller_role
        for key, value in ex.rep_info.model_dump().items():
            if value is not None:
                setattr(session.rep_info, key, value)

    if ex.intent_label:
        info.new_hints = True
        if ex.intent_label not in session.intent_hints:
            session.intent_hints.append(ex.intent_label)
    for key, value in ex.case_hints.model_dump().items():
        if value is None:
            continue
        if key == "claim_id":
            value = normalize_claim_id(value)
            if value is None:
                continue
        setattr(session.case_hints, key, value)
        info.turn_hints[key] = value
        info.new_hints = True
    return info
