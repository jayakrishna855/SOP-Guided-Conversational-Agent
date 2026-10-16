from __future__ import annotations

from typing import Literal

from app.model.extraction import Extraction
from app.workflow.state import Session

ScopeDecision = Literal["continue", "refuse", "small_talk", "offer_handoff"]


def apply_scope(session: Session, ex: Extraction, threshold: int) -> ScopeDecision:
    if ex.scope == "small_talk":
        return "small_talk"  # doesn't count toward the streak, or reset it
    if ex.scope == "out_of_scope":
        session.out_of_scope_streak += 1
        return "offer_handoff" if session.out_of_scope_streak >= threshold else "refuse"
    session.out_of_scope_streak = 0
    return "continue"
