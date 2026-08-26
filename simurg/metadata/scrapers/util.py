"""Shared helpers for scrapers (ISSN/date/issue parsing)."""

from __future__ import annotations

import re

MONTHS = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}
MONTH_ABBR = {m: i for i, m in MONTHS.items()}
for _i, _m in list(MONTHS.items()):
    MONTH_ABBR[_m[:3].lower()] = _i


def clean_issn(issn) -> str | None:
    """Return a normalized '1234-567X' ISSN or None."""
    if not issn:
        return None
    s = str(issn).strip().upper()
    m = re.search(r"\d{4}-\d{3}[\dX]", s)
    return m.group(0) if m else None


def year_from(value) -> int | None:
    if not value:
        return None
    m = re.search(r"(\d{4})", str(value))
    return int(m.group(1)) if m else None


def date_precision(date) -> str | None:
    """Map an issue date string to Simurg's day/month/year precision."""
    if not date:
        return None
    s = str(date).strip()
    if re.match(r"\d{4}-\d{2}-\d{2}$", s):
        return "day"
    if re.match(r"\d{4}-\d{2}$", s):
        return "month"
    if re.match(r"\d{4}$", s):
        return "year"
    return None


def normalize_issue_date(raw) -> tuple[str | None, str | None]:
    """Normalize a messy issue date to ('YYYY-MM-DD'|'YYYY-MM'|'YYYY', precision)."""
    if not raw:
        return None, None
    s = str(raw).strip()
    # ISO already
    if re.match(r"\d{4}-\d{2}(-\d{2})?$", s):
        return s[:10] if len(s) >= 10 else s, date_precision(s)
    # "Month Year" / "Month DD, Year"
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m:
        mi = MONTH_ABBR.get(m.group(1)[:3].lower())
        if mi:
            return f"{m.group(3)}-{mi:02d}-{int(m.group(2)):02d}", "day"
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{4})", s)
    if m:
        mi = MONTH_ABBR.get(m.group(1)[:3].lower())
        if mi:
            return f"{m.group(2)}-{mi:02d}", "month"
    y = year_from(s)
    if y:
        return str(y), "year"
    return None, None


def issue_label(issue_date, precision, volume=None, issue_number=None) -> str:
    """Human label for a magazine issue, e.g. 'June 2020' or 'June 2020 Vol 12 Issue 3'."""
    parts = []
    if issue_date:
        if precision == "month" and "-" in issue_date:
            y, mo = issue_date.split("-")[:2]
            parts.append(f"{MONTHS[int(mo)]} {y}")
        elif precision == "day" and issue_date.count("-") == 2:
            y, mo, d = issue_date.split("-")
            parts.append(f"{MONTHS[int(mo)]} {int(d)}, {y}")
        else:
            parts.append(str(issue_date))
    if volume:
        parts.append(f"Vol {volume}")
    if issue_number:
        parts.append(f"Issue {issue_number}")
    return " ".join(parts)
