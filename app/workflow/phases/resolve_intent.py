from __future__ import annotations

from app.claims.fixtures import Claim
from app.workflow.actions import Action, PhaseResult, Step, TurnContext
from app.workflow.state import CaseHints, Phase

DEFAULT_INTENT = "general_claim_question"


def brief(claim: Claim) -> dict[str, str]:
    """A claim in a few words, for confirming or choosing."""
    return {"case_id": claim.case_id, "case_type": claim.case_type, "created_at": claim.created_at.isoformat(),
            "status": claim.status}


def handle(ctx: TurnContext) -> PhaseResult:
    s = ctx.session
    repo = ctx.services.claims
    party = s.verified_party_id
    if not s.intent_hints:
        s.intent_hints.append(ctx.extraction.intent_label or DEFAULT_INTENT)

    # Re-entry with the active claim still selected and nothing pointing elsewhere
    # ("anything else?" → "yes, how long after I submit?"): keep that claim.
    if s.active_case_id and not ctx.turn_hints:
        return PhaseResult([], next_phase=Phase.PROCESS_CASE)

    hints = s.case_hints.given()
    candidates = repo.resolve_cases(party, **hints) if hints else []
    if hints and not candidates and ctx.turn_hints and ctx.turn_hints != hints:
        # The new hints may replace stale ones ("no, the dental one").
        candidates = repo.resolve_cases(party, **ctx.turn_hints)
        if candidates:
            s.case_hints = CaseHints(**ctx.turn_hints)
    if s.candidate_case_ids and len(candidates) > 1:
        narrowed = [c for c in candidates if c.case_id in s.candidate_case_ids]
        candidates = narrowed or candidates
    if not hints:
        own = repo.claims_for_party(party)
        candidates = own if len(own) == 1 else []

    if len(candidates) == 1:
        claim = candidates[0]
        s.active_case_id = claim.case_id
        s.candidate_case_ids = []
        return PhaseResult([Step(Action.CASE_CONFIRMED, {"claim": brief(claim)})], next_phase=Phase.PROCESS_CASE)

    if len(candidates) > 1:
        s.candidate_case_ids = [c.case_id for c in candidates]
        return PhaseResult([Step(Action.CHOOSE_CASE, {"options": [brief(c) for c in candidates]})])

    # No match (stale hints are dropped so they can't block the next answer) or no hints.
    s.case_hints = CaseHints()
    s.candidate_case_ids = []
    own = repo.claims_for_party(party)
    return PhaseResult([Step(Action.ASK_INTENT, {
        "claim_count": len(own),
        "case_types": sorted({c.case_type for c in own}),
        "no_match": bool(hints),
    })])
