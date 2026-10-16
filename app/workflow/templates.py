from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.claims.formatting import fmt_amount, fmt_date, fmt_month
from app.workflow.actions import Action, Step
from app.workflow.state import Phase, Session

FIELD_LABELS = {
    "full_name": "full name",
    "dob": "date of birth",
    "phone": "phone number",
    "email": "email address",
    "id_last4": "last four digits of your SSN or national ID",
}
_REP_FIELD_LABELS = {
    **{k: f"the policyholder's {v}" for k, v in FIELD_LABELS.items()},
    "id_last4": "the last four digits of the policyholder's SSN or national ID",
}


def field_label(name: str, representative: bool = False) -> str:
    return (_REP_FIELD_LABELS if representative else FIELD_LABELS)[name]


def _join(items: list[str], word: str) -> str:
    if len(items) <= 2:
        return f" {word} ".join(items)
    return f"{', '.join(items[:-1])}, {word} {items[-1]}"


def _your(labels: list[str]) -> list[str]:
    return [label if label.startswith("the ") else f"your {label}" for label in labels]


GREETING = (
    "Hello, and thanks for contacting claims support. This is a demo with synthetic data, "
    "so please don't share real personal information. Before I can help, I need to verify "
    "your identity: please share at least three of your full name, date of birth, phone "
    "number, email address, or the last four digits of your SSN or national ID."
)


def _ask_identity(f: dict[str, Any]) -> str:
    parts = []
    if f.get("misunderstood"):
        parts.append("Sorry, I didn't quite catch that.")
    if f.get("refused_ack"):
        parts.append("No problem, we can use a different detail instead.")
    if f.get("hint_noted"):
        parts.append("I'll be glad to look into that once I've verified your identity.")
    for label in f.get("unreadable", []):
        if "date of birth" in label:
            parts.append("I couldn't read that date of birth. Could you give it as month, day and year, "
                         "for example January 31, 1980?")
        else:
            parts.append(f"I couldn't read {_your([label])[0]}. Could you give it again?")
    fields = _your(f.get("fields", []))
    if fields and not f.get("unreadable"):
        if f.get("needed", 2) == 1 and len(fields) > 1:
            parts.append(f"To finish verifying, could you give me {_join(fields, 'or')}?")
        else:
            parts.append(f"Could you give me {_join(fields, 'and')}?")
    elif not fields and not f.get("unreadable"):
        parts.append("Could you re-confirm your details so I can verify your identity?")
    return " ".join(parts)


def _verify_failed(f: dict[str, Any]) -> str:
    # Deliberately the same text whichever field failed (§5.2.3).
    return ("I'm sorry, I wasn't able to verify your identity with those details. Could you please "
            "re-confirm them? Any three of your full name, date of birth, phone number, email address, "
            "or the last four digits of your SSN or national ID will do.")


def _ask_rep_details(f: dict[str, Any]) -> str:
    return ("Since you're calling on someone else's behalf, could you tell me your full name "
            "and your relationship to the policyholder?")


_HANDOFF_OFFERS = {
    "human_request": "I can connect you with a human representative. Would you like me to do that?",
    "out_of_scope": ("I'm sorry, I can only help with questions about your insurance claims. Since I haven't "
                     "been able to help with these questions, would you like me to connect you with a human "
                     "representative?"),
    "verification_exhausted": ("I'm sorry, I wasn't able to verify your identity, and for your security I can't "
                               "keep trying in this conversation. I can connect you with a human representative "
                               "instead. Would you like that?"),
    "verification_impossible": ("Without at least three of those details I'm not able to verify your identity "
                                "here. I can connect you with a human representative instead. Would you like that?"),
    "deadline_passed": "Would you like me to connect you with a human representative to discuss your options?",
    "alternatives_exhausted": ("Would you like me to connect you with a human claims representative to review "
                               "the file?"),
    "caller_upset": ("I don't want to keep you going in circles. I'm not able to share claim information here "
                     "without completing these checks, but a human representative can help you another way. "
                     "Would you like me to connect you?"),
}
_HANDOFFS = {
    "consent_timeout": ("I haven't received the policyholder's consent, so I can't share any information on this "
                        "call. I'm transferring you to a human representative who can help further."),
    "no_relationship": ("I'm not able to confirm that you're authorized to discuss this policy, so I can't share "
                        "any information on this call. I'm transferring you to a human representative who can "
                        "help further."),
}
_DEFAULT_HANDOFF = "Okay, I'm transferring you to a human representative now. Thank you for your patience."
SIMULATED_NOTE = " (In this demo the transfer is simulated.)"


