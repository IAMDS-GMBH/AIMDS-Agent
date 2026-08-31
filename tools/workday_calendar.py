"""Working days, DACH public holidays and target hours — one stdlib module.

The agent used to *type* calendar facts into SQL: weekdays per month counted
in a comment, Easter-derived holidays from memory (a week off), the federal
state guessed. This module is the single source those facts come from — for
the prompt's "today is a holiday" line (``hermes_time``) and for the
``workdays`` tool.

Regions are codes: ``DE`` / ``DE-BY`` … (16 Länder), ``AT`` / ``AT-W`` …
(9 Bundesländer), ``CH`` / ``CH-ZH`` … (26 cantons). A bare country code
yields the nationwide holidays only.

Holiday ``kind``:
* ``statutory`` — a public holiday for everyone in that region (deducted
  from working days),
* ``regional`` — a day off by regional custom/law that not every employer
  grants (Austrian Landespatrone; listed, deducted only when the region code
  names that state),
* ``partial`` — a holiday only in parts of the region (Swiss cantons with
  municipal differences, German municipal holidays); listed, **never**
  deducted — the caller adds it via ``extra_holidays`` when it applies.

Nothing here is a legal opinion: the tables follow the published lists
(Bundesministerium des Innern, help.gv.at, Bundesamt für Justiz) as of 2026;
``source`` in every result says "built-in table".
"""

from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

KIND_STATUTORY = "statutory"
KIND_REGIONAL = "regional"
KIND_PARTIAL = "partial"

SOURCE = "built-in table (DE/AT/CH, 2026 lists)"

DEFAULT_WEEKLY_HOURS = 40.0
DEFAULT_DAYS_PER_WEEK = 5
DEFAULT_HALF_DAYS: Tuple[str, ...] = ("12-24", "12-31")

# ISO weekday tokens (Mon=1 … Sun=7): English two-letter plus the German
# aliases that differ (mo/fr/sa are shared between both languages).
WEEKDAY_TOKENS: Dict[str, int] = {
    "mo": 1, "tu": 2, "we": 3, "th": 4, "fr": 5, "sa": 6, "su": 7,
    "di": 2, "mi": 3, "do": 4, "so": 7,
}
_WEEKDAY_HINT = "valid weekday tokens: mo, tu, we, th, fr, sa, su (German di, mi, do, so) or ISO numbers 1-7"


def parse_work_weekdays(value: Any) -> Optional[frozenset]:
    """``["mo", "di", 3]`` → ``frozenset({1, 2, 3})``; empty input → ``None``.

    Accepts a list or a comma-separated string of two-letter tokens (longer
    words like "monday"/"dienstag" match by their first two letters) and ISO
    numbers 1-7. Raises ``ValueError`` naming the valid tokens — never guesses.
    """
    if value in (None, "", [], ()):
        return None
    items = value.replace(";", ",").split(",") if isinstance(value, str) else list(value)
    out = set()
    for item in items:
        text = "" if item is None else str(item).strip().lower()
        if not text:
            continue
        key = text if text in WEEKDAY_TOKENS else text[:2]
        if text.isalpha() and key in WEEKDAY_TOKENS:
            out.add(WEEKDAY_TOKENS[key])
            continue
        try:
            iso = int(text)
        except ValueError:
            raise ValueError(f"unknown weekday '{item}'; {_WEEKDAY_HINT}") from None
        if not 1 <= iso <= 7:
            raise ValueError(f"weekday number {iso} out of range; {_WEEKDAY_HINT}")
        out.add(iso)
    return frozenset(out) or None

# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------

