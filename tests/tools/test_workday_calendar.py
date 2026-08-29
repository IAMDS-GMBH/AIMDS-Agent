"""tools/workday_calendar.py — calendar facts the model must never type by hand.

The expected numbers for DE-BY Jan–Aug 2026 are the independently verified
values from session 20260829_205458_1fecc5 (the model's own table had
January and March wrong by one day each, cancelling out).
"""

from __future__ import annotations

from datetime import date

import pytest

from tools import workday_calendar as wc


class TestEasterAndDerived:
    def test_easter(self):
        assert wc.easter_sunday(2025) == date(2025, 4, 20)
        assert wc.easter_sunday(2026) == date(2026, 4, 5)
        assert wc.easter_sunday(2027) == date(2027, 3, 28)

    def test_buss_und_bettag_is_the_wednesday_before_nov_23(self):
        assert wc.buss_und_bettag(2026) == date(2026, 11, 18)
        assert wc.buss_und_bettag(2025) == date(2025, 11, 19)

    def test_naefelser_fahrt_skips_holy_week(self):
        assert wc.naefelser_fahrt(2026) == date(2026, 4, 9)  # Apr 2 is in Holy Week
        assert wc.naefelser_fahrt(2025) == date(2025, 4, 3)

    def test_jeune_genevois_and_bettag(self):
        assert wc.jeune_genevois(2026) == date(2026, 9, 10)
        assert wc.eidgenoessischer_bettag(2026) == date(2026, 9, 20)


class TestRegions:
    @pytest.mark.parametrize("raw,expected", [
        ("DE-BY", "DE-BY"), ("de-by", "DE-BY"), ("BY", "DE-BY"), ("Bayern", "DE-BY"),
        ("Baden-Württemberg", "DE-BW"), ("AT", "AT"), ("at-noe", "AT-NÖ"), ("AT-W", "AT-W"),
        ("Österreich", "AT"), ("CH-ZH", "CH-ZH"), ("Zürich", "CH-ZH"), ("schweiz", "CH"), ("Germany", "DE"),
    ])
    def test_normalize(self, raw, expected):
        assert wc.normalize_region(raw) == expected

    def test_unknown_region_names_the_valid_codes(self):
        with pytest.raises(ValueError) as exc:
            wc.normalize_region("XX-YY")
        assert "DE-BY" in str(exc.value) and "CH-ZH" in str(exc.value)
        with pytest.raises(ValueError):
            wc.normalize_region("")

    def test_names(self):
        assert wc.region_name("DE-BY") == "Bayern (Deutschland)"
        assert wc.region_name("CH") == "Schweiz"


class TestGermanHolidays:
    def test_bavaria_2026(self):
        names = {(h.date.isoformat(), h.name) for h in wc.holidays_for(2026, "DE-BY") if h.kind == wc.KIND_STATUTORY}
        assert names == {
            ("2026-01-01", "Neujahr"), ("2026-01-06", "Heilige Drei Könige"), ("2026-04-03", "Karfreitag"),
            ("2026-04-06", "Ostermontag"), ("2026-05-01", "Tag der Arbeit"), ("2026-05-14", "Christi Himmelfahrt"),
            ("2026-05-25", "Pfingstmontag"), ("2026-06-04", "Fronleichnam"), ("2026-08-15", "Mariä Himmelfahrt"),
            ("2026-10-03", "Tag der Deutschen Einheit"), ("2026-11-01", "Allerheiligen"),
            ("2026-12-25", "1. Weihnachtstag"), ("2026-12-26", "2. Weihnachtstag"),
        }

    def test_partial_holidays_are_flagged_not_statutory(self):
        by = {h.name: h.kind for h in wc.holidays_for(2026, "DE-BY")}
        assert by["Augsburger Friedensfest"] == wc.KIND_PARTIAL
        sn = {h.name: h.kind for h in wc.holidays_for(2026, "DE-SN")}
        assert sn["Fronleichnam"] == wc.KIND_PARTIAL and sn["Buß- und Bettag"] == wc.KIND_STATUTORY

    def test_nationwide_only_for_bare_country(self):
        names = {h.name for h in wc.holidays_for(2026, "DE")}
        assert "Heilige Drei Könige" not in names and "Pfingstmontag" in names and len(names) == 9

    def test_state_specifics(self):
        assert "Internationaler Frauentag" in {h.name for h in wc.holidays_for(2026, "DE-BE")}
        assert "Weltkindertag" in {h.name for h in wc.holidays_for(2026, "DE-TH")}
        assert "Reformationstag" in {h.name for h in wc.holidays_for(2026, "DE-HH")}
        assert "Ostersonntag" in {h.name for h in wc.holidays_for(2026, "DE-BB")}


