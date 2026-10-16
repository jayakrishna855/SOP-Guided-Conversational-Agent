from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.claims.fixtures import Claim, DocumentGuideline, Fixtures

FALLBACK_TOPIC = "claim_followup_fallback"

# Names callers (or claims) use that token matching can't map to a guidance key.
DOCUMENT_ALIASES: dict[str, str] = {
    "path report": "original pathology report",
    "pathology": "original pathology report",
    "lab report": "original pathology report",
    "visit note": "treating provider office note",
    "clinic note": "treating provider office note",
    "doctor note": "treating provider office note",
    "provider note": "treating provider office note",
    "accident photo": "supplemental accident scene photos",
    "scene photo": "supplemental accident scene photos",
    "damage photo": "supplemental accident scene photos",
    "estimate": "repair estimate",
    "repair quote": "repair estimate",
}

_STOPWORDS = {"a", "an", "the", "my", "of", "from", "for"}


def _tokens(text: str) -> tuple[str, ...]:
    words = re.findall(r"[a-z0-9]+", text.casefold().replace("'", "").replace("’", ""))
    # Crude singularisation so "photo" and "photos" agree.
    return tuple(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w not in _STOPWORDS)


def _phrase_in(phrase: str, text: str) -> bool:
    """Phrase occurs in text starting at a word boundary ('format' is not in 'information')."""
    return re.search(r"\b" + re.escape(" ".join(_tokens(phrase))), " ".join(_tokens(text))) is not None


def format_documents(documents: Iterable[str]) -> str:
    docs = list(documents)
    if len(docs) <= 1:
        return "".join(docs)
    if len(docs) == 2:
        return f"{docs[0]} and {docs[1]}"
    return f"{', '.join(docs[:-1])}, and {docs[-1]}"


@dataclass(frozen=True)
class SelectedTopic:
    topic: str  # a claim_followup_guidance topic, or FALLBACK_TOPIC
    text: str  # placeholders already filled
    matched_by: str  # "match_any" | "model" | "fallback"


class Guidance:
    def __init__(self, fixtures: Fixtures):
        self._g: DocumentGuideline = fixtures.guideline
        self._field_descriptions = fixtures.claim_schema.field_descriptions

    

    def resolve_document_key(self, name: str) -> str | None:
        """Map a document name to a document_guidance key.

        Order: exact key, explicit alias, then the key that contains every token
        of the name (fewest extra tokens wins; a tie is ambiguous and returns None).
        Subset matching needs at least two tokens, so a bare word like "report"
        only resolves through the alias map.
        """
        tokens = _tokens(name)
        if not tokens:
            return None
        keys = {_tokens(k): k for k in self._g.document_guidance}
        if tokens in keys:
            return keys[tokens]
        alias = next((target for a, target in DOCUMENT_ALIASES.items() if _tokens(a) == tokens), None)
        if alias in self._g.document_guidance:
            return alias
        if len(tokens) < 2:
            return None
        supersets = sorted((len(kt) - len(tokens), k) for kt, k in keys.items() if set(tokens) <= set(kt))
        if not supersets or (len(supersets) > 1 and supersets[0][0] == supersets[1][0]):
            return None
        return supersets[0][1]

    def documents_mentioned(self, claim: Claim, message: str) -> list[str]:
        """Which of the claim's needed documents the message refers to (claim's own wording)."""
        found = []
        for doc in claim.documents_needed:
            key = self.resolve_document_key(doc)
            names = [doc] + ([key] if key else []) + [a for a, t in DOCUMENT_ALIASES.items() if key and t == key]
            if any(_phrase_in(n, message) for n in names):
                found.append(doc)
        return found


    def submission_guidance(self, claim: Claim) -> list[str]:
        """How to prepare the claim's documents, most specific first:
        each needed document's own guidance, then case-type guidance, then the default."""
        out = []
        for doc in claim.documents_needed:
            key = self.resolve_document_key(doc)
            if key is not None:
                out.append(self._g.document_guidance[key].en)
        if claim.case_type in self._g.case_type_guidance:
            out.append(self._g.case_type_guidance[claim.case_type].en)
        out.append(self._g.default_guidance.en)
        return out

    def alternative_guidance(self, document: str, *, exhausted: bool = False) -> str:
        """What to do when the caller can't get a document. Once the alternatives
        have been tried (`exhausted`), the human-review rule applies instead."""
        if exhausted:
            return self._g.claim_followup_settings.human_review_after_document_alternatives_exhausted.en
        key = self.resolve_document_key(document)
        alternatives = self._g.document_alternative_guidance
        return alternatives[key].en if key in alternatives else alternatives["default"].en


    def eligible_topics(self, claim: Claim, intents: Iterable[str]) -> list[str]:
        wanted = set(intents)
        return [
            t.topic
            for t in self._g.claim_followup_guidance
            if wanted.intersection(t.intent_hints) and (claim.documents_needed or not t.requires_documents)
        ]

    def select_topic(
        self,
        claim: Claim,
        intents: Iterable[str],
        message: str,
        model_topic: str | None = None,
    ) -> SelectedTopic:
        """selection: filter by intent and documents, then a deterministic
        match_any hit (longest phrase wins, file order breaks ties), then the
        model's suggestion if still eligible, else the fallback.

        `intents` is a collection so the caller can pass both the session intent
        and the intent of the current message.
        """
        eligible = self.eligible_topics(claim, intents)
        by_name = {t.topic: t for t in self._g.claim_followup_guidance}

        best: tuple[int, str] | None = None
        for name in eligible:
            for phrase in by_name[name].match_any:
                if _phrase_in(phrase, message) and (best is None or len(phrase) > best[0]):
                    best = (len(phrase), name)
        if best is not None:
            return SelectedTopic(best[1], self.fill(claim, by_name[best[1]].en), "match_any")
        if model_topic in eligible:
            return SelectedTopic(model_topic, self.fill(claim, by_name[model_topic].en), "model")
        return SelectedTopic(FALLBACK_TOPIC, self._g.claim_followup_fallback.en, "fallback")

    def fill(self, claim: Claim, template: str) -> str:
        return template.format(
            case_id=claim.case_id,
            documents=format_documents(claim.documents_needed),
            average_processing_time_after_submission=(
                self._g.claim_followup_settings.average_processing_time_after_submission.en
            ),
        )


    def field_description(self, field: str) -> str | None:
        desc = self._field_descriptions.get(field)
        return desc.description if desc else None