DE_STATES: Dict[str, str] = {
    "BW": "Baden-Württemberg", "BY": "Bayern", "BE": "Berlin", "BB": "Brandenburg",
    "HB": "Bremen", "HH": "Hamburg", "HE": "Hessen", "MV": "Mecklenburg-Vorpommern",
    "NI": "Niedersachsen", "NW": "Nordrhein-Westfalen", "RP": "Rheinland-Pfalz",
    "SL": "Saarland", "SN": "Sachsen", "ST": "Sachsen-Anhalt", "SH": "Schleswig-Holstein",
    "TH": "Thüringen",
}
AT_STATES: Dict[str, str] = {
    "B": "Burgenland", "K": "Kärnten", "NÖ": "Niederösterreich", "OÖ": "Oberösterreich",
    "S": "Salzburg", "ST": "Steiermark", "T": "Tirol", "V": "Vorarlberg", "W": "Wien",
}
CH_CANTONS: Dict[str, str] = {
    "AG": "Aargau", "AI": "Appenzell Innerrhoden", "AR": "Appenzell Ausserrhoden", "BE": "Bern",
    "BL": "Basel-Landschaft", "BS": "Basel-Stadt", "FR": "Freiburg", "GE": "Genf", "GL": "Glarus",
    "GR": "Graubünden", "JU": "Jura", "LU": "Luzern", "NE": "Neuenburg", "NW": "Nidwalden",
    "OW": "Obwalden", "SG": "St. Gallen", "SH": "Schaffhausen", "SO": "Solothurn", "SZ": "Schwyz",
    "TG": "Thurgau", "TI": "Tessin", "UR": "Uri", "VD": "Waadt", "VS": "Wallis", "ZG": "Zug", "ZH": "Zürich",
}
COUNTRIES: Dict[str, str] = {"DE": "Deutschland", "AT": "Österreich", "CH": "Schweiz"}

_AT_ALIASES = {"NOE": "NÖ", "OOE": "OÖ", "N": "NÖ", "O": "OÖ", "STMK": "ST", "SBG": "S", "VBG": "V", "KTN": "K", "BGLD": "B"}

_ALL_CH = frozenset(CH_CANTONS)


def valid_regions() -> List[str]:
    out = list(COUNTRIES)
    out += [f"DE-{c}" for c in DE_STATES]
    out += [f"AT-{c}" for c in AT_STATES]
    out += [f"CH-{c}" for c in CH_CANTONS]
    return out


def region_name(region: str) -> str:
    country, _, sub = region.partition("-")
    if not sub:
        return COUNTRIES[country]
    table = {"DE": DE_STATES, "AT": AT_STATES, "CH": CH_CANTONS}[country]
    return f"{table[sub]} ({COUNTRIES[country]})"


def normalize_region(value: Any) -> str:
    """``de-by`` → ``DE-BY``; ``BY`` → ``DE-BY``; ``Bayern`` → ``DE-BY``.

    Raises ``ValueError`` naming the valid codes — never guesses.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("region is required; valid codes: " + ", ".join(valid_regions()))
    text = raw.replace("_", "-").replace("/", "-").strip().upper()
    text = text.replace("OE", "Ö").replace("UE", "Ü") if text.startswith("AT-") else text
    # full names
    lowered = raw.strip().lower()
    for country, table in (("DE", DE_STATES), ("AT", AT_STATES), ("CH", CH_CANTONS)):
        for code, name in table.items():
            if lowered == name.lower():
                return f"{country}-{code}"
    for code, name in COUNTRIES.items():
        if lowered in (name.lower(), code.lower(), "germany", "austria", "switzerland", "deutschland", "österreich", "schweiz"):
            if lowered in ("germany", "deutschland"):
                return "DE"
            if lowered in ("austria", "österreich"):
                return "AT"
            if lowered in ("switzerland", "schweiz"):
                return "CH"
            return code
    if text in COUNTRIES:
        return text
    if "-" in text:
        country, _, sub = text.partition("-")
        if country == "AT":
            sub = _AT_ALIASES.get(sub, sub)
        table = {"DE": DE_STATES, "AT": AT_STATES, "CH": CH_CANTONS}.get(country)
        if table and sub in table:
            return f"{country}-{sub}"
    elif text in DE_STATES:  # bare German state code (legacy `state:` config key)
        return f"DE-{text}"
    raise ValueError(f"unknown region '{raw}'; valid codes: " + ", ".join(valid_regions()))


# ---------------------------------------------------------------------------
# Easter and derived dates
# ---------------------------------------------------------------------------


def easter_sunday(year: int) -> date:
    """Easter Sunday (Gregorian) — Meeus/Jones/Butcher."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th ``weekday`` (Mon=0 … Sun=6) of a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def buss_und_bettag(year: int) -> date:
    """Wednesday before 23 November."""
    d = date(year, 11, 22)
    while d.weekday() != 2:
        d -= timedelta(days=1)
    return d


def eidgenoessischer_bettag(year: int) -> date:
    """Third Sunday of September."""
    return _nth_weekday(year, 9, 6, 3)


def naefelser_fahrt(year: int) -> date:
    """First Thursday of April; a week later when that is in Holy Week."""
    d = _nth_weekday(year, 4, 3, 1)
    easter = easter_sunday(year)
    if easter - timedelta(days=7) <= d <= easter:
        d += timedelta(days=7)
    return d