_ACKNOWLEDGEMENTS = {
    "frustrated": "I understand this is frustrating, and I'm sorry for the hassle.",
    "angry": "I'm sorry you're upset, and I understand why.",
    "anxious": "I understand this is worrying. I'm here to help.",
    "confused": "Sorry for the confusion, let me make this clearer.",
}
# Why each gate exists, and the acceptable alternatives (other details, a human).
_GATE_REASONS = {
    "identity": ("Claim details are protected, so I have to verify your identity before I can share anything. "
                 "It's what stops someone else from getting your information. Any three of your full name, date "
                 "of birth, phone number, email address, or the last four digits of your SSN or national ID will "
                 "do, so if you'd rather not share one, we can use another. You can also ask for a human "
                 "representative at any time."),
    "consent": ("Because you're calling on someone else's behalf, the policyholder has to agree before I can share "
                "their information. That protects their privacy. You can also ask for a human representative at "
                "any time."),
}

_GATE_REASONS_AGAIN = {
    "identity": ("As I mentioned, I can't share claim details until your identity is verified, because that's what "
                 "keeps your information safe."),
    "consent": "As I mentioned, I need the policyholder's consent before I can share their information.",
}

def _claim_phrase(c: dict[str, str]) -> str:
    return f"{c['case_id']}, a {c['case_type']} claim from {fmt_month(c['created_at'])} that is {c['status']}"


def _ask_intent(f: dict[str, Any]) -> str:
    parts = ["I couldn't find a claim matching that description."] if f.get("no_match") else []
    n, types = f.get("claim_count", 0), f.get("case_types", [])
    if n == 0:
        return " ".join(parts + ["I don't see any claims on your account. A human representative can help "
                                 "if you think that's wrong."])
    if n == 1:
        parts.append(f"I can see one {types[0]} claim on your account.")
    else:
        parts.append(f"I can see {n} claims on your account ({_join(types, 'and')}).")
    parts.append("Which claim are you calling about, and what can I help you with? You can give the claim "
                 "number or describe it, for example the type and the month it was filed.")
    return " ".join(parts)


def _choose_case(f: dict[str, Any]) -> str:
    options = [_claim_phrase(c) for c in f["options"]]
    return f"I found {len(options)} claims that could match: {'; '.join(options)}. Which one are you calling about?"


def _case_confirmed(f: dict[str, Any]) -> str:
    c = f["claim"]
    return f"I found your {c['case_type']} claim {c['case_id']} from {fmt_date(c['created_at'])}."





def _answer_status(f: dict[str, Any]) -> str:
    c = f["claim"]
    parts = []
    if f.get("not_denied"):
        parts.append(f"Claim {c['case_id']} doesn't have a denial on file.")
    parts.append(f"Your {c['case_type']} claim {c['case_id']}, filed {fmt_date(c['created_at'])}, is currently "
                 f"{c['status']}. Summary: {f['summary']}.")
    if f.get("denial_reason"):
        parts.append(f"The reason given was that {f['denial_reason']}.")
    if f.get("net_pay") is not None:
        parts.append(f"The net pay on this claim is {fmt_amount(f['net_pay'])}.")
    if f.get("documents"):
        parts.append(f"To move it forward, we need the {f['documents']}.")
    elif f.get("no_documents_needed"):
        parts.append("No documents are needed from you for this claim right now.")
    return " ".join(parts)


def _answer_denial(f: dict[str, Any]) -> str:
    c = f["claim"]
    parts = [f"Your {c['case_type']} claim {c['case_id']} was denied because {f['denial_reason']}."]
    if f.get("documents"):
        parts.append(f"To have it reconsidered, the {f['documents']} need to be submitted.")
    if f.get("appeal_deadline"):
        when = fmt_date(f["appeal_deadline"])
        if f["deadline_passed"]:
            parts.append(f"The appeal deadline was {when}, which has already passed, so a human representative "
                         "would need to review your options.")
        else:
            days = f["days_remaining"]
            left = "today is the last day" if days == 0 else f"{days} day{'s' if days != 1 else ''} from today"
            parts.append(f"The appeal deadline is {when} ({left}).")
    return " ".join(parts)


def _answer_documents(f: dict[str, Any]) -> str:
    return " ".join([f"For claim {f['claim']['case_id']}, we need the {f['documents']}.", *f.get("guidance", [])])


def _answer_topic(f: dict[str, Any]) -> str:
    return " ".join([f["text"], *f.get("guidance", [])])


def _answer_amount(f: dict[str, Any]) -> str:
    text = f"For claim {f['claim']['case_id']}, the {f['label']} is {fmt_amount(f['value'])}."
    return f"{text} {f['description']}" if f.get("description") else text


