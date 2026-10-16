from __future__ import annotations

import re

from app.claims.fixtures import Claim
from app.claims.formatting import fmt_date
from app.claims.guidance import FALLBACK_TOPIC, format_documents
from app.claims.repo import appeal_deadline_status
from app.workflow.actions import Action, PhaseResult, Step, TurnContext, offer_handoff
from app.workflow.phases.resolve_intent import DEFAULT_INTENT, brief
from app.workflow.state import CaseHints, Phase

ALTERNATIVES_TOPIC = "missing_required_material_alternatives"
# Topics whose answer also carries the per-document preparation guidance.
_WITH_SUBMISSION_GUIDANCE = {"submission_method", "file_format_requirements"}

AMOUNT_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "net_pay": ("net pay", ("net pay", "amount paid", "paid amount")),
    "allowed_max_amount": ("allowed maximum amount", ("allowed max", "allowed maximum", "maximum amount", "max amount")),
    "expected_reimbursement_amount": ("expected reimbursement amount", ("expected reimbursement", "reimbursement")),
    "net_fee": ("net fee", ("net fee", "fee schedule")),
}


def handle(ctx: TurnContext) -> PhaseResult:
    s = ctx.session
    claim = ctx.services.claims.get_claim(s.verified_party_id, s.active_case_id or "")
    if claim is None:  # can't happen through RESOLVE_INTENT; never answer without a claim
        s.active_case_id = None
        return PhaseResult([], next_phase=Phase.RESOLVE_INTENT)

    if not ctx.chained:
        if ctx.turn_hints and claim not in ctx.services.claims.resolve_cases(s.verified_party_id, **ctx.turn_hints):
            # The caller is asking about a different claim.
            s.active_case_id = None
            s.case_hints = CaseHints(**ctx.turn_hints)
            return PhaseResult([], next_phase=Phase.RESOLVE_INTENT)
        if ctx.extraction.done:
            return PhaseResult([], next_phase=Phase.POST_PROCESS)

    return _answer(ctx, claim)


def _answer(ctx: TurnContext, claim: Claim) -> PhaseResult:
    s, ex, guidance = ctx.session, ctx.extraction, ctx.services.guidance
    message = ctx.message

    if ex.followup_topic == ALTERNATIVES_TOPIC and claim.documents_needed:
        documents = guidance.documents_mentioned(claim, message)
        if documents:
            return _alternatives(ctx, claim, documents)

    amounts = [f for f, (_, phrases) in AMOUNT_FIELDS.items() if any(_mentions(message, p) for p in phrases)]
    if amounts:
        return PhaseResult([_amount(ctx, claim, f) for f in amounts])

    intents = set(s.intent_hints) | ({ex.intent_label} if ex.intent_label else set())
    selected = guidance.select_topic(claim, intents or {DEFAULT_INTENT}, message,
                                     None if ctx.chained else ex.followup_topic)
    if selected.matched_by in ("match_any", "model"):
        return PhaseResult([_topic(ctx, claim, selected.topic, selected.text)])

    if ctx.chained:
        intent = ex.intent_label or (s.intent_hints[0] if s.intent_hints else DEFAULT_INTENT)
        return _intent_answer(ctx, claim, intent)
    if ex.intent_label and ex.intent_label != DEFAULT_INTENT:
        return _intent_answer(ctx, claim, ex.intent_label)

    if not claim.documents_needed:
        s.note_topic(claim.case_id, "not_covered")
        return PhaseResult([Step(Action.ANSWER_NOT_COVERED, _status_facts(claim))])
    return PhaseResult([_topic(ctx, claim, FALLBACK_TOPIC, selected.text)])