def jeune_genevois(year: int) -> date:
    """Thursday after the first Sunday of September."""
    return _nth_weekday(year, 9, 6, 1) + timedelta(days=4)


# ---------------------------------------------------------------------------
# Holiday tables
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Holiday:
    date: date
    name: str
    kind: str = KIND_STATUTORY

    def as_dict(self) -> Dict[str, Any]:
        return {"date": self.date.isoformat(), "weekday": self.date.isoweekday(), "name": self.name, "kind": self.kind}


def _de(year: int, state: Optional[str]) -> List[Holiday]:
    e = easter_sunday(year)
    out = [
        Holiday(date(year, 1, 1), "Neujahr"),
        Holiday(e - timedelta(days=2), "Karfreitag"),
        Holiday(e + timedelta(days=1), "Ostermontag"),
        Holiday(date(year, 5, 1), "Tag der Arbeit"),
        Holiday(e + timedelta(days=39), "Christi Himmelfahrt"),
        Holiday(e + timedelta(days=50), "Pfingstmontag"),
        Holiday(date(year, 10, 3), "Tag der Deutschen Einheit"),
        Holiday(date(year, 12, 25), "1. Weihnachtstag"),
        Holiday(date(year, 12, 26), "2. Weihnachtstag"),
    ]
    st = state or ""
    if st in ("BW", "BY", "ST"):
        out.append(Holiday(date(year, 1, 6), "Heilige Drei Könige"))
    if st in ("BE", "MV"):
        out.append(Holiday(date(year, 3, 8), "Internationaler Frauentag"))
    if st in ("BW", "BY", "HE", "NW", "RP", "SL"):
        out.append(Holiday(e + timedelta(days=60), "Fronleichnam"))
    if st in ("SN", "TH"):
        out.append(Holiday(e + timedelta(days=60), "Fronleichnam", KIND_PARTIAL))  # catholic municipalities only
    if st in ("BY", "SL"):
        out.append(Holiday(date(year, 8, 15), "Mariä Himmelfahrt"))
    if st == "BY":
        out.append(Holiday(date(year, 8, 8), "Augsburger Friedensfest", KIND_PARTIAL))  # Stadt Augsburg only
    if st == "TH":
        out.append(Holiday(date(year, 9, 20), "Weltkindertag"))
    if st in ("BB", "HB", "HH", "MV", "NI", "SN", "ST", "SH", "TH"):
        out.append(Holiday(date(year, 10, 31), "Reformationstag"))
    if st in ("BW", "BY", "NW", "RP", "SL"):
        out.append(Holiday(date(year, 11, 1), "Allerheiligen"))
    if st == "SN":
        out.append(Holiday(buss_und_bettag(year), "Buß- und Bettag"))
    if st == "BB":
        out.append(Holiday(e, "Ostersonntag"))
        out.append(Holiday(e + timedelta(days=49), "Pfingstsonntag"))
    return out


def _at(year: int, state: Optional[str]) -> List[Holiday]:
    e = easter_sunday(year)
    out = [
        Holiday(date(year, 1, 1), "Neujahr"),
        Holiday(date(year, 1, 6), "Heilige Drei Könige"),
        Holiday(e + timedelta(days=1), "Ostermontag"),
        Holiday(date(year, 5, 1), "Staatsfeiertag"),
        Holiday(e + timedelta(days=39), "Christi Himmelfahrt"),
        Holiday(e + timedelta(days=50), "Pfingstmontag"),
        Holiday(e + timedelta(days=60), "Fronleichnam"),
        Holiday(date(year, 8, 15), "Mariä Himmelfahrt"),
        Holiday(date(year, 10, 26), "Nationalfeiertag"),
        Holiday(date(year, 11, 1), "Allerheiligen"),
        Holiday(date(year, 12, 8), "Mariä Empfängnis"),
        Holiday(date(year, 12, 25), "Christtag"),
        Holiday(date(year, 12, 26), "Stefanitag"),
    ]
    patrons = {
        "K": (3, 19, "Josef"), "ST": (3, 19, "Josef"), "T": (3, 19, "Josef"), "V": (3, 19, "Josef"),
        "OÖ": (5, 4, "Florian"), "S": (9, 24, "Rupert"), "NÖ": (11, 15, "Leopold"), "W": (11, 15, "Leopold"),
        "B": (11, 11, "Martin"),
    }
    if state in patrons:
        m, d, name = patrons[state]
        out.append(Holiday(date(year, m, d), f"{name} (Landespatron)", KIND_REGIONAL))
    return out


