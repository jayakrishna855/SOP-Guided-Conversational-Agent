from __future__ import annotations

import datetime as dt
import json
import re
import string
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, field_validator, model_validator

CaseType = Literal["healthcare", "dental", "auto"]
ClaimStatus = Literal["denied", "open", "closed"]
IdType = Literal["ssn_last4", "national_id_last4"]
Intent = Literal[
    "status_inquiry",
    "denial_question",
    "document_submission",
    "next_steps",
    "general_claim_question",
]

AMOUNT_RE = re.compile(r"^\d+\.\d{2}$")
GUIDANCE_PLACEHOLDERS = {"case_id", "documents", "average_processing_time_after_submission"}


class FixtureError(RuntimeError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Text(_Strict):
    en: str = Field(min_length=1)



class Policyholder(_Strict):
    party_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    name_aliases: tuple[str, ...] = ()
    policy_number: str = Field(min_length=1)
    dob: dt.date
    id_type: IdType
    id_last4: str = Field(pattern=r"^\d{4}$")
    phone: str = Field(pattern=r"^\+\d{8,15}$")
    phone_aliases: tuple[str, ...] = ()
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")
    email_aliases: tuple[str, ...] = ()



class Claim(_Strict):
    case_id: str = Field(pattern=r"^CL-\d+$")
    party_id: str = Field(min_length=1)
    case_type: CaseType
    created_at: dt.date
    status: ClaimStatus
    summary: str
    denial_reason: str | None = None
    documents_needed: tuple[str, ...] = ()
    appeal_deadline: dt.date | None = None
    expected_reimbursement_amount: str
    allowed_max_amount: str
    net_pay: str
    net_fee: str

    @field_validator("expected_reimbursement_amount", "allowed_max_amount", "net_pay", "net_fee")
    @classmethod
    def _amount(cls, value: str) -> str:
        if not AMOUNT_RE.match(value):
            raise ValueError(f"amount must be a decimal string like '780.00', got {value!r}")
        return value




class Representative(_Strict):
    rep_name: str = Field(min_length=1)
    relationship: str = Field(min_length=1)
    buyer_name: str = Field(min_length=1)
    buyer_party_id: str = Field(min_length=1)




class ConsentScenario(_Strict):
    status_sequence: tuple[Literal["pending", "approved"], ...] = Field(min_length=1)




class FollowupTopic(_Strict):
    topic: str = Field(min_length=1)
    intent_hints: tuple[Intent, ...] = Field(min_length=1)
    requires_documents: bool = False
    match_any: tuple[str, ...] = ()
    en: str = Field(min_length=1)

    @field_validator("en")
    @classmethod
    def _known_placeholders(cls, value: str) -> str:
        names = {name for _, name, _, _ in string.Formatter().parse(value) if name is not None}
        unknown = names - GUIDANCE_PLACEHOLDERS
        if unknown:
            raise ValueError(f"unknown placeholder(s) {sorted(unknown)}")
        return value


class FollowupSettings(_Strict):
    average_processing_time_after_submission: Text
    human_review_after_document_alternatives_exhausted: Text


class DocumentGuideline(_Strict):
    default_guidance: Text
    case_type_guidance: dict[CaseType, Text]
    document_guidance: dict[str, Text]
    document_alternative_guidance: dict[str, Text]
    claim_followup_settings: FollowupSettings
    claim_followup_guidance: tuple[FollowupTopic, ...]
    claim_followup_fallback: Text

    @model_validator(mode="after")
    def _check(self) -> "DocumentGuideline":
        if "default" not in self.document_alternative_guidance:
            raise ValueError("document_alternative_guidance needs a 'default' entry")
        topics = [t.topic for t in self.claim_followup_guidance]
        if len(topics) != len(set(topics)):
            raise ValueError("claim_followup_guidance topics must be unique")
        return self




class FieldDescription(_Strict):
    type: str
    example: str
    description: str = Field(min_length=1)


class ClaimSchema(_Strict):
    notes: tuple[str, ...] = ()
    field_descriptions: dict[str, FieldDescription]



class Fixtures(_Strict):
    policyholders: tuple[Policyholder, ...]
    claims: tuple[Claim, ...]
    representatives: tuple[Representative, ...]
    consent_scenarios: dict[str, ConsentScenario]
    guideline: DocumentGuideline
    claim_schema: ClaimSchema

    def policyholder(self, party_id: str) -> Policyholder | None:
        return next((p for p in self.policyholders if p.party_id == party_id), None)


_FILES: dict[str, tuple[str, type]] = {
    "policyholders": ("policyholders.json", RootModel[tuple[Policyholder, ...]]),
    "claims": ("claims.json", RootModel[tuple[Claim, ...]]),
    "representatives": ("representatives.json", RootModel[tuple[Representative, ...]]),
    "consent_scenarios": ("consent_scenarios.json", RootModel[dict[str, ConsentScenario]]),
    "guideline": ("required_document_guideline.json", DocumentGuideline),
    "claim_schema": ("claim_schema.json", ClaimSchema),
}


def _load_file(directory: Path, filename: str, model: type) -> object:
    path = directory / filename
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FixtureError(f"{filename}: file not found in {directory}") from None
    except json.JSONDecodeError as exc:
        raise FixtureError(f"{filename}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from None
    try:
        parsed = model.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
        )
        raise FixtureError(f"{filename}: {problems}") from None
    return parsed.root if isinstance(parsed, RootModel) else parsed


def _cross_check(fx: Fixtures) -> None:
    def unique(values: list[str], what: str, filename: str) -> None:
        dupes = sorted({v for v in values if values.count(v) > 1})
        if dupes:
            raise FixtureError(f"{filename}: duplicate {what}: {dupes}")

    unique([p.party_id for p in fx.policyholders], "party_id", "policyholders.json")
    unique([p.policy_number for p in fx.policyholders], "policy_number", "policyholders.json")
    unique([c.case_id for c in fx.claims], "case_id", "claims.json")

    parties = {p.party_id: p for p in fx.policyholders}
    for claim in fx.claims:
        if claim.party_id not in parties:
            raise FixtureError(f"claims.json: {claim.case_id} references unknown party_id {claim.party_id!r}")
    for rep in fx.representatives:
        holder = parties.get(rep.buyer_party_id)
        if holder is None:
            raise FixtureError(f"representatives.json: {rep.rep_name} references unknown party_id {rep.buyer_party_id!r}")
        if rep.buyer_name.casefold() != holder.name.casefold():
            raise FixtureError(
                f"representatives.json: buyer_name {rep.buyer_name!r} does not match {rep.buyer_party_id}'s name"
            )


def load_fixtures(directory: Path) -> Fixtures:
    loaded = {key: _load_file(directory, filename, model) for key, (filename, model) in _FILES.items()}
    fixtures = Fixtures(**loaded)
    _cross_check(fixtures)
    return fixtures
