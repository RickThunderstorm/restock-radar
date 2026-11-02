from datetime import date

import pytest

from restockradar.release import find_release

TODAY = date(2026, 11, 2)


@pytest.mark.parametrize("text,expected", [
    ("Erscheint am 14.11.2026 – jetzt vorbestellen", ("14.11.2026", "2026-11-14")),
    ("Erscheinungsdatum: 5.12.26", ("05.12.2026", "2026-12-05")),
    ("VORVERKAUF – Release 23. Januar 2027", ("23.01.2027", "2027-01-23")),
    ("Release-Date: 2027-03-07", ("07.03.2027", "2027-03-07")),
    ("Voraussichtlich lieferbar ab KW 47", ("KW 47/2026", "2026-11-16")),
    ("Auslieferung ab KW 3/2027", ("KW 3/2027", "2027-01-18")),
    ("Erscheint voraussichtlich im Dezember 2026", ("Dezember 2026", "2026-12-01")),
    ("Release in KW 2", ("KW 2/2027", "2027-01-11")),          # im Herbst: Januar-KW = nächstes Jahr
])
def test_finds_release_dates(text, expected):
    assert find_release(text, today=TODAY) == expected


@pytest.mark.parametrize("text", [
    "Versand ab 50 € kostenlos",
    "Lieferung in 1-3 Werktagen",
    "Artikelnummer 14.11.2026 ohne Schlüsselwort",
    "Erscheint am 31.02.2026",                                 # gibt es nicht
    "",
])
def test_ignores_non_dates(text):
    assert find_release(text, today=TODAY) == (None, None)


def test_json_date_wins():
    assert find_release("Erscheint am 01.12.2026", ["2026-11-28T00:00:00+01:00"], today=TODAY) == ("28.11.2026", "2026-11-28")
