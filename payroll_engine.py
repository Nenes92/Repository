"""Pure payroll calculations for the cedolino V2 forecast.

The ordinary salary is represented by a configurable monthly net amount.
Only variable components (shift premiums, allowances and overtime) are
calculated from the contractual gross hourly rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from math import sqrt
from statistics import mean
from typing import Iterable, Mapping, Sequence


DEFAULT_RULES: dict[str, float] = {
    "paga_oraria_lorda": 18.01988,
    "netto_fisso_mensile": 2200.0,
    "coefficiente_netto_variabili": 0.60,
    "rettifica_mensile": 0.0,
    "ritardo_competenze_mesi": 1.0,
    "m_p_feriale_pct": 20.0,
    "m_p_festivo_giorno_pct": 50.0,
    "notte_feriale_pct": 20.0,
    "festivo_sera_notte_pct": 60.0,
    "straordinario_feriale_pct": 25.0,
    "straordinario_festivo_pct": 50.0,
    "stra_mattina_feriale_pct": 25.0,
    "stra_mattina_festivo_pct": 55.0,
    "stra_pomeriggio_feriale_pct": 40.0,
    "stra_pomeriggio_festivo_pct": 60.0,
    "stra_notte_feriale_pct": 50.0,
    "stra_notte_festivo_pct": 70.0,
    "stra_ferie_feriale_pct": 25.0,
    "stra_ferie_festivo_pct": 50.0,
    "ind_m_p_feriale": 6.0,
    "ind_notte_feriale": 18.0,
    "ind_m_p_festivo": 15.0,
    "ind_notte_festiva": 25.0,
    "buono_pasto": 7.0,
    "smart_target": 15.0,
}

SHIFT_TIMES: dict[str, tuple[time, time]] = {
    "Mattina": (time(6), time(14)),
    "Pomeriggio": (time(14), time(22)),
    "Notte": (time(22), time(6)),
    "Ferie": (time(9), time(17)),
    "Riposo": (time(0), time(0)),
}


@dataclass(frozen=True)
class Shift:
    day: date
    kind: str
    forced_holiday: bool = False
    overtime_minutes: int = 0
    onsite: bool = False


@dataclass(frozen=True)
class VariableBreakdown:
    premiums_gross: float = 0.0
    allowances_gross: float = 0.0
    overtime_gross: float = 0.0
    meal_vouchers: float = 0.0
    premium_hours: Mapping[float, float] = field(default_factory=dict)
    overtime_hours: Mapping[float, float] = field(default_factory=dict)

    @property
    def variables_gross(self) -> float:
        return self.premiums_gross + self.allowances_gross + self.overtime_gross


@dataclass(frozen=True)
class PayslipEstimate:
    month: str
    competence_month: str
    fixed_net: float
    variables_gross: float
    variables_net: float
    adjustment: float
    credited_net: float
    meal_vouchers: float
    realistic_low: float
    realistic_high: float
    breakdown: VariableBreakdown


@dataclass(frozen=True)
class CalibrationRow:
    month: str
    variables_month: str
    actual_net: float
    variables_gross: float
    estimated_net: float
    absolute_error: float
    percentage_error: float | None
    included: bool
    exclusion_reason: str = ""


@dataclass(frozen=True)
class CalibrationResult:
    fixed_net: float
    variable_coefficient: float
    mean_absolute_error: float
    confidence_low: float
    confidence_high: float
    rows: tuple[CalibrationRow, ...]


def migrate_rules(
    saved: Mapping[str, object] | None,
    defaults: Mapping[str, float] = DEFAULT_RULES,
) -> dict[str, float]:
    """Add V2 fields without discarding values from an old rules row."""
    result = dict(defaults)
    saved = saved or {}
    aliases = {"paga_oraria": "paga_oraria_lorda", "quota_fissa_mensile": "netto_fisso_mensile"}
    for key, raw in saved.items():
        target = aliases.get(key, key)
        if target not in result or raw in (None, ""):
            continue
        try:
            result[target] = float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            continue
    return result


def add_months(month: str, offset: int) -> str:
    year, mon = (int(part) for part in month.split("-"))
    index = year * 12 + mon - 1 + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _bounds(shift: Shift) -> tuple[datetime, datetime]:
    if shift.kind not in SHIFT_TIMES:
        raise ValueError(f"Turno non riconosciuto: {shift.kind}")
    start_time, end_time = SHIFT_TIMES[shift.kind]
    start = datetime.combine(shift.day, start_time)
    end = datetime.combine(shift.day, end_time)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f, g = (b + 8) // 25, (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(year, month, (h + l - 7 * m + 114) % 31 + 1)


def is_holiday(moment: datetime, forced: bool = False) -> bool:
    fixed = {(1, 1), (1, 6), (4, 25), (5, 1), (6, 2), (8, 15), (11, 1), (12, 8), (12, 25), (12, 26)}
    return forced or moment.weekday() == 6 or (moment.month, moment.day) in fixed or moment.date() == _easter(moment.year) + timedelta(days=1)


def premium_percentage(kind: str, moment: datetime, forced: bool, rules: Mapping[str, float]) -> float:
    minutes = moment.hour * 60 + moment.minute
    if moment.weekday() == 5 and 6 * 60 <= minutes < 18 * 60:
        return 0.0
    festive = is_holiday(moment, forced)
    if kind == "Mattina":
        return rules["m_p_festivo_giorno_pct"] if festive else rules["m_p_feriale_pct"]
    if kind == "Pomeriggio":
        if minutes >= 18 * 60:
            return rules["festivo_sera_notte_pct"] if festive else rules["m_p_feriale_pct"]
        return rules["m_p_festivo_giorno_pct"] if festive else rules["m_p_feriale_pct"]
    if kind == "Notte":
        return rules["festivo_sera_notte_pct"] if festive else rules["notte_feriale_pct"]
    return 0.0


def shift_allowance(shift: Shift, rules: Mapping[str, float]) -> float:
    if shift.kind in {"Ferie", "Riposo"}:
        return 0.0
    start, _ = _bounds(shift)
    festive = is_holiday(start, shift.forced_holiday)
    if shift.kind == "Notte":
        return rules["ind_notte_festiva"] if festive else rules["ind_notte_feriale"]
    return rules["ind_m_p_festivo"] if festive else rules["ind_m_p_feriale"]


def _overtime_percentage(shift: Shift, moment: datetime, rules: Mapping[str, float]) -> float:
    festive = is_holiday(moment, shift.forced_holiday)
    suffix = "festivo" if festive else "feriale"
    prefix = {"Mattina": "mattina", "Pomeriggio": "pomeriggio", "Notte": "notte", "Ferie": "ferie"}.get(shift.kind)
    fallback = rules[f"straordinario_{suffix}_pct"]
    return rules.get(f"stra_{prefix}_{suffix}_pct", fallback) if prefix else fallback


def calculate_shift_variables(shift: Shift, rules: Mapping[str, float]) -> VariableBreakdown:
    """Calculate variable gross only; ordinary hours and leave add no base pay."""
    if shift.kind not in SHIFT_TIMES:
        raise ValueError(f"Turno non riconosciuto: {shift.kind}")
    hourly = float(rules["paga_oraria_lorda"])
    premiums = 0.0
    premium_hours: dict[float, float] = {}
    if shift.kind not in {"Ferie", "Riposo"}:
        start, end = _bounds(shift)
        cursor = start
        while cursor < end:
            nxt = min(cursor + timedelta(minutes=1), end)
            hours = (nxt - cursor).total_seconds() / 3600
            pct = premium_percentage(shift.kind, cursor, shift.forced_holiday, rules)
            premiums += hourly * pct / 100 * hours
            if pct:
                premium_hours[pct] = premium_hours.get(pct, 0.0) + hours
            cursor = nxt
    overtime = 0.0
    overtime_hours: dict[float, float] = {}
    if shift.kind != "Riposo" and shift.overtime_minutes > 0:
        _, cursor = _bounds(shift)
        overtime_end = cursor + timedelta(minutes=min(120, max(0, shift.overtime_minutes)))
        while cursor < overtime_end:
            nxt = min(cursor + timedelta(minutes=1), overtime_end)
            hours = (nxt - cursor).total_seconds() / 3600
            pct = _overtime_percentage(shift, cursor, rules)
            overtime += hourly * (1 + pct / 100) * hours
            overtime_hours[pct] = overtime_hours.get(pct, 0.0) + hours
            cursor = nxt
    meal = 0.0
    if shift.onsite and shift.kind not in {"", "Ferie", "Riposo"}:
        start, _ = _bounds(shift)
        if shift.kind != "Mattina" or is_holiday(start, shift.forced_holiday):
            meal = float(rules.get("buono_pasto", 0.0))
    return VariableBreakdown(
        premiums_gross=premiums,
        allowances_gross=shift_allowance(shift, rules),
        overtime_gross=overtime,
        meal_vouchers=meal,
        premium_hours=premium_hours,
        overtime_hours=overtime_hours,
    )


def calculate_month_variables(shifts: Iterable[Shift], rules: Mapping[str, float]) -> VariableBreakdown:
    premium_hours: dict[float, float] = {}
    overtime_hours: dict[float, float] = {}
    premiums = allowances = overtime = vouchers = 0.0
    for shift in shifts:
        item = calculate_shift_variables(shift, rules)
        premiums += item.premiums_gross
        allowances += item.allowances_gross
        overtime += item.overtime_gross
        vouchers += item.meal_vouchers
        for pct, hours in item.premium_hours.items():
            premium_hours[pct] = premium_hours.get(pct, 0.0) + hours
        for pct, hours in item.overtime_hours.items():
            overtime_hours[pct] = overtime_hours.get(pct, 0.0) + hours
    return VariableBreakdown(premiums, allowances, overtime, vouchers, premium_hours, overtime_hours)


def estimate_payslip(
    month: str,
    variables_by_month: Mapping[str, VariableBreakdown],
    rules: Mapping[str, float],
    uncertainty: float = 0.0,
) -> PayslipEstimate:
    delay = int(round(rules.get("ritardo_competenze_mesi", 1)))
    competence_month = add_months(month, -delay)
    breakdown = variables_by_month.get(competence_month, VariableBreakdown())
    fixed = float(rules["netto_fisso_mensile"])
    coefficient = float(rules["coefficiente_netto_variabili"])
    adjustment = float(rules.get("rettifica_mensile", 0.0))
    variables_net = breakdown.variables_gross * coefficient
    credited = fixed + variables_net + adjustment
    spread = max(0.0, float(uncertainty))
    return PayslipEstimate(
        month=month,
        competence_month=competence_month,
        fixed_net=fixed,
        variables_gross=breakdown.variables_gross,
        variables_net=variables_net,
        adjustment=adjustment,
        credited_net=credited,
        meal_vouchers=breakdown.meal_vouchers,
        realistic_low=credited - spread,
        realistic_high=credited + spread,
        breakdown=breakdown,
    )


def calibrate(
    salaries: Mapping[str, float],
    variables_by_month: Mapping[str, VariableBreakdown],
    delay: int = 1,
    manual_included: Mapping[str, bool] | None = None,
) -> CalibrationResult:
    """Fit actual_net = fixed_net + coefficient * previous variables.

    Obvious exceptional months are excluded with a robust median/MAD rule.
    Manual choices always override the automatic classification.
    """
    candidates: list[tuple[str, str, float, float]] = []
    for month, actual in sorted(salaries.items()):
        variables_month = add_months(month, -delay)
        if variables_month in variables_by_month and float(actual) > 0:
            candidates.append((month, variables_month, float(actual), variables_by_month[variables_month].variables_gross))
    if len(candidates) < 2:
        raise ValueError("Servono almeno due mensilità abbinate per calibrare il modello.")
    actuals = sorted(item[2] for item in candidates)
    median = actuals[len(actuals) // 2]
    deviations = sorted(abs(value - median) for value in actuals)
    mad = deviations[len(deviations) // 2] or max(1.0, median * 0.05)
    included_flags: list[bool] = []
    reasons: list[str] = []
    for month, _, actual, _ in candidates:
        automatic = abs(actual - median) <= max(3.5 * mad, median * 0.25)
        manual = (manual_included or {}).get(month)
        included_flags.append(automatic if manual is None else bool(manual))
        reasons.append("" if automatic else "Mensilità straordinaria/anomala")
    selected = [row for row, flag in zip(candidates, included_flags) if flag]
    if len(selected) < 2:
        raise ValueError("Servono almeno due mensilità incluse per calibrare il modello.")
    xs = [row[3] for row in selected]
    ys = [row[2] for row in selected]
    x_mean, y_mean = mean(xs), mean(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    coefficient = 0.60 if denominator <= 1e-12 else sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator
    coefficient = min(1.0, max(0.0, coefficient))
    fixed = mean(y - coefficient * x for x, y in zip(xs, ys))
    residuals = [y - (fixed + coefficient * x) for x, y in zip(xs, ys)]
    mae = mean(abs(value) for value in residuals)
    std_error = sqrt(sum(value * value for value in residuals) / max(1, len(residuals) - 2))
    margin = 1.96 * std_error
    rows = tuple(
        CalibrationRow(
            month=month,
            variables_month=variables_month,
            actual_net=actual,
            variables_gross=variables,
            estimated_net=fixed + coefficient * variables,
            absolute_error=abs(actual - (fixed + coefficient * variables)),
            percentage_error=(abs(actual - (fixed + coefficient * variables)) / actual * 100) if actual else None,
            included=included,
            exclusion_reason="" if included else reason,
        )
        for (month, variables_month, actual, variables), included, reason in zip(candidates, included_flags, reasons)
    )
    return CalibrationResult(fixed, coefficient, mae, fixed - margin, fixed + margin, rows)
