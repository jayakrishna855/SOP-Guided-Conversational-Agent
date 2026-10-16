from __future__ import annotations

from app.claims.consent import ConsentStatus
from app.verification.matcher import IDENTITY_FIELDS, REQUIRED_MATCHES, MatchResult, match_identity
from app.workflow.actions import Action, PhaseResult, Step, TurnContext, handoff, offer_handoff
from app.workflow.state import Phase
from app.workflow.templates import field_label

ASK_AT_ONCE = 2  # missing fields are asked for one or two at a time


def handle(ctx: TurnContext) -> PhaseResult:
    s = ctx.session
    if s.consent_status == "pending":
        s.consent_poll_index += 1
        return _consent_result(ctx, ctx.services.consent.poll(s.consent_poll_index), first=False)
    if _exhausted(ctx):
        return offer_handoff(s, "verification_exhausted")

    provided = {name: slot.value for name, slot in s.identity_slots.items()}
    result = match_identity(ctx.services.fixtures.policyholders, provided, s.refused_fields)
    for name, slot in s.identity_slots.items():
        slot.status = result.field_status.get(name, "unchecked")
    s.candidate_party_id = result.candidate_party_id
    for name in result.unreadable_fields:
        s.identity_slots.pop(name, None)  # asked for again

    if result.outcome == "verified":
        if s.caller_role == "representative":
            return _start_consent(ctx, result.candidate_party_id)
        s.verified_party_id = result.candidate_party_id
        s.caller_role = "policyholder"
        return PhaseResult([Step(Action.VERIFIED)], next_phase=Phase.RESOLVE_INTENT)

    if result.outcome == "failed":
        # Start over without saying which field failed.
        s.failed_verification_attempts += 1
        s.identity_slots.clear()
        s.candidate_party_id = None
        if _exhausted(ctx):
            return offer_handoff(s, "verification_exhausted")
        return PhaseResult([Step(Action.VERIFY_FAILED)])

    if len([f for f in IDENTITY_FIELDS if f not in s.refused_fields]) < REQUIRED_MATCHES:
        # Too many refusals for 3 matches to be possible. The refusal that caused
        # it counts as a failed attempt; the caller can still change their mind.
        if ctx.new_refusals:
            s.failed_verification_attempts += 1
            if _exhausted(ctx):
                return offer_handoff(s, "verification_exhausted")
        return offer_handoff(s, "verification_impossible")

    return PhaseResult([_ask_identity(ctx, result)])


def _exhausted(ctx: TurnContext) -> bool:
    return ctx.session.failed_verification_attempts >= ctx.settings.max_verify_attempts


def _ask_identity(ctx: TurnContext, result: MatchResult) -> Step:
    s = ctx.session
    rep = s.caller_role == "representative"
    have = len([f for f in s.identity_slots if f in IDENTITY_FIELDS])
    needed = max(1, REQUIRED_MATCHES - have)
    # With one match still needed, two fields are offered as alternatives ("X or Y").
    ask = [f for f in result.missing_fields if f not in result.unreadable_fields][:ASK_AT_ONCE]
    return Step(Action.ASK_IDENTITY, {
        "fields": [field_label(f, rep) for f in ask],
        "needed": needed,
        "unreadable": [field_label(f, rep) for f in result.unreadable_fields],
        "refused_ack": bool(ctx.new_refusals),
        "hint_noted": ctx.new_hints,
        "misunderstood": not ctx.extraction_ok,
    })


def _start_consent(ctx: TurnContext, party_id: str) -> PhaseResult:
    """The policyholder's identity checks out; a representative also needs a
    relationship on file and the policyholder's consent (§5.2.7)."""
    s = ctx.session
    if not s.rep_info.rep_name:
        return PhaseResult([Step(Action.ASK_REP_DETAILS)])
    record = ctx.services.consent.find_representative(s.rep_info.rep_name, party_id, s.rep_info.relationship)
    if record is None:
        return handoff("no_relationship")
    s.consent_status = "pending"
    s.consent_poll_index = 0
    return _consent_result(ctx, ctx.services.consent.poll(0), first=True)


def _consent_result(ctx: TurnContext, status: ConsentStatus, *, first: bool) -> PhaseResult:
    s = ctx.session
    if status == "approved":
        s.consent_status = "approved"
        s.verified_party_id = s.candidate_party_id
        return PhaseResult([Step(Action.CONSENT_APPROVED)], next_phase=Phase.RESOLVE_INTENT)
    if status == "timeout":
        s.consent_status = "timeout"
        return handoff("consent_timeout")
    return PhaseResult([Step(Action.CONSENT_REQUESTED if first else Action.CONSENT_PENDING)])
