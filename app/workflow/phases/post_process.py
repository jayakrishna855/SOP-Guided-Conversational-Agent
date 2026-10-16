from __future__ import annotations

from app.claims.formatting import mask_email
from app.claims.summary import ClaimSummary, build_summary
from app.services import Services
from app.workflow.actions import Action, PhaseResult, Step, TurnContext
from app.workflow.state import CaseHints, Phase, Session

# Bookkeeping entries in discussed_topics that aren't topics for the caller.
_INTERNAL_TOPIC_PREFIXES = ("alternatives:", "deadline_offer")


def handle(ctx: TurnContext) -> PhaseResult:
    s, ex = ctx.session, ctx.extraction
    if s.email_preview is None:
        if ctx.chained:
            return PhaseResult([Step(Action.ASK_ANYTHING_ELSE)])
        if ex.confirmation == "no" or ex.done:
            return _preview(ctx.session, ctx.services)
        has_question = bool(ex.intent_label or ctx.turn_hints or ex.followup_topic)
        if ex.confirmation == "yes" or has_question:
            if ctx.turn_hints:
                s.active_case_id = None
                s.case_hints = CaseHints(**ctx.turn_hints)
            elif not has_question:  # a bare "yes": ask afresh which claim and what about
                s.active_case_id = None
                s.case_hints = CaseHints()
            return PhaseResult([], next_phase=Phase.RESOLVE_INTENT)
        return PhaseResult([Step(Action.ASK_ANYTHING_ELSE)])

    if ex.confirmation in ("yes", "no"):
        return choose(s, ctx.services, send=ex.confirmation == "yes")
    return PhaseResult([Step(Action.ASK_SEND_AGAIN, {"email": _masked(s, ctx.services)})])


def awaiting_choice(session: Session) -> bool:
    return session.phase == Phase.POST_PROCESS and session.email_preview is not None \
        and session.email_choice == "none"


def choose(session: Session, services: Services, *, send: bool) -> PhaseResult:
    holder = _holder(session, services)
    if send:
        services.outbox.send(session.id, holder.email, session.email_preview or "")
        session.email_choice = "sent"
        step = Step(Action.SUMMARY_SENT, {"email": mask_email(holder.email)})
    else:
        session.email_choice = "skipped"
        step = Step(Action.SUMMARY_SKIPPED)
    return PhaseResult([step], next_phase=Phase.END, end_reason="completed")


def _preview(session: Session, services: Services) -> PhaseResult:
    order = list(dict.fromkeys(d.case_id for d in session.discussed_topics))
    items = []
    for case_id in order:
        claim = services.claims.get_claim(session.verified_party_id, case_id)
        if claim is None:
            continue
        topics = [d.topic for d in session.discussed_topics
                  if d.case_id == case_id and not d.topic.startswith(_INTERNAL_TOPIC_PREFIXES)]
        steps = [f.text for f in session.followups if f.case_id == case_id]
        items.append(ClaimSummary(claim, topics, steps))
    session.email_preview = build_summary(_holder(session, services), items)
    return PhaseResult([Step(Action.SUMMARY_PREVIEW, {"preview": session.email_preview,
                                                      "email": _masked(session, services)})])


def _holder(session: Session, services: Services):
    return next(p for p in services.fixtures.policyholders if p.party_id == session.verified_party_id)


def _masked(session: Session, services: Services) -> str:
    return mask_email(_holder(session, services).email)
