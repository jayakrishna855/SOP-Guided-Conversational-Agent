from __future__ import annotations

import datetime as dt
from decimal import Decimal


def fmt_date(value: dt.date | str) -> str:
    d = dt.date.fromisoformat(value) if isinstance(value, str) else value
    return f"{d:%B} {d.day}, {d.year}"


def fmt_month(value: dt.date | str) -> str:
    d = dt.date.fromisoformat(value) if isinstance(value, str) else value
    return f"{d:%B} {d.year}"


def fmt_amount(value: str) -> str:
    return f"${Decimal(value):,.2f}"


def mask_email(email: str) -> str:
    """'margaret@email.com' -> 'm***@email.com'."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"