# Swiss cantonal membership (statutory = "dem Sonntag gleichgestellt"); a
# canton listed under PARTIAL grants the day only in parts of its territory.
_CH_STATUTORY: Dict[str, frozenset] = {
    "Berchtoldstag": frozenset({"AG", "BE", "FR", "GL", "JU", "NE", "OW", "SH", "TG", "VD", "ZH"}),
    "Heilige Drei Könige": frozenset({"SZ", "TI", "UR"}),
    "Josefstag": frozenset({"NW", "SZ", "TI", "UR", "VS"}),
    "Karfreitag": _ALL_CH - {"TI", "VS"},
    "Ostermontag": _ALL_CH - {"VS"},
    "Tag der Arbeit": frozenset({"BL", "BS", "JU", "NE", "SH", "TG", "TI", "ZH"}),
    "Pfingstmontag": _ALL_CH - {"VS"},
    "Fronleichnam": frozenset({"AI", "FR", "JU", "LU", "NW", "OW", "SZ", "TI", "UR", "VS", "ZG"}),
    "Mariä Himmelfahrt": frozenset({"AI", "FR", "JU", "LU", "NW", "OW", "SZ", "TI", "UR", "VS", "ZG"}),
    "Allerheiligen": frozenset({"AI", "FR", "GL", "JU", "LU", "NW", "OW", "SG", "SZ", "TI", "UR", "VS", "ZG"}),
    "Mariä Empfängnis": frozenset({"AI", "FR", "LU", "NW", "OW", "SZ", "TI", "UR", "VS", "ZG"}),
    "Stephanstag": frozenset({"AI", "AR", "BE", "BL", "BS", "GL", "GR", "LU", "NE", "SG", "SH", "TG", "TI", "UR", "ZH"}),
}
_CH_PARTIAL: Dict[str, frozenset] = {
    "Berchtoldstag": frozenset({"LU", "SO", "ZG", "GR"}),
    "Heilige Drei Könige": frozenset({"GR"}),
    "Josefstag": frozenset({"GR", "LU", "SO"}),
    "Tag der Arbeit": frozenset({"SO", "FR", "AG"}),
    "Fronleichnam": frozenset({"AG", "GR", "SO", "BL"}),
    "Mariä Himmelfahrt": frozenset({"AG", "GR", "SO"}),
    "Allerheiligen": frozenset({"AG", "GR", "SO"}),
    "Mariä Empfängnis": frozenset({"AG", "GR", "SO"}),
    "Stephanstag": frozenset({"AG", "FR", "NW", "OW", "SO", "SZ", "ZG"}),
}


def _ch(year: int, canton: Optional[str]) -> List[Holiday]:
    e = easter_sunday(year)
    dates = {
        "Berchtoldstag": date(year, 1, 2),
        "Heilige Drei Könige": date(year, 1, 6),
        "Josefstag": date(year, 3, 19),
        "Karfreitag": e - timedelta(days=2),
        "Ostermontag": e + timedelta(days=1),
        "Tag der Arbeit": date(year, 5, 1),
        "Pfingstmontag": e + timedelta(days=50),
        "Fronleichnam": e + timedelta(days=60),
        "Mariä Himmelfahrt": date(year, 8, 15),
        "Allerheiligen": date(year, 11, 1),
        "Mariä Empfängnis": date(year, 12, 8),
        "Stephanstag": date(year, 12, 26),
    }
    out = [
        Holiday(date(year, 1, 1), "Neujahr"),
        Holiday(e + timedelta(days=39), "Auffahrt"),
        Holiday(date(year, 8, 1), "Bundesfeiertag"),
        Holiday(date(year, 12, 25), "Weihnachten"),
    ]
    if not canton:
        return out
    for name, day in dates.items():
        if canton in _CH_STATUTORY.get(name, ()):
            out.append(Holiday(day, name))
        elif canton in _CH_PARTIAL.get(name, ()):
            out.append(Holiday(day, name, KIND_PARTIAL))
    extras = {
        "GL": [Holiday(naefelser_fahrt(year), "Näfelser Fahrt")],
        "GE": [Holiday(jeune_genevois(year), "Jeûne genevois"), Holiday(date(year, 12, 31), "Restauration de la République")],
        "VD": [Holiday(eidgenoessischer_bettag(year) + timedelta(days=1), "Bettagsmontag")],
        "JU": [Holiday(date(year, 6, 23), "Fête de l'indépendance")],
        "TI": [Holiday(date(year, 6, 29), "Peter und Paul")],
        "OW": [Holiday(date(year, 9, 25), "Bruder Klaus")],
        "NE": [Holiday(date(year, 3, 1), "Instauration de la République")],
    }
    out.extend(extras.get(canton, []))
    return out


