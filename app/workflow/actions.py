from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.model.extraction import Extraction
from app.workflow.state import HandoffReason, Phase, Session

if TYPE_CHECKING:
    from app.config import Settings
    from app.services import Services


class Action(StrEnum):
    GREETING = "greeting"
    # VERIFY_ID
    ASK_IDENTITY = "ask_identity"
    VERIFY_FAILED = "verify_failed"
    VERIFIED = "verified"
    ASK_REP_DETAILS = "ask_rep_details"
    CONSENT_REQUESTED = "consent_requested"
    CONSENT_PENDING = "consent_pending"
    CONSENT_APPROVED = "consent_approved"
    # RESOLVE_INTENT
    ASK_INTENT = "ask_intent"  # no usable hints: what is the call about?
    CHOOSE_CASE = "choose_case"
    CASE_CONFIRMED = "case_confirmed"
    # PROCESS_CASE
    ANSWER_STATUS = "answer_status"
    ANSWER_DENIAL = "answer_denial"
    ANSWER_DOCUMENTS = "answer_documents"
    ANSWER_TOPIC = "answer_topic"  # a claim_followup_guidance topic, or the fallback
    ANSWER_ALTERNATIVE = "answer_alternative"
    ANSWER_AMOUNT = "answer_amount"
    ANSWER_NOT_COVERED = "answer_not_covered"  # no-documents claim, nothing specific applies
    # POST_PROCESS
    ASK_ANYTHING_ELSE = "ask_anything_else"
    SUMMARY_PREVIEW = "summary_preview"
    ASK_SEND_AGAIN = "ask_send_again"
    SUMMARY_SENT = "summary_sent"
    SUMMARY_SKIPPED = "summary_skipped"
    ACTION_UNAVAILABLE = "action_unavailable"
    # Global rules
    REFUSE_OUT_OF_SCOPE = "refuse_out_of_scope"
    SMALL_TALK = "small_talk"
    OFFER_HANDOFF = "offer_handoff"
    HANDOFF = "handoff"
    HANDOFF_DECLINED = "handoff_declined"
    SESSION_ENDED = "session_ended"


@dataclass(frozen=True)
class Step:
    action: Action
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhaseResult:
    steps: list[Step]
    # Transition to apply after this handler. If the target phase has a handler
    # and the session didn't end, the engine runs it in the same turn.
    next_phase: Phase | None = None
    end_reason: str | None = None  # set when next_phase is END


def handoff(reason: HandoffReason) -> PhaseResult:
    """Hand the caller to a (simulated) human now; the session ends."""
    return PhaseResult([Step(Action.HANDOFF, {"reason": reason})], next_phase=Phase.END, end_reason=reason)


def offer_handoff(session: Session, reason: HandoffReason) -> PhaseResult:
    """Offer a human; the caller's next yes/no answers it (handled by the global rules)."""
    session.pending_offer = reason
    return PhaseResult([Step(Action.OFFER_HANDOFF, {"reason": reason})])


@dataclass
class TurnContext:
    session: Session
    message: str
    extraction: Extraction
    extraction_ok: bool
    services: Services
    settings: Settings
    # What this turn's merge added.
    new_identity_fields: set[str] = field(default_factory=set)
    new_refusals: set[str] = field(default_factory=set)
    new_hints: bool = False
    turn_hints: dict[str, str | int] = field(default_factory=dict)
    # True when the handler runs after a transition earlier in the same turn.
    chained: bool = False
