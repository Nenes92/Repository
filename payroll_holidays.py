"""Annual CCNL holiday compensation, separate from shift premiums and adjustments."""
from dataclasses import dataclass
from datetime import date
from typing import Mapping


@dataclass(frozen=True)
class PaidHoliday:
    day: date
    label: str
    gross: float


def paid_holidays(year: int, daily_gross: float = 119.90,
                  patron_month: int = 0, patron_day: int = 0) -> tuple[PaidHoliday, ...]:
    fixed = [
        (1, 1, "Capodanno"), (1, 6, "Epifania"), (4, 25, "Liberazione"),
        (5, 1, "Festa dei lavoratori"), (6, 2, "Festa della Repubblica"),
        (8, 15, "Ferragosto"), (11, 1, "Ognissanti"),
        (12, 8, "Immacolata"), (12, 25, "Natale"), (12, 26, "Santo Stefano"),
    ]
    if year >= 2026:
        fixed.append((10, 4, "San Francesco"))
    events = {date(year, m, d): label for m, d, label in fixed}
    # Pasqua is deliberately excluded; Easter Monday can never fall on Sunday.
    if patron_month and patron_day:
        try:
            patron = date(year, patron_month, patron_day)
        except ValueError:
            patron = None
        if patron is not None and patron not in events:
            events[patron] = "Santo Patrono"
    result = [PaidHoliday(day, label, max(0.0, daily_gross))
              for day, label in events.items() if day.weekday() == 6]
    # Independent contractual payment, even when Nov 1 is itself a Sunday holiday.
    result.append(PaidHoliday(date(year, 11, 4), "4 novembre · trattamento CCNL", max(0.0, daily_gross)))
    return tuple(sorted(result, key=lambda event: event.day))


def holiday_compensation(month: str, rules: Mapping[str, float]) -> tuple[PaidHoliday, ...]:
    if not rules.get("festivita_automatiche", 0):
        return ()
    year, number = map(int, month.split("-"))
    lag = int(rules.get("festivita_ritardo_mesi", 0))
    index = year * 12 + number - 1 - lag
    source_year, source_number = divmod(index, 12)
    return tuple(event for event in paid_holidays(
        source_year, float(rules.get("festivita_quota_lorda", 119.90)),
        int(rules.get("patrono_mese", 0)), int(rules.get("patrono_giorno", 0)),
    ) if event.day.month == source_number + 1)


def holiday_net(month: str, rules: Mapping[str, float]) -> float:
    return sum(event.gross for event in holiday_compensation(month, rules)) * float(
        rules.get("coefficiente_netto_variabili", .60))
