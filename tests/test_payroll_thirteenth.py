"""December is an annual net component, never ordinary pay or shift earnings."""
import ast
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from payroll_engine import (
    DEFAULT_RULES, VariableBreakdown, add_months, calibrate, estimate_payslip,
    thirteenth_month_net, thirteenth_rules_from_snapshot,
)


def history(extra=1700.):
    variables, salaries, adjustments, holidays = {}, {}, {}, {}
    for i in range(12):
        month = add_months("2025-11", i)
        gross = 100. + (i % 4) * 80.
        variables[add_months(month, -1)] = VariableBreakdown(premiums_gross=gross, allowances_net=30.)
        adjustments[month] = -63.
        holidays[month] = 119.90 if month.endswith("-11") else 0.
        salaries[month] = 2100. + (gross + holidays[month]) * .68 + 30. - 63.
        if month.endswith("-12"):
            salaries[month] += extra
    return salaries, variables, adjustments, holidays


def fit(extra=1700., **kwargs):
    salaries, variables, adjustments, holidays = history(extra)
    return calibrate(salaries, variables, adjustments=adjustments,
                     automatic_gross=holidays, include_thirteenth=True, **kwargs)


def test_december_is_included_without_inflating_fixed_or_variable_coefficient():
    result = fit()
    assert result.fixed_net == pytest.approx(2100.)
    assert result.variable_coefficient == pytest.approx(.68)
    assert result.thirteenth_net == pytest.approx(1700.)
    assert result.thirteenth_samples == 1
    assert result.mean_absolute_error == pytest.approx(0., abs=1e-9)
    assert all(row.included for row in result.rows)
    december = next(row for row in result.rows if row.month.endswith("-12"))
    assert december.thirteenth_net == pytest.approx(1700.)
    assert december.estimated_net == pytest.approx(december.actual_net)
    assert all(row.thirteenth_net == 0 for row in result.rows if not row.month.endswith("-12"))


def test_forecast_adds_annual_extra_once_in_december_with_prior_month_variables():
    result = fit()
    config = thirteenth_rules_from_snapshot(
        dict(DEFAULT_RULES, netto_fisso_mensile=result.fixed_net,
             coefficiente_netto_variabili=result.variable_coefficient, tredicesima_automatica=1),
        asdict(result))
    variables = {"2026-11": VariableBreakdown(premiums_gross=400., allowances_net=25.)}
    december = estimate_payslip("2026-12", variables, config, uncertainty=50., adjustments={"2026-12": -63.})
    assert december.competence_month == "2026-11"
    assert december.variables_net == pytest.approx(400. * .68 + 25.)
    assert december.credited_net == pytest.approx(2100. + 400. * .68 + 25. - 63. + 1700.)
    assert december.realistic_low == pytest.approx(december.credited_net - 50.)
    assert december.realistic_high == pytest.approx(december.credited_net + 50.)
    assert december.thirteenth_samples == 1
    assert thirteenth_month_net("2027-12", config) == pytest.approx(1700.)
    for month in ("2026-07", "2026-11", "2027-01"):
        assert estimate_payslip(month, variables, config).thirteenth_net == 0


def test_without_december_history_the_extra_is_explicitly_provisional():
    salaries, variables, adjustments, holidays = history()
    salaries.pop("2025-12")
    result = calibrate(salaries, variables, adjustments=adjustments,
                       automatic_gross=holidays, include_thirteenth=True)
    assert result.thirteenth_samples == 0
    assert result.thirteenth_net == pytest.approx(result.fixed_net)
    assert result.fixed_net == pytest.approx(2100.)


def test_manual_december_exclusion_and_unrelated_bonus_remain_respected():
    salaries, variables, adjustments, holidays = history()
    salaries["2026-07"] += 1500.
    result = calibrate(salaries, variables, adjustments=adjustments, automatic_gross=holidays,
                       include_thirteenth=True, manual_included={"2025-12": False})
    rows = {r.month: r for r in result.rows}
    assert not rows["2025-12"].included
    assert not rows["2026-07"].included
    assert result.thirteenth_samples == 0
    assert result.fixed_net == pytest.approx(2100.)


def test_zero_annual_component_and_legacy_desktop_behavior():
    result = fit(extra=0.)
    assert result.thirteenth_samples == 1
    assert result.thirteenth_net == pytest.approx(0., abs=1e-9)
    config = thirteenth_rules_from_snapshot(dict(DEFAULT_RULES, tredicesima_automatica=1), asdict(result))
    assert estimate_payslip("2026-12", {}, config).thirteenth_net == pytest.approx(0., abs=1e-9)
    salaries, variables, adjustments, holidays = history()
    legacy = calibrate(salaries, variables, adjustments=adjustments, automatic_gross=holidays)
    assert not next(r for r in legacy.rows if r.month.endswith("-12")).included
    assert estimate_payslip("2026-12", {}, DEFAULT_RULES).thirteenth_net == 0


def test_legacy_snapshot_can_supply_an_estimate_without_reading_drive():
    snapshot = {"fixed_net": 2000., "rows": [
        {"month": "2024-12", "actual_net": 4000., "estimated_net": 2500.},
        {"month": "2025-12", "actual_net": 4300., "estimated_net": 2600.},
    ]}
    rules = dict(DEFAULT_RULES, netto_fisso_mensile=2100., tredicesima_automatica=1)
    updated = thirteenth_rules_from_snapshot(rules, snapshot)
    assert updated["tredicesima_coeff_netto"] == .85
    assert thirteenth_month_net("2026-12", updated) == pytest.approx(1785.)
    assert "tredicesima_coeff_netto" not in rules


def test_need_ordinary_comparable_months_to_separate_annual_component():
    with pytest.raises(ValueError):
        calibrate({"2025-12": 4200., "2026-12": 4400.},
                  {"2025-11": VariableBreakdown(), "2026-11": VariableBreakdown()},
                  include_thirteenth=True)


def test_salary_averages_include_december_ordinary_portion_only_in_mobile():
    source = Path(__file__).parents[1] / "spese_mensili.py"
    node = next(n for n in ast.parse(source.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == "calcola_medie")
    # The first three startup rows remain excluded by the existing policy.
    data = pd.DataFrame({"Mese": pd.date_range("2025-08-01", periods=6, freq="MS"),
                         "Stipendio": [100., 100., 100., 2200., 4000., 2400.]})
    state = {"payroll_calibration_saved": {"result": {"rows": [{"month": "2025-12", "thirteenth_net": 1500.}]}}}
    ns = dict(pd=pd, MOBILE_VIEW=True, st=SimpleNamespace(session_state=state),
              _payroll_v2_rules=lambda rules: rules, get_turni_rules=lambda: {},
              thirteenth_month_net=lambda *a: 999.)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), ns)
    mobile = ns["calcola_medie"](data, ["Stipendio"])
    assert mobile.iloc[-1]["Media Stipendio"] == pytest.approx(2866.67)
    assert mobile.iloc[-1]["Media Stipendio NO 13°/PDR"] == pytest.approx(2366.67)
    ns["MOBILE_VIEW"] = False
    desktop = ns["calcola_medie"](data, ["Stipendio"])
    assert desktop.iloc[-1]["Media Stipendio NO 13°/PDR"] == 2300.
