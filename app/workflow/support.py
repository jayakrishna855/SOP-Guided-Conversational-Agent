from __future__ import annotations

from typing import Literal

from app.workflow.actions import Action, PhaseResult, Step, TurnContext, offer_handoff
from app.workflow.state import Phase, Session

Gate = Literal["identity", "consent"]


def gate(session: Session) -> Gate | None:
    """The required step the caller is currently held at, if any."""
    if session.phase != Phase.VERIFY_ID or session.verified:
        return None
    return "consent" if session.consent_status == "pending" else "identity"


def pushing_back(ctx: TurnContext) -> bool:
    return ctx.extraction.pushback and gate(ctx.session) is not None


def plan(ctx: TurnContext, max_pushback: int) -> tuple[list[Step], PhaseResult | None]:
    """Steps to put before this turn's reply, and an escalation that replaces the
    phase handler when the caller has pushed back too many times in a row."""
    s, ex = ctx.session, ctx.extraction
    held = gate(s)
    if held is not None:
        if ex.pushback:
            s.pushback_streak += 1
        elif ctx.new_identity_fields:
            s.pushback_streak = 0

    # A pending handoff offer or a request for a human is answered by the global rules.
    recover = pushing_back(ctx) and s.pending_offer is None and not ex.wants_human
    steps: list[Step] = []
    if ex.emotion != "none" or recover:
        steps.append(Step(Action.ACKNOWLEDGE_FEELING, {"emotion": ex.emotion if ex.emotion != "none" else "frustrated"}))
    if not recover:
        return steps, None
    if s.pushback_streak >= max_pushback:
        return steps, offer_handoff(s, "caller_upset")
    steps.append(Step(Action.EXPLAIN_GATE, {"gate": held, "again": s.pushback_streak > 1}))
    return steps, None
