"""Academic term helpers.

UTA writes the same term in several ways, and we need to compare them:
  label    "Spring 2023"          (DM profiles, DM/courses year+semester)
  term_key "2023-1"               (our DB: sorts correctly as plain text)
  strm     "2232"                 (PeopleSoft code, seen in S3 syllabus URLs)
  url      "2023-SPRING"          (S3 folder names)

Seasons are numbered 1 = Spring, 2 = Summer, 3 = Fall so that ordering by
term_key follows the calendar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

SEASONS = {"spring": 1, "summer": 2, "fall": 3}
SEASON_NAMES = {1: "Spring", 2: "Summer", 3: "Fall"}
# PeopleSoft's last strm digit per season.
STRM_DIGIT = {1: 2, 2: 5, 3: 8}
STRM_SEASON = {v: k for k, v in STRM_DIGIT.items()}


@dataclass(frozen=True, order=True)
class Term:
    year: int
    season: int  # 1 Spring, 2 Summer, 3 Fall

    def __post_init__(self):
        if self.season not in SEASON_NAMES:
            raise ValueError(f"bad season {self.season}")

    @property
    def key(self) -> str:
        return f"{self.year}-{self.season}"

    @property
    def label(self) -> str:
        return f"{SEASON_NAMES[self.season]} {self.year}"

    @property
    def semester(self) -> str:
        """Value for the DM/courses `semester` parameter."""
        return SEASON_NAMES[self.season]

    @property
    def strm(self) -> str:
        return f"2{self.year % 100:02d}{STRM_DIGIT[self.season]}"

    def next(self) -> Term:
        return Term(self.year + 1, 1) if self.season == 3 else Term(self.year, self.season + 1)

    def __str__(self) -> str:
        return self.label


def parse_term(text: str) -> Term:
    """Accept any of the formats above (case-insensitive)."""
    s = text.strip()
    if m := re.fullmatch(r"(\d{4})-([123])", s):  # term_key
        return Term(int(m[1]), int(m[2]))
    if m := re.fullmatch(r"2(\d{2})([258])", s):  # strm
        return Term(2000 + int(m[1]), STRM_SEASON[int(m[2])])
    if m := re.fullmatch(r"(spring|summer|fall)\s+(\d{4})", s, re.I):  # "Spring 2023"
        return Term(int(m[2]), SEASONS[m[1].lower()])
    if m := re.fullmatch(r"(\d{4})[\s_-]+(spring|summer|fall)", s, re.I):  # "2023 Spring", "2023-SPRING"
        return Term(int(m[1]), SEASONS[m[2].lower()])
    raise ValueError(f"unrecognized term: {text!r}")


def current_term(today: date | None = None) -> Term:
    """Rough calendar guess: Jan–May Spring, Jun–Jul Summer, Aug–Dec Fall."""
    d = today or date.today()
    season = 1 if d.month <= 5 else 2 if d.month <= 7 else 3
    return Term(d.year, season)


def term_range(start: Term | str, end: Term | str) -> list[Term]:
    """Inclusive list of terms from start to end."""
    a = parse_term(start) if isinstance(start, str) else start
    b = parse_term(end) if isinstance(end, str) else end
    out = []
    while a <= b:
        out.append(a)
        a = a.next()
    return out
