from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.claims.fixtures import Fixtures
from app.claims.guidance import DOCUMENT_ALIASES, Guidance

_CLAIM_ID = re.compile(r"\bCL[\s-]?(\d+)\b", re.IGNORECASE)
_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)|\b(\d[\d,]*\.\d{2})\b")
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_MONTH_ABBR = r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), lambda m: (int(m[3]), int(m[1]), int(m[2]))),
    (re.compile(rf"\b({_MONTHS}|{_MONTH_ABBR})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE),
     lambda m: (int(m[3]), _month_number(m[1]), int(m[2]))),
    (re.compile(rf"\b(\d{{1,2}})\s+({_MONTHS}|{_MONTH_ABBR})\.?,?\s+(\d{{4}})\b", re.IGNORECASE),
     lambda m: (int(m[3]), _month_number(m[2]), int(m[1]))),
]
STATUS_WORDS = ("denied", "approved", "open", "closed", "settled", "completed", "paid", "rejected", "pending",
                "in progress")
_STATUS = re.compile(r"\b(" + "|".join(STATUS_WORDS) + r")\b", re.IGNORECASE)


def _month_number(name: str) -> int:
    name = name.casefold()[:3]
    return [m[:3] for m in _MONTHS.split("|")].index(name) + 1


def _ids(text: str) -> set[str]:
    return {f"CL-{n}" for n in _CLAIM_ID.findall(text)}


def _amounts(text: str) -> set[Decimal]:
    out = set()
    for a, b in _MONEY.findall(text):
        try:
            out.add(Decimal((a or b).replace(",", "")))
        except InvalidOperation:
            pass
    return out


def _dates(text: str) -> set[dt.date]:
    out = set()
    for pattern, parts in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                out.add(dt.date(*parts(m)))
            except ValueError:
                pass
    return out


def _statuses(text: str) -> set[str]:
    return {w.casefold() for w in _STATUS.findall(text)}


def context_text(facts: Any) -> str:
    return json.dumps(facts, ensure_ascii=False, default=str)


class GroundingValidator:
    def __init__(self, fixtures: Fixtures, guidance: Guidance):
        self._guidance = guidance
        names = {d for c in fixtures.claims for d in c.documents_needed}
        names |= set(fixtures.guideline.document_guidance) | set(DOCUMENT_ALIASES)
        # Longest first, so "original pathology report" is found before "pathology report".
        self._documents = sorted(names, key=len, reverse=True)

    def _document_keys(self, text: str) -> set[str]:
        folded = " ".join(re.findall(r"[a-z0-9]+", text.casefold()))
        found = set()
        for name in self._documents:
            pattern = rf"\b{re.escape(' '.join(re.findall(r'[a-z0-9]+', name.casefold())))}s?\b"
            if re.search(pattern, folded):
                found.add(self._guidance.resolve_document_key(name) or name.casefold())
                folded = re.sub(pattern, " ", folded)
        return found

    def check(self, reply: str, facts: Any) -> list[str]:
        """Problems found (empty means grounded)."""
        context = context_text(facts)
        problems = []
        if _ids(reply) - _ids(context):
            problems.append("claim_id")
        if _amounts(reply) - _amounts(context):
            problems.append("amount")
        if _dates(reply) - _dates(context):
            problems.append("date")
        if self._document_keys(reply) - self._document_keys(context):
            problems.append("document")
        if _statuses(reply) - _statuses(context):
            problems.append("status")
        return problems