class TestAustriaAndSwitzerland:
    def test_austria_2026(self):
        by_name = {h.name: h for h in wc.holidays_for(2026, "AT-W")}
        assert by_name["Fronleichnam"].date == date(2026, 6, 4)
        assert by_name["Nationalfeiertag"].date == date(2026, 10, 26)
        assert by_name["Leopold (Landespatron)"].kind == wc.KIND_REGIONAL
        assert "Karfreitag" not in by_name
        assert "Leopold (Landespatron)" not in {h.name for h in wc.holidays_for(2026, "AT")}
        assert "Florian (Landespatron)" in {h.name for h in wc.holidays_for(2026, "AT-OÖ")}

    def test_zurich_2026(self):
        names = {h.name for h in wc.holidays_for(2026, "CH-ZH") if h.kind == wc.KIND_STATUTORY}
        assert {"Berchtoldstag", "Karfreitag", "Ostermontag", "Auffahrt", "Pfingstmontag", "Tag der Arbeit", "Bundesfeiertag", "Stephanstag"} <= names
        assert "Fronleichnam" not in names

    def test_cantonal_specials(self):
        assert "Näfelser Fahrt" in {h.name for h in wc.holidays_for(2026, "CH-GL")}
        assert "Jeûne genevois" in {h.name for h in wc.holidays_for(2026, "CH-GE")}
        assert "Bettagsmontag" in {h.name for h in wc.holidays_for(2026, "CH-VD")}
        vs = {h.name for h in wc.holidays_for(2026, "CH-VS")}
        assert "Karfreitag" not in vs and "Fronleichnam" in vs
        assert {h.name for h in wc.holidays_for(2026, "CH")} == {"Neujahr", "Auffahrt", "Bundesfeiertag", "Weihnachten"}


class TestWorkingDaysAndTargetHours:
    def test_bavaria_jan_to_aug_2026_matches_the_verified_table(self):
        days = wc.calendar_days(date(2026, 1, 1), date(2026, 8, 29), "DE-BY")
        months = wc.monthly_summary(days, wc.hours_per_day(40, 5))
        by_month = {m["month"]: m for m in months}
        assert by_month["2026-01"]["workdays_net"] == 20 and by_month["2026-01"]["target_hours"] == 160.0
        assert by_month["2026-01"]["holidays_on_workdays"] == 2
        assert by_month["2026-03"]["workdays_net"] == 22 and by_month["2026-03"]["target_hours"] == 176.0
        assert by_month["2026-05"]["holidays_on_workdays"] == 3
        assert by_month["2026-08"]["holidays_on_weekend"] == 1  # Mariä Himmelfahrt on a Saturday, no deduction
        assert "2026-08-15 Mariä Himmelfahrt (weekend, no deduction)" in by_month["2026-08"]["holidays"]
        total = wc.totals(months)
        assert total["workdays_net"] == 164.0 and total["holidays_on_workdays"] == 8 and total["target_hours"] == 1312.0

    def test_half_days_and_holiday_precedence(self):
        days = {d.day: d for d in wc.calendar_days(date(2026, 12, 20), date(2026, 12, 31), "DE-BY")}
        assert days[date(2026, 12, 24)].factor == 0.5 and days[date(2026, 12, 24)].reason == "half_day"
        assert days[date(2026, 12, 31)].factor == 0.5
        assert days[date(2026, 12, 25)].factor == 0.0 and days[date(2026, 12, 25)].reason == "holiday"
        # a half day that is also a holiday counts 0
        custom = wc.calendar_days(date(2026, 5, 1), date(2026, 5, 1), "DE-BY", half_days=["05-01"])
        assert custom[0].factor == 0.0 and custom[0].reason == "holiday"
        # no half days at all
        plain = {d.day: d for d in wc.calendar_days(date(2026, 12, 24), date(2026, 12, 24), "DE-BY", half_days=None)}
        assert plain[date(2026, 12, 24)].factor == 1.0

    def test_six_day_week_counts_saturdays(self):
        five = sum(d.factor for d in wc.calendar_days(date(2026, 8, 1), date(2026, 8, 31), "DE-BY"))
        six = sum(d.factor for d in wc.calendar_days(date(2026, 8, 1), date(2026, 8, 31), "DE-BY", days_per_week=6))
        assert five == 21 and six == 25  # 15.08. is a Saturday holiday → counts as holiday in the 6-day week
        assert wc.hours_per_day(48, 6) == 8.0 and wc.hours_per_day(40, 5, 0.5) == 4.0

    def test_employment_window_and_extra_holidays(self):
        days = wc.calendar_days(date(2026, 3, 1), date(2026, 3, 31), "DE-BY", employment_start=date(2026, 3, 16))
        assert sum(d.factor for d in days) == 12
        assert wc.monthly_summary(days, 8)[0]["outside_employment"] == 15
        extra = wc.calendar_days(date(2026, 3, 16), date(2026, 3, 16), "DE-BY", extra_holidays=[{"date": "2026-03-16", "name": "Betriebsausflug"}])
        assert extra[0].reason == "holiday" and extra[0].holiday.name == "Betriebsausflug"

    def test_partial_holidays_never_deduct(self):
        days = {d.day: d for d in wc.calendar_days(date(2026, 8, 3), date(2026, 8, 14), "DE-BY")}
        assert date(2026, 8, 8) not in days or days.get(date(2026, 8, 8)) is None or days[date(2026, 8, 8)].is_weekend
        aug = wc.calendar_days(date(2026, 8, 7), date(2026, 8, 7), "DE-BY")  # Friday before the Augsburg day
        assert aug[0].factor == 1.0

    def test_validation(self):
        with pytest.raises(ValueError):
            wc.calendar_days(date(2026, 2, 1), date(2026, 1, 1), "DE-BY")
        with pytest.raises(ValueError):
            wc.weekend_days(4)
        with pytest.raises(ValueError):
            wc.parse_iso_date("31.12.2026")
        assert wc.parse_iso_date("2026-12-31T08:00:00") == date(2026, 12, 31)
