"""Liest Erscheinungstermine aus Shopseiten: „Erscheint am 14.11.2026“, „Release: KW 47“ …

Termine sind Angaben der Shops und verschieben sich gern – die Oberfläche zeigt sie als „laut Shop“.
"""
from __future__ import annotations

import re
from datetime import date

MONTHS = {"januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6,
          "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
          "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7, "october": 10, "december": 12}
MONTH_NAMES = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
               "November", "Dezember"]

KEYWORDS = (r"(?:erscheint|erscheinungs(?:datum|termin)|release(?:datum|[\s-]?date)?|ver(?:ö|oe)ffentlichung(?:sdatum)?"
            r"|vorverkauf|vorbestellung|(?:lieferbar|verfügbar|verfuegbar|erhältlich|erhaeltlich)\s+ab"
            r"|auslieferung(?:\s+ab)?|versand\s+ab|liefertermin|voraussichtlich)")
GAP = r"[^0-9\n]{0,30}?"          # „ am “, „: “, „ (voraussichtlich) “ …
_month_alt = "|".join(sorted(MONTHS, key=len, reverse=True))

PATTERNS = [
    ("dmy", re.compile(KEYWORDS + GAP + r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4}|\d{2})(?!\d)", re.I)),
    ("iso", re.compile(KEYWORDS + GAP + r"(\d{4})-(\d{2})-(\d{2})", re.I)),
    ("d_month", re.compile(KEYWORDS + r"[^\n]{0,30}?(\d{1,2})\.?\s+(" + _month_alt + r")\s+(\d{4})", re.I)),
    ("month", re.compile(KEYWORDS + r"[^\n]{0,30}?\b(" + _month_alt + r")\s+(\d{4})", re.I)),
    ("week", re.compile(KEYWORDS + GAP + r"\bKW\s?(\d{1,2})(?:\s*[/.\s]\s*(\d{4}))?", re.I)),
]


def find_release(text: str, json_dates: list[str] | None = None, today: date | None = None) -> tuple[str | None, str | None]:
    """Liefert (Anzeige-Text, ISO-Datum) oder (None, None)."""
    today = today or date.today()
    for raw in json_dates or []:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(raw).strip())
        if m:
            found = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if found:
                return found.strftime("%d.%m.%Y"), found.isoformat()

    text = re.sub(r"\s+", " ", text or "")
    for kind, pattern in PATTERNS:
        for m in pattern.finditer(text):
            result = _convert(kind, m.groups(), today)
            if result:
                return result
    return None, None


def _convert(kind: str, groups: tuple, today: date) -> tuple[str, str] | None:
    if kind == "dmy":
        day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
        year += 2000 if year < 100 else 0
        found = _valid(year, month, day)
        return (found.strftime("%d.%m.%Y"), found.isoformat()) if found else None
    if kind == "iso":
        found = _valid(int(groups[0]), int(groups[1]), int(groups[2]))
        return (found.strftime("%d.%m.%Y"), found.isoformat()) if found else None
    if kind == "d_month":
        found = _valid(int(groups[2]), MONTHS[groups[1].lower()], int(groups[0]))
        return (found.strftime("%d.%m.%Y"), found.isoformat()) if found else None
    if kind == "month":
        month, year = MONTHS[groups[0].lower()], int(groups[1])
        found = _valid(year, month, 1)
        return (f"{MONTH_NAMES[month - 1]} {year}", found.isoformat()) if found else None
    if kind == "week":
        week = int(groups[0])
        year = int(groups[1]) if groups[1] else today.year
        if not 1 <= week <= 53:
            return None
        try:
            monday = date.fromisocalendar(year, week, 1)
        except ValueError:
            return None
        if not groups[1] and monday < today.replace(day=1) and today.month >= 10 and week <= 10:
            monday = date.fromisocalendar(year + 1, week, 1)        # „KW 3“ im Herbst = nächstes Jahr
            year += 1
        return f"KW {week}/{year}", monday.isoformat()
    return None


def _valid(year: int, month: int, day: int) -> date | None:
    if not 2015 <= year <= 2040:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None
