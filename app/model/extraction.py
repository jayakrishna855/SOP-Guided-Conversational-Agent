from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, WrapValidator, field_validator

IdentityField = Literal["full_name", "dob", "phone", "email", "id_last4"]
Intent = Literal["status_inquiry", "denial_question", "document_submission", "next_steps", "general_claim_question"]

# Names the model may use for the last-4 field; both ID types are one field.
_ID_SYNONYMS = {"ssn_last4": "id_last4", "national_id_last4": "id_last4", "ssn": "id_last4", "last4": "id_last4"}


def _lenient(default: Any) -> WrapValidator:
    def validate(value, handler):
        try:
            return handler(value)
        except ValidationError:
            return default() if callable(default) else default

    return WrapValidator(validate)


def _clean_text(value, handler):
    """Strings (and bare numbers, e.g. a last-4 sent as 4472) are trimmed; blanks and
    anything else (lists, objects, booleans) become None."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return handler(str(value).strip() or None)


Text = Annotated[str | None, WrapValidator(_clean_text)]


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class IdentityExtract(_Lenient):
    full_name: Text = None
    dob: Text = None
    phone: Text = None
    email: Text = None
    id_last4: Text = None
    policy_number: Text = None

    def given(self) -> dict[str, str]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class RepInfoExtract(_Lenient):
    rep_name: Text = None
    relationship: Text = None
    policyholder_name: Text = None


class CaseHintsExtract(_Lenient):
    case_type: Annotated[Literal["healthcare", "dental", "auto"] | None, _lenient(None)] = None  # CaseType
    status: Annotated[Literal["denied", "open", "closed"] | None, _lenient(None)] = None  # CaseStatus
    month: Annotated[int | None, Field(ge=1, le=12), _lenient(None)] = None
    year: Annotated[int | None, Field(ge=1900, le=2100), _lenient(None)] = None
    claim_id: Text = None


class Extraction(_Lenient):
    identity: Annotated[IdentityExtract, _lenient(IdentityExtract)] = Field(default_factory=IdentityExtract)
    caller_role: Annotated[Literal["policyholder", "representative", "unknown"], _lenient("unknown")] = "unknown"
    rep_info: Annotated[RepInfoExtract, _lenient(RepInfoExtract)] = Field(default_factory=RepInfoExtract)
    intent: Annotated[Intent | Literal["none"], _lenient("none")] = "none"
    case_hints: Annotated[CaseHintsExtract, _lenient(CaseHintsExtract)] = Field(default_factory=CaseHintsExtract)
    # Checked against the eligible guidance topics later, so any text is kept here.
    followup_topic: Text = None
    scope: Annotated[Literal["in_scope", "out_of_scope", "small_talk"], _lenient("in_scope")] = "in_scope"
    wants_human: Annotated[bool, _lenient(False)] = False
    # The caller signals they're finished ("that's all", "thanks, bye"). Added in M2b:
    # 5.4.8 needs it and nothing else in the schema carries it.
    done: Annotated[bool, _lenient(False)] = False
    refused_fields: Annotated[list[str], _lenient(list)] = Field(default_factory=list)
    confirmation: Annotated[Literal["yes", "no", "none"], _lenient("none")] = "none"

    @field_validator("identity", mode="before")
    @classmethod
    def _identity_synonyms(cls, value):
        # Accept ssn_last4 / national_id_last4 as id_last4.
        if isinstance(value, dict):
            value = dict(value)
            for alias, name in _ID_SYNONYMS.items():
                if alias in value and value.get(name) in (None, ""):
                    value[name] = value.pop(alias)
        return value

    @field_validator("wants_human", "done", mode="before")
    @classmethod
    def _strict_bool(cls, value):
        # "maybe" or 1 shouldn't count as asking for a human.
        return value if isinstance(value, bool) else False

    @field_validator("followup_topic", mode="after")
    @classmethod
    def _none_topic(cls, value: str | None) -> str | None:
        return None if value == "none" else value

    @field_validator("refused_fields", mode="after")
    @classmethod
    def _known_fields_only(cls, value: list[str]) -> list[str]:
        allowed = IdentityField.__args__
        out: list[str] = []
        for item in value:
            name = _ID_SYNONYMS.get(item, item)
            if name in allowed and name not in out:
                out.append(name)
        return out

    @property
    def intent_label(self) -> str | None:
        return None if self.intent == "none" else self.intent


EMPTY_EXTRACTION = Extraction()

CaseType = Literal["healthcare", "dental", "auto"]
CaseStatus = Literal["denied", "open", "closed"]


def extraction_json_schema(followup_topics: list[str]) -> dict[str, Any]:
    """The same schema as `Extraction`, written for strict structured outputs:
    every property required, nullable where optional, no extra keys."""

    def obj(props: dict[str, Any]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

    def text() -> dict[str, Any]:
        return {"type": ["string", "null"]}

    def enum(values, nullable=False) -> dict[str, Any]:
        values = list(values)
        if nullable:
            return {"type": ["string", "null"], "enum": [*values, None]}
        return {"type": "string", "enum": values}

    return obj({
        "identity": obj({f: text() for f in IdentityExtract.model_fields}),
        "caller_role": enum(["policyholder", "representative", "unknown"]),
        "rep_info": obj({f: text() for f in RepInfoExtract.model_fields}),
        "intent": enum([*Intent.__args__, "none"]),
        "case_hints": obj({
            "case_type": enum(CaseType.__args__, nullable=True),
            "status": enum(CaseStatus.__args__, nullable=True),
            "month": {"type": ["integer", "null"]},
            "year": {"type": ["integer", "null"]},
            "claim_id": text(),
        }),
        "followup_topic": enum([*followup_topics, "none"]),
        "scope": enum(["in_scope", "out_of_scope", "small_talk"]),
        "wants_human": {"type": "boolean"},
        "done": {"type": "boolean"},
        "refused_fields": {"type": "array", "items": enum(IdentityField.__args__)},
        "confirmation": enum(["yes", "no", "none"]),
    })


def parse_extraction(raw: str | dict | None) -> tuple[Extraction, bool]:
    """Parse the model's raw output. Returns (extraction, ok); on invalid JSON or a
    non-object, the extraction is empty and ok is False (7.3)."""
    if raw is None:
        return EMPTY_EXTRACTION, False
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return EMPTY_EXTRACTION, False
    if not isinstance(raw, dict):
        return EMPTY_EXTRACTION, False
    return Extraction.model_validate(raw), True