def holidays_for(year: int, region: str) -> List[Holiday]:
    """All holidays of ``year`` for a normalized region code, sorted by date."""
    region = normalize_region(region)
    country, _, sub = region.partition("-")
    sub = sub or None
    if country == "DE":
        out = _de(year, sub)
    elif country == "AT":
        out = _at(year, sub)
    else:
        out = _ch(year, sub)
    return sorted(out, key=lambda h: (h.date, h.kind != KIND_STATUTORY, h.name))


def holidays_between(start: date, end: date, region: str) -> List[Holiday]:
    out: List[Holiday] = []
    for year in range(start.year, end.year + 1):
        out.extend(h for h in holidays_for(year, region) if start <= h.date <= end)
    return out


# ---------------------------------------------------------------------------
# Working days and target hours
# ---------------------------------------------------------------------------


@dataclass
class DayInfo:
    day: date
    is_weekend: bool
    holiday: Optional[Holiday]
    factor: float  # 1.0 working day, 0.5 half day, 0.0 no target time
    reason: str  # "workday" | "weekend" | "holiday" | "half_day" | "outside_employment"

    @property
    def counts(self) -> bool:
        return self.factor > 0


def parse_iso_date(value: Any, name: str = "date") -> date:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text[:10]) if len(text) >= 10 else date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD, got '{value}'") from exc


def _half_day_set(half_days: Optional[Iterable[str]], years: Iterable[int]) -> set:
    out: set = set()
    for token in half_days or ():
        text = str(token or "").strip()
        if not text:
            continue
        if len(text) == 5 and text[2] == "-":  # MM-DD every year
            month, day = int(text[:2]), int(text[3:])
            for year in years:
                out.add(date(year, month, day))
        else:
            out.add(parse_iso_date(text, "half_days"))
    return out


def _extra_holidays(extra: Optional[Iterable[Any]]) -> Dict[date, str]:
    out: Dict[date, str] = {}
    for item in extra or ():
        if isinstance(item, dict):
            day = parse_iso_date(item.get("date"), "extra_holidays.date")
            out[day] = str(item.get("name") or "Betriebsfeiertag")
        else:
            out[parse_iso_date(item, "extra_holidays")] = "Betriebsfeiertag"
    return out


def weekend_days(days_per_week: int, work_weekdays: Optional[frozenset] = None) -> frozenset:
    if work_weekdays:
        return frozenset(range(1, 8)) - parse_work_weekdays(work_weekdays)
    if days_per_week == 5:
        return frozenset({6, 7})
    if days_per_week == 6:
        return frozenset({7})
    if days_per_week == 7:
        return frozenset()
    raise ValueError("days_per_week must be 5, 6 or 7 (use work_weekdays for explicit day sets)")


def calendar_days(
    start: date,
    end: date,
    region: str,
    *,
    days_per_week: int = DEFAULT_DAYS_PER_WEEK,
    work_weekdays: Optional[Iterable[Any]] = None,
    half_days: Optional[Iterable[str]] = DEFAULT_HALF_DAYS,
    extra_holidays: Optional[Iterable[Any]] = None,
    employment_start: Optional[date] = None,
    employment_end: Optional[date] = None,
) -> List[DayInfo]:
    """One ``DayInfo`` per calendar day in [start, end] (both inclusive).

    Rules: a holiday on a weekend is listed but costs nothing (the weekend
    already does); a half day only halves an otherwise working day; a
    holiday beats a half day; ``partial`` holidays never deduct.
    """
    if end < start:
        raise ValueError("end must not be before start")
    region = normalize_region(region)
    weekend = weekend_days(days_per_week, parse_work_weekdays(work_weekdays))
    years = range(start.year, end.year + 1)
    halves = _half_day_set(half_days, years)
    extras = _extra_holidays(extra_holidays)
    by_date: Dict[date, Holiday] = {}
    for h in holidays_between(start, end, region):
        if h.kind == KIND_PARTIAL:
            continue
        by_date.setdefault(h.date, h)
    for day, name in extras.items():
        by_date.setdefault(day, Holiday(day, name, KIND_STATUTORY))

    out: List[DayInfo] = []
    current = start
    while current <= end:
        holiday = by_date.get(current)
        is_weekend = current.isoweekday() in weekend
        if (employment_start and current < employment_start) or (employment_end and current > employment_end):
            out.append(DayInfo(current, is_weekend, holiday, 0.0, "outside_employment"))
        elif is_weekend:
            out.append(DayInfo(current, True, holiday, 0.0, "weekend"))
        elif holiday is not None:
            out.append(DayInfo(current, False, holiday, 0.0, "holiday"))
        elif current in halves:
            out.append(DayInfo(current, False, None, 0.5, "half_day"))
        else:
            out.append(DayInfo(current, False, None, 1.0, "workday"))
        current += timedelta(days=1)
    return out


