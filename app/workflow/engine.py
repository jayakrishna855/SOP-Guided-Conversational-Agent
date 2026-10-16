from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.config import Settings
from app.model.client import ExtractRequest, ModelAdapter, ReplyRequest, ReplyStep
from app.model.extraction import EMPTY_EXTRACTION, Extraction, parse_extraction
from app.services import Services
from app.workflow import support, templates
from app.workflow.actions import Action, PhaseResult, Step, TurnContext, handoff, offer_handoff
from app.workflow.guard import LeakGuard
from app.workflow.memory import merge
from app.workflow.grounding import GroundingValidator
from app.workflow.phases import post_process, process_case, resolve_intent, verify
from app.workflow.scope import apply_scope
from app.workflow.state import Phase, Session, TranscriptEntry

log = logging.getLogger("app.workflow")

# Any phase may also move to END (handoff or finished).
TRANSITIONS: frozenset[tuple[Phase, Phase]] = frozenset({
    (Phase.VERIFY_ID, Phase.RESOLVE_INTENT),
    (Phase.RESOLVE_INTENT, Phase.PROCESS_CASE),
    (Phase.PROCESS_CASE, Phase.RESOLVE_INTENT),
    (Phase.PROCESS_CASE, Phase.POST_PROCESS),
    (Phase.POST_PROCESS, Phase.RESOLVE_INTENT),
})

HANDLERS: dict[Phase, Callable[[TurnContext], PhaseResult]] = {
    Phase.VERIFY_ID: verify.handle,
    Phase.RESOLVE_INTENT: resolve_intent.handle,
    Phase.PROCESS_CASE: process_case.handle,
    Phase.POST_PROCESS: post_process.handle,
}
MAX_CHAINED_TRANSITIONS = 3
HISTORY_TURNS = 10
# Replies for these actions are fixed text; the model isn't asked to phrase them.
# The summary preview is shown exactly as built.
_TEMPLATE_ONLY = {Action.GREETING, Action.SESSION_ENDED, Action.SUMMARY_PREVIEW}


class IllegalTransition(RuntimeError):
    pass


def transition(session: Session, to: Phase, end_reason: str | None = None) -> None:
    if to == Phase.END:
        if session.phase == Phase.END:
            raise IllegalTransition("session already ended")
        session.end_reason = end_reason
    elif (session.phase, to) not in TRANSITIONS:
        raise IllegalTransition(f"{session.phase} -> {to}")
    elif to == Phase.RESOLVE_INTENT and not session.verified:
        raise IllegalTransition("RESOLVE_INTENT requires a verified party")
    session.phase = to
    session.pending_offer = None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    phase: Phase
    allowed_actions: list[str]
    actions: list[str]  # for logs; not sent to the browser
    guard_hits: list[str]
    grounding_problems: list[str]
    summary_preview: str | None  # shown with the Send/Skip buttons


def allowed_actions(session: Session) -> list[str]:
    if post_process.awaiting_choice(session):
        return ["send_summary", "skip_summary", "reset"]
    return ["reset"]


