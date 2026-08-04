from datetime import date

import pytest

from payroll_engine import (
    DEFAULT_RULES,
    Shift,
    VariableBreakdown,
    calculate_month_variables,
    calculate_shift_variables,
    calibrate,
    estimate_live_net_accrual,
    estimate_payslip,
    migrate_rules,
)


@pytest.fixture
def rules():
    return dict(DEFAULT_RULES)


def test_ordinary_month_does_not_rebuild_base_salary(rules):
    shifts = [Shift(date(2026, 6, day), "Mattina") for day in range(1, 6)]
    result = calculate_month_variables(shifts, rules)
    assert result.variables_gross < 5 * 8 * rules["paga_oraria_lorda"]
    assert result.premiums_gross > 0


def test_weekday_night_uses_twenty_percent_and_allowance(rules):
    result = calculate_shift_variables(Shift(date(2026, 6, 8), "Notte"), rules)
    assert result.premiums_gross == pytest.approx(8 * rules["paga_oraria_lorda"] * 0.20)
    assert result.allowances_gross == 18.0


def test_holiday_shift(rules):
    result = calculate_shift_variables(Shift(date(2026, 12, 25), "Mattina"), rules)
    assert result.premiums_gross == pytest.approx(8 * rules["paga_oraria_lorda"] * 0.50)
    assert result.allowances_gross == 15.0


def test_overtime_contains_base_and_premium(rules):
    result = calculate_shift_variables(Shift(date(2026, 6, 8), "Mattina", overtime_minutes=60), rules)
    assert result.overtime_gross == pytest.approx(rules["paga_oraria_lorda"] * 1.25)


def test_leave_has_no_variable_pay(rules):
    result = calculate_shift_variables(
        Shift(date(2026, 6, 8), "Ferie", overtime_minutes=120),
        rules,
    )
    assert result.variables_gross == 0
    assert result.overtime_gross == 0


def test_day_shift_from_legacy_excel_has_no_variable_pay(rules):
    result = calculate_shift_variables(Shift(date(2026, 1, 9), "Giornata"), rules)
    assert result.variables_gross == 0


def test_variables_are_paid_next_month(rules):
    june = VariableBreakdown(premiums_gross=100)
    estimate = estimate_payslip("2026-07", {"2026-06": june}, rules)
    assert estimate.competence_month == "2026-06"
    assert estimate.variables_net == 60


def test_meal_vouchers_are_not_credited_net(rules):
    june = VariableBreakdown(premiums_gross=100, meal_vouchers=70)
    estimate = estimate_payslip("2026-07", {"2026-06": june}, rules)
    assert estimate.credited_net == 2197
    assert estimate.meal_vouchers == 70


def test_default_adjustment_is_sixty_three_euro_net_deduction(rules):
    estimate = estimate_payslip("2026-07", {}, rules)
    assert estimate.adjustment == -63
    assert estimate.credited_net == 2137


@pytest.mark.parametrize(("adjustment", "expected"), [(150, 2350), (-150, 2050)])
def test_positive_and_negative_adjustments(rules, adjustment, expected):
    rules["rettifica_mensile"] = adjustment
    assert estimate_payslip("2026-07", {}, rules).credited_net == expected


def test_adjustment_is_applied_only_to_its_payslip_month(rules):
    adjustments = {"2026-07": 245.0}
    july = estimate_payslip("2026-07", {}, rules, adjustments=adjustments)
    august = estimate_payslip("2026-08", {}, rules, adjustments=adjustments)
    assert july.adjustment == 245.0
    assert july.credited_net == 2445.0
    assert august.adjustment == 0.0
    assert august.credited_net == 2200.0


def test_old_rules_are_migrated_without_losing_values():
    migrated = migrate_rules({"paga_oraria": "12,60", "m_p_feriale_pct": 22})
    assert migrated["paga_oraria_lorda"] == 18.01988
    assert migrated["m_p_feriale_pct"] == 22
    assert migrated["netto_fisso_mensile"] == 2200


def test_calibration_with_ordinary_months():
    variables = {f"2026-0{month}": VariableBreakdown(premiums_gross=gross) for month, gross in zip(range(1, 7), (100, 200, 300, 400, 500, 600))}
    salaries = {f"2026-0{month + 1}": 2100 + gross * 0.65 for month, gross in zip(range(1, 7), (100, 200, 300, 400, 500, 600))}
    result = calibrate(salaries, variables)
    assert result.fixed_net == pytest.approx(2100)
    assert result.variable_coefficient == pytest.approx(0.65)
    assert result.mean_absolute_error == pytest.approx(0)


def test_calibration_excludes_thirteenth_and_730_outliers():
    variables = {f"2026-0{month}": VariableBreakdown(premiums_gross=month * 100) for month in range(1, 7)}
    salaries = {f"2026-0{month + 1}": 2200 + month * 60 for month in range(1, 7)}
    salaries["2026-05"] = 5000  # premio/tredicesima
    salaries["2026-07"] = 900   # 730/trattenuta anomala
    result = calibrate(salaries, variables)
    excluded = {row.month for row in result.rows if not row.included}
    assert {"2026-05", "2026-07"} <= excluded


def test_registered_adjustment_keeps_month_usable_for_calibration():
    variables = {
        f"2026-0{month}": VariableBreakdown(premiums_gross=month * 100)
        for month in range(1, 7)
    }
    salaries = {
        f"2026-0{month + 1}": 2100 + month * 65
        for month in range(1, 7)
    }
    salaries["2026-07"] += 280
    result = calibrate(
        salaries,
        variables,
        adjustments={"2026-07": 280},
    )
    july = next(row for row in result.rows if row.month == "2026-07")
    assert july.included
    assert july.adjustment == 280
    assert result.fixed_net == pytest.approx(2100)
    assert result.variable_coefficient == pytest.approx(0.65)


def test_live_counter_converts_only_variables_with_calibrated_coefficient():
    value = estimate_live_net_accrual(
        ordinary_hours=8,
        ordinary_net_hourly=13.0,
        variable_gross=30.0,
        variable_coefficient=0.60,
    )
    assert value == pytest.approx(122.0)


def test_live_counter_clamps_invalid_inputs():
    value = estimate_live_net_accrual(
        ordinary_hours=-2,
        ordinary_net_hourly=13.0,
        variable_gross=10.0,
        variable_coefficient=2.0,
    )
    assert value == pytest.approx(10.0)
