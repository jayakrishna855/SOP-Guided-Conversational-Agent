from __future__ import annotations

import datetime as dt
import secrets
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Phase(StrEnum):
    VERIFY_ID = "VERIFY_ID"
    RESOLVE_INTENT = "RESOLVE_INTENT"
    PROCESS_CASE = "PROCESS_CASE"
    POST_PROCESS = "POST_PROCESS"
    END = "END"  # handed off or finished; accepts no further turns


SlotStatus = Literal["unchecked", "matched", "mismatched"]
CallerRole = Literal["policyholder", "representative", "unknown"]
ConsentState = Literal["none", "pending", "approved", "timeout"]
# Why a human handoff was offered (and is waiting for a yes/no) or happened.
HandoffReason = Literal[
    "human_request",
    "out_of_scope",
    "verification_exhausted",
    "verification_impossible",
    "consent_timeout",
    "no_relationship",
    "deadline_passed",
    "alternatives_exhausted",
    "caller_upset",  # repeated pushback on verification or consent
]


class IdentitySlot(BaseModel):
    value: str
    status: SlotStatus = "unchecked"


class RepInfo(BaseModel):
    rep_name: str | None = None
    relationship: str | None = None
    policyholder_name: str | None = None


class CaseHints(BaseModel):
    case_type: str | None = None
    status: str | None = None
    month: int | None = None
    year: int | None = None
    claim_id: str | None = None

    def given(self) -> dict[str, str | int]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class DiscussedTopic(BaseModel):
    case_id: str
    topic: str  # an answer kind ("status", "denial_reason", ...) or a guidance topic


class FollowUp(BaseModel):
    case_id: str
    text: str  # a next step for the caller, built from fixture data


class TranscriptEntry(BaseModel):
    role: Literal["caller", "agent"]
    text: str


def _new_id() -> str:
    return secrets.token_urlsafe(16)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Session(BaseModel):
    id: str = Field(default_factory=_new_id)
    phase: Phase = Phase.VERIFY_ID
    created_at: dt.datetime = Field(default_factory=_now)

   
    identity_slots: dict[str, IdentitySlot] = Field(default_factory=dict, repr=False)
    refused_fields: list[str] = Field(default_factory=list)
    candidate_party_id: str | None = None
    verified_party_id: str | None = None
    failed_verification_attempts: int = 0

    # Representative path.
    caller_role: CallerRole = "unknown"
    rep_info: RepInfo = Field(default_factory=RepInfo, repr=False)
    consent_status: ConsentState = "none"
    consent_poll_index: int = 0

    # Cross-phase memory.
    intent_hints: list[str] = Field(default_factory=list)
    case_hints: CaseHints = Field(default_factory=CaseHints)

    # Case handling. Topics and follow-ups are per claim, for the email summary.
    active_case_id: str | None = None
    candidate_case_ids: list[str] = Field(default_factory=list)
    discussed_topics: list[DiscussedTopic] = Field(default_factory=list)
    followups: list[FollowUp] = Field(default_factory=list)

    out_of_scope_streak: int = 0
    pushback_streak: int = 0
    # Consecutive turns pushing back on verification or consent (support.py).
    # A handoff offer waiting for the caller's yes/no, and why the session ended.
    pending_offer: HandoffReason | None = None
    end_reason: HandoffReason | Literal["completed"] | None = None

    email_choice: Literal["none", "sent", "skipped"] = "none"
    email_preview: str | None = Field(default=None, repr=False)

    transcript: list[TranscriptEntry] = Field(default_factory=list, repr=False)

    @property
    def verified(self) -> bool:
        return self.verified_party_id is not None

    def discussed(self, case_id: str, topic: str) -> bool:
        return any(d.case_id == case_id and d.topic == topic for d in self.discussed_topics)

    def note_topic(self, case_id: str, topic: str) -> None:
        if not self.discussed(case_id, topic):
            self.discussed_topics.append(DiscussedTopic(case_id=case_id, topic=topic))

    def note_followup(self, case_id: str, text: str) -> None:
        if not any(f.case_id == case_id and f.text == text for f in self.followups):
            self.followups.append(FollowUp(case_id=case_id, text=text))