def _mentions(message: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", message.casefold()) is not None




def _intent_answer(ctx: TurnContext, claim: Claim, intent: str) -> PhaseResult:
    if intent == "denial_question" and claim.status == "denied":
        return _denial(ctx, claim)
    if intent in ("document_submission", "next_steps") and claim.documents_needed:
        return PhaseResult([_documents(ctx, claim)])
    ctx.session.note_topic(claim.case_id, "status")
    facts = _status_facts(claim)
    if intent == "denial_question":
        facts["not_denied"] = True
    if intent in ("document_submission", "next_steps"):
        facts["no_documents_needed"] = True
    return PhaseResult([Step(Action.ANSWER_STATUS, facts)])


def _status_facts(claim: Claim) -> dict:
    facts = {"claim": brief(claim), "summary": claim.summary}
    if claim.denial_reason:
        facts["denial_reason"] = claim.denial_reason
    if claim.status == "closed":
        facts["net_pay"] = claim.net_pay
    if claim.documents_needed:
        facts["documents"] = format_documents(claim.documents_needed)
    return facts


def _denial(ctx: TurnContext, claim: Claim) -> PhaseResult:
    s = ctx.session
    facts = {"claim": brief(claim), "denial_reason": claim.denial_reason,
             "documents": format_documents(claim.documents_needed)}
    s.note_topic(claim.case_id, "denial_reason")
    if claim.documents_needed:
        s.note_followup(claim.case_id, f"Send the {facts['documents']} for claim {claim.case_id}.")
    steps = []
    deadline = appeal_deadline_status(claim, ctx.settings.today())
    offer = None
    if deadline is not None:
        s.note_topic(claim.case_id, "appeal_deadline")
        facts.update({"appeal_deadline": deadline.deadline.isoformat(), "deadline_passed": deadline.passed,
                      "days_remaining": deadline.days_remaining, "today": ctx.settings.today().isoformat()})
        if deadline.passed:
            s.note_followup(claim.case_id, f"Talk to a human representative about your options, since the appeal "
                                           f"deadline ({fmt_date(deadline.deadline)}) has passed.")
            if not s.discussed(claim.case_id, "deadline_offer"):
                s.note_topic(claim.case_id, "deadline_offer")
                offer = offer_handoff(s, "deadline_passed")
        else:
            s.note_followup(claim.case_id, f"Appeal by {fmt_date(deadline.deadline)} if you want the denial reviewed.")
    steps.append(Step(Action.ANSWER_DENIAL, facts))
    if offer:
        steps += offer.steps
    return PhaseResult(steps)


def _documents(ctx: TurnContext, claim: Claim) -> Step:
    docs = format_documents(claim.documents_needed)
    ctx.session.note_topic(claim.case_id, "documents")
    ctx.session.note_followup(claim.case_id, f"Send the {docs} for claim {claim.case_id}.")
    return Step(Action.ANSWER_DOCUMENTS, {"claim": brief(claim), "documents": docs,
                                          "guidance": ctx.services.guidance.submission_guidance(claim)})


def _topic(ctx: TurnContext, claim: Claim, topic: str, text: str) -> Step:
    s, g = ctx.session, ctx.services.fixtures.guideline
    s.note_topic(claim.case_id, topic)
    facts = {"claim": brief(claim), "topic": topic, "text": text}
    docs = format_documents(claim.documents_needed)
    if topic in _WITH_SUBMISSION_GUIDANCE:
        facts["guidance"] = ctx.services.guidance.submission_guidance(claim)
    if topic == "submission_timing":
        s.note_followup(claim.case_id, f"Submit the {docs} within a week.")
    elif topic == "processing_time_after_submission":
        avg = g.claim_followup_settings.average_processing_time_after_submission.en
        s.note_followup(claim.case_id, f"Once the files are received, the review time is {avg}.")
    elif topic == "submission_method":
        s.note_followup(claim.case_id, f"Upload the {docs} through the member portal or claim upload link.")
    return Step(Action.ANSWER_TOPIC, facts)


def _alternatives(ctx: TurnContext, claim: Claim, documents: list[str]) -> PhaseResult:
    s, guidance = ctx.session, ctx.services.guidance
    steps, exhausted_any = [], False
    for doc in documents:
        key = f"alternatives:{doc}"
        exhausted = s.discussed(claim.case_id, key)  # alternatives already given once
        exhausted_any |= exhausted
        s.note_topic(claim.case_id, key)
        s.note_topic(claim.case_id, "alternatives")
        if exhausted:
            s.note_followup(claim.case_id, f"A human claims representative should review the file for manual "
                                           f"options for the {doc}.")
        else:
            s.note_followup(claim.case_id, f"Ask the provider for a replacement copy of the {doc}, or send the "
                                           f"closest substitute with a short note.")
        steps.append(Step(Action.ANSWER_ALTERNATIVE, {
            "claim": brief(claim), "document": doc, "exhausted": exhausted,
            "text": guidance.alternative_guidance(doc, exhausted=exhausted),
        }))
    if exhausted_any:
        steps += offer_handoff(s, "alternatives_exhausted").steps
    return PhaseResult(steps)


def _amount(ctx: TurnContext, claim: Claim, field: str) -> Step:
    ctx.session.note_topic(claim.case_id, "amount")
    return Step(Action.ANSWER_AMOUNT, {
        "claim": brief(claim), "field": field, "label": AMOUNT_FIELDS[field][0],
        "value": getattr(claim, field), "description": ctx.services.guidance.field_description(field),
    })
