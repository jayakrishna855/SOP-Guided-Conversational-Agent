from __future__ import annotations

import re

from app.claims.fixtures import Fixtures

_CLAIM_ID = re.compile(r"\bCL[\s-]?\d+\b|\bclaim\s*(?:number|no\.?|#)?\s*#?\s*\d{3,}\b", re.IGNORECASE)
_AMOUNT = re.compile(
    r"[$€£]\s*\d|\b\d[\d,]*\.\d{2}\b|\b\d[\d,]*\s*(?:usd|dollars?|bucks)\b", re.IGNORECASE
)
_STATUS_WORDS = re.compile(
    r"\b(?:denied|denial|approved|open|closed|settled|completed|paid|in progress|rejected)\b", re.IGNORECASE
)
_CLAIM_WORD = re.compile(r"\bclaims?\b", re.IGNORECASE)
_SENTENCES = re.compile(r"[^.!?\n]+")
_WINDOW = 5  # words of a denial reason or summary that count as quoting it


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


class LeakGuard:
    def __init__(self, fixtures: Fixtures):
        self._documents = sorted(
            {d.casefold() for c in fixtures.claims for d in c.documents_needed}
            | {k.casefold() for k in fixtures.guideline.document_guidance},
            key=len,
            reverse=True,
        )
        # Every run of _WINDOW words (or the whole text, if shorter) of each denial reason and summary.
        self._windows: set[str] = set()
        for c in fixtures.claims:
            for text in filter(None, (c.denial_reason, c.summary)):
                w = _words(text)
                self._windows.update(" ".join(w[i : i + _WINDOW]) for i in range(max(1, len(w) - _WINDOW + 1)))
        # Whole-number forms of fixture amounts ("1450", "1,450") that the patterns above miss.
        self._amounts = set()
        for c in fixtures.claims:
            for value in (c.expected_reimbursement_amount, c.allowed_max_amount, c.net_pay, c.net_fee):
                whole = value.split(".")[0]
                if len(whole) >= 3:
                    self._amounts.update({whole, f"{int(whole):,}"})

    def scan(self, text: str) -> list[str]:
        """Categories found (empty means the reply is safe to show)."""
        hits = []
        if _CLAIM_ID.search(text):
            hits.append("claim_id")
        if _AMOUNT.search(text) or any(re.search(rf"(?<![\d,]){re.escape(a)}(?![\d,])", text) for a in self._amounts):
            hits.append("amount")
        if any(_CLAIM_WORD.search(s) and _STATUS_WORDS.search(s) for s in _SENTENCES.findall(text)):
            hits.append("claim_status")
        folded = " ".join(_words(text))
        padded = f" {folded} "
        if any(f" {w} " in padded for w in self._windows):
            hits.append("claim_detail")
        if any(re.search(rf"\b{re.escape(' '.join(_words(d)))}s?\b", folded) for d in self._documents):
            hits.append("document")
        return hits
