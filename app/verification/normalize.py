from __future__ import annotations

import datetime as dt
import re
import unicodedata

# Numeric dates are read US-style (month first) when ambiguous.
_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d",
    "%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y",
    "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y",
    "%B %d, %Y", "%b %d, %Y",
)
_ORDINAL_RE = re.compile(r"(\d{1,2})(st|nd|rd|th)\b", re.IGNORECASE)


def normalize_name(value: str) -> str | None:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"[.,]", " ", text)
    text = " ".join(text.split())
    return text or None


def normalize_dob(value: str) -> str | None:
    """Any common date format to ISO 'YYYY-MM-DD'."""
    text = _ORDINAL_RE.sub(r"\1", " ".join(value.strip().split())).replace(" ,", ",")
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_phone(value: str, default_country_code: str = "1") -> str | None:
    """To E.164, assuming a US number when no country code is given."""
    text = value.strip()
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    if text.startswith("00") and 10 <= len(digits) - 2 <= 15:
        return f"+{digits[2:]}"
    if len(digits) == 10:
        return f"+{default_country_code}{digits}"
    if len(digits) == 11 and digits.startswith(default_country_code):
        return f"+{digits}"
    return None


def normalize_email(value: str) -> str | None:
    text = value.strip().casefold()
    if text.startswith("mailto:"):
        text = text[len("mailto:"):]
    return text if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text) else None


def normalize_last4(value: str) -> str | None:
    """Exactly four digits once separators are removed. Longer input is rejected
    rather than truncated, so a full SSN is never quietly accepted."""
    digits = re.sub(r"[\s-]", "", value.strip())
    return digits if re.fullmatch(r"\d{4}", digits) else None


def normalize_policy_number(value: str) -> str | None:
    text = re.sub(r"[\s_]", "", value.strip().upper())
    match = re.fullmatch(r"(?:POL-?)?(\d+)", text)
    return f"POL-{match.group(1)}" if match else None