def _answer_not_covered(f: dict[str, Any]) -> str:
    c = f["claim"]
    return (f"I don't have specific guidance on that for claim {c['case_id']}. It's currently {c['status']}. "
            f"Summary: {f['summary']}. If you need more detail, a human representative can help.")





def _summary_preview(f: dict[str, Any]) -> str:
    return (f"Here's a summary of our conversation:\n\n{f['preview']}\n\nWould you like me to send it to the email "
            f"on file ({f['email']})? Choose Send summary or Skip.")

TEMPLATES: dict[Action, Callable[[dict[str, Any]], str]] = {
    Action.GREETING: lambda f: GREETING,
    Action.ASK_IDENTITY: _ask_identity,
    Action.VERIFY_FAILED: _verify_failed,
    Action.VERIFIED: lambda f: "Thank you, you're verified.",
    Action.ASK_REP_DETAILS: _ask_rep_details,
    Action.CONSENT_REQUESTED: lambda f: (
        "Thank you. Because you're calling on the policyholder's behalf, I've sent them a consent request. "
        "I'll check on it with each message. Let me know when you're ready."),
    Action.CONSENT_PENDING: lambda f: "I'm still waiting for the policyholder to respond to the consent request.",
    Action.CONSENT_APPROVED: lambda f: "The policyholder has given consent, so I can help you now.",
    Action.ASK_INTENT: _ask_intent,
    Action.CHOOSE_CASE: _choose_case,
    Action.CASE_CONFIRMED: _case_confirmed,
    Action.ANSWER_STATUS: _answer_status,
    Action.ANSWER_DENIAL: _answer_denial,
    Action.ANSWER_DOCUMENTS: _answer_documents,
    Action.ANSWER_TOPIC: _answer_topic,
    Action.ANSWER_ALTERNATIVE: lambda f: f"For the {f['document']}: {f['text']}",
    Action.ANSWER_AMOUNT: _answer_amount,
    Action.ANSWER_NOT_COVERED: _answer_not_covered,
    Action.ASK_ANYTHING_ELSE: lambda f: "Is there anything else I can help you with?",
    Action.SUMMARY_PREVIEW: _summary_preview,
    Action.ASK_SEND_AGAIN: lambda f: (
        f"Sorry, I didn't catch that. Should I send the summary to {f['email']}? Please choose Send summary or Skip."),
    Action.SUMMARY_SENT: lambda f: (
        f"Done. I've sent the summary to {f['email']}. This is simulated, so no real email was sent. "
        "Thanks for contacting claims support, goodbye!"),
    Action.SUMMARY_SKIPPED: lambda f: (
        "No problem, I haven't sent any email. Thanks for contacting claims support, goodbye!"),
    Action.ACTION_UNAVAILABLE: lambda f: "That option isn't available right now.",
    Action.REFUSE_OUT_OF_SCOPE: lambda f: (
        f"I'm sorry, I can only help with questions about your insurance claims. {f['steer']}"),
    Action.SMALL_TALK: lambda f: f"Thanks for asking! {f['steer']}",
    Action.OFFER_HANDOFF: lambda f: _HANDOFF_OFFERS[f["reason"]],
    Action.HANDOFF: lambda f: _HANDOFFS.get(f["reason"], _DEFAULT_HANDOFF) + SIMULATED_NOTE,
    Action.HANDOFF_DECLINED: lambda f: f"No problem. {f['steer']}",
    Action.ACKNOWLEDGE_FEELING: lambda f: _ACKNOWLEDGEMENTS[f["emotion"]],
    Action.EXPLAIN_GATE: lambda f: (_GATE_REASONS_AGAIN if f.get("again") else _GATE_REASONS)[f["gate"]],
    Action.SESSION_ENDED: lambda f: "This conversation has ended. Please start a new session if you need more help.",
}


def steer(session: Session, max_verify_attempts: int) -> str:
    """One sentence pointing the caller back to the current step."""
    if session.phase == Phase.VERIFY_ID:
        if session.consent_status == "pending":
            return "I'm still waiting for the policyholder to respond to the consent request."
        if session.failed_verification_attempts >= max_verify_attempts:
            return ("I'm not able to continue verifying your identity in this conversation, but you can ask "
                    "for a human representative at any time or start a new session.")
        return "Whenever you're ready, I still need a few details to verify your identity."
    if session.phase == Phase.RESOLVE_INTENT:
        return "What can I help you with on your claims today?"
    if session.phase == Phase.PROCESS_CASE:
        return "Is there anything else about your claim I can help with?"
    return "Is there anything else I can help you with?"


def render(step: Step) -> str:
    return TEMPLATES[step.action](step.facts)


def render_all(steps: list[Step]) -> str:
    return " ".join(render(s) for s in steps)