class Engine:
    """The model is passed per turn: each session uses its own key's adapter.
    Greetings and button actions are template-only and need none."""

    def __init__(self, services: Services, settings: Settings):
        self.services = services
        self.settings = settings
        self.guard = LeakGuard(services.fixtures)
        self.grounding = GroundingValidator(services.fixtures, services.guidance)

    def start(self, session: Session) -> TurnResult:
        return self._reply(session, None, [Step(Action.GREETING)])

    def handle_turn(self, session: Session, message: str, model: ModelAdapter) -> TurnResult:
        started = time.perf_counter()
        if session.phase == Phase.END:
            return self._reply(session, message, [Step(Action.SESSION_ENDED)])

        extraction, ok = self._extract(session, message, model)
        info = merge(session, extraction)
        ctx = TurnContext(
            session=session, message=message, extraction=extraction, extraction_ok=ok,
            services=self.services, settings=self.settings,
            new_identity_fields=info.new_identity_fields, new_refusals=info.new_refusals, new_hints=info.new_hints,
            turn_hints=info.turn_hints,
        )
        
        support_steps, escalation = support.plan(ctx, self.settings.max_pushback)
        result = escalation or self._global_rules(ctx) or HANDLERS[session.phase](ctx)
        steps = list(result.steps)
        chained = 0
        while result.next_phase is not None:
            transition(session, result.next_phase, result.end_reason)
            if session.phase == Phase.END:
                break
            chained += 1
            if chained > MAX_CHAINED_TRANSITIONS:
                raise RuntimeError("too many chained transitions in one turn")
            ctx.chained = True
            result = HANDLERS[session.phase](ctx)
            steps += result.steps

        out = self._reply(session, message, steps, model)
        log.info("turn phase=%s actions=%s guard=%s grounding=%s ms=%d", session.phase, out.actions, out.guard_hits,
                 out.grounding_problems, (time.perf_counter() - started) * 1000)
        return out

    def handle_action(self, session: Session, action: str) -> TurnResult:
        """A Send/Skip button press (§4.5). Checked against allowed_actions on the
        server, whatever the UI or the model's reply suggested."""
        if action not in allowed_actions(session) or action not in ("send_summary", "skip_summary"):
            return self._reply(session, None, [Step(Action.ACTION_UNAVAILABLE)])
        result = post_process.choose(session, self.services, send=action == "send_summary")
        transition(session, result.next_phase, result.end_reason)
        return self._reply(session, None, result.steps)

    # --- pipeline steps --------------------------------------------------------

    def _extract(self, session: Session, message: str, model: ModelAdapter) -> tuple[Extraction, bool]:
        request = ExtractRequest(
            message=message,
            phase=session.phase,
            history=self._history(session),
            followup_topics=[t.topic for t in self.services.fixtures.guideline.claim_followup_guidance],
        )
        try:
            raw = model.extract(request)
        except Exception as exc:  # any model failure means an empty extraction
            log.warning("extraction failed: %s", type(exc).__name__)
            return EMPTY_EXTRACTION, False
        extraction, ok = parse_extraction(raw)
        if not ok:
            log.warning("extraction invalid")
        return extraction, ok

    def _global_rules(self, ctx: TurnContext) -> PhaseResult | None:
        """§5.1, checked before the phase handler. None means the handler runs."""
        s, ex = ctx.session, ctx.extraction
        if s.pending_offer is not None:
            reason, s.pending_offer = s.pending_offer, None
            if ex.wants_human or ex.confirmation == "yes":
                return handoff(reason)
            if ex.confirmation == "no":
                if reason == "out_of_scope":
                    s.out_of_scope_streak = 0
                if reason == "caller_upset":
                    s.pushback_streak = 0
                return PhaseResult([Step(Action.HANDOFF_DECLINED, {"steer": self._steer(s)})])
            # Anything else: the caller moved on, so the offer lapses.
        if ex.wants_human:
            return offer_handoff(s, "human_request")
        if support.pushing_back(ctx):
            return None  # an objection to a gate, not off-topic: the handler asks for what's still needed
        decision = apply_scope(s, ex, self.settings.out_of_scope_threshold)
        if decision == "refuse":
            return PhaseResult([Step(Action.REFUSE_OUT_OF_SCOPE, {"steer": self._steer(s)})])
        if decision == "small_talk":
            return PhaseResult([Step(Action.SMALL_TALK, {"steer": self._steer(s)})])
        if decision == "offer_handoff":
            return offer_handoff(s, "out_of_scope")
        return None

    def _reply(self, session: Session, message: str | None, steps: list[Step],
               model: ModelAdapter | None = None) -> TurnResult:
        fallback = templates.render_all(steps)
        reply = fallback
        if model is not None and message is not None and not any(s.action in _TEMPLATE_ONLY for s in steps):
            request = ReplyRequest(
                message=message,
                phase=session.phase,
                steps=[ReplyStep(s.action, dict(s.facts)) for s in steps],
                fallback=fallback,
                history=self._history(session),
            )
            try:
                reply = (model.respond(request) or "").strip() or fallback
            except Exception as exc:  # §7.3: reply failures use the template
                log.warning("reply failed: %s", type(exc).__name__)

        # output checks. Both are logged without the reply's content.
        hits: list[str] = []
        problems: list[str] = []
        if not session.verified:
            hits = self.guard.scan(reply)
            if hits:
                log.warning("guard_event phase=%s actions=%s categories=%s",
                            session.phase, [s.action.value for s in steps], hits)
                reply = fallback
        elif reply != fallback:
            problems = self.grounding.check(reply, [s.facts for s in steps])
            if problems:
                log.warning("grounding_event phase=%s actions=%s problems=%s",
                            session.phase, [s.action.value for s in steps], problems)
                reply = fallback

        if message is not None:
            session.transcript.append(TranscriptEntry(role="caller", text=message))
        session.transcript.append(TranscriptEntry(role="agent", text=reply))
        preview = session.email_preview if post_process.awaiting_choice(session) else None
        return TurnResult(reply, session.phase, allowed_actions(session), [s.action.value for s in steps], hits,
                          problems, preview)

    def _steer(self, session: Session) -> str:
        return templates.steer(session, self.settings.max_verify_attempts)

    @staticmethod
    def _history(session: Session) -> list[tuple[str, str]]:
        return [(t.role, t.text) for t in session.transcript[-HISTORY_TURNS:]]