def hours_per_day(
    weekly_hours: float, days_per_week: int, part_time_factor: float = 1.0,
    work_weekdays: Optional[Iterable[Any]] = None,
) -> float:
    if weekly_hours <= 0:
        raise ValueError("weekly_hours must be positive")
    parsed = parse_work_weekdays(work_weekdays)
    if parsed:
        divisor = len(parsed)
    else:
        weekend_days(days_per_week)  # validates
        divisor = days_per_week
    if not 0 < part_time_factor <= 1:
        raise ValueError("part_time_factor must be in (0, 1]")
    return round(weekly_hours / divisor * part_time_factor, 4)


def monthly_summary(days: Sequence[DayInfo], hours: float) -> List[Dict[str, Any]]:
    """Per-month counts plus target hours (``hours`` = hours per full day)."""
    months: Dict[str, Dict[str, Any]] = {}
    for info in days:
        key = info.day.strftime("%Y-%m")
        row = months.setdefault(key, {
            "month": key, "calendar_days": 0, "weekend_days": 0, "holidays_on_workdays": 0,
            "holidays_on_weekend": 0, "half_days": 0, "outside_employment": 0, "workdays_net": 0.0,
            "holidays": [],
        })
        row["calendar_days"] += 1
        if info.reason == "outside_employment":
            row["outside_employment"] += 1
            continue
        if info.is_weekend:
            row["weekend_days"] += 1
            if info.holiday is not None:
                row["holidays_on_weekend"] += 1
                row["holidays"].append(f"{info.holiday.date.isoformat()} {info.holiday.name} (weekend, no deduction)")
            continue
        if info.reason == "holiday":
            row["holidays_on_workdays"] += 1
            row["holidays"].append(f"{info.holiday.date.isoformat()} {info.holiday.name}")
        elif info.reason == "half_day":
            row["half_days"] += 1
        row["workdays_net"] += info.factor
    out = []
    for key in sorted(months):
        row = months[key]
        row["workdays_net"] = round(row["workdays_net"], 2)
        row["target_hours"] = round(row["workdays_net"] * hours, 2)
        out.append(row)
    return out


def totals(months: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    keys = ("calendar_days", "weekend_days", "holidays_on_workdays", "holidays_on_weekend", "half_days", "outside_employment")
    out: Dict[str, Any] = {k: sum(int(m[k]) for m in months) for k in keys}
    out["workdays_net"] = round(sum(float(m["workdays_net"]) for m in months), 2)
    out["target_hours"] = round(sum(float(m["target_hours"]) for m in months), 2)
    return out


def month_range(year: int, month: int) -> Tuple[date, date]:
    return date(year, month, 1), date(year, month, _calendar.monthrange(year, month)[1])


__all__ = [
    "AT_STATES", "CH_CANTONS", "COUNTRIES", "DE_STATES", "DEFAULT_DAYS_PER_WEEK", "DEFAULT_HALF_DAYS",
    "DEFAULT_WEEKLY_HOURS", "DayInfo", "Holiday", "KIND_PARTIAL", "KIND_REGIONAL", "KIND_STATUTORY", "SOURCE",
    "WEEKDAY_TOKENS", "buss_und_bettag", "calendar_days", "easter_sunday", "eidgenoessischer_bettag",
    "holidays_between", "holidays_for", "hours_per_day", "jeune_genevois", "month_range", "monthly_summary",
    "naefelser_fahrt", "normalize_region", "parse_iso_date", "parse_work_weekdays", "region_name", "totals",
    "valid_regions", "weekend_days",
]
