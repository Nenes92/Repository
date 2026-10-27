from datetime import date
import pytest

from payroll_engine import DEFAULT_RULES, VariableBreakdown, calibrate, estimate_payslip
from payroll_holidays import paid_holidays, holiday_compensation, holiday_net


def rules(**kwargs):
    return {**DEFAULT_RULES, "festivita_automatiche": 1,
            "festivita_quota_lorda": 119.90, **kwargs}


def test_2026_calendar_and_remaining_november():
    events = paid_holidays(2026)
    assert [(e.day, e.label) for e in events] == [
        (date(2026, 10, 4), "San Francesco"),
        (date(2026, 11, 1), "Ognissanti"),
        (date(2026, 11, 4), "4 novembre · trattamento CCNL"),
    ]
    assert sum(e.gross for e in events if e.day > date(2026, 10, 27)) == pytest.approx(239.80)
    assert sum(e.gross for e in events) == pytest.approx(359.70)


def test_no_san_francesco_before_2026_and_no_easter_payment():
    assert all(not (e.day.month == 10 and e.day.day == 4) for e in paid_holidays(2020))
    assert all(e.day != date(2026, 4, 5) for e in paid_holidays(2026))
    # Pasqua (March 28, 2027) and Pasquetta never create an automatic payment.
    assert all(e.day not in {date(2027, 3, 28), date(2027, 3, 29)} for e in paid_holidays(2027))


def test_4_november_is_always_separate_from_ognissanti():
    assert len(holiday_compensation("2026-11", rules())) == 2
    assert len([e for e in paid_holidays(2027) if e.day.month == 11]) == 1


def test_patron_only_on_sunday_and_no_duplicate_with_national_holiday():
    assert len(paid_holidays(2026, patron_month=6, patron_day=28)) == 4
    assert len(paid_holidays(2026, patron_month=6, patron_day=29)) == 3
    assert len(paid_holidays(2026, patron_month=10, patron_day=4)) == 3


def test_holiday_month_is_independent_of_previous_month_shift_variables():
    config = rules(ritardo_competenze_mesi=1)
    result = estimate_payslip("2026-11", {"2026-10": VariableBreakdown(premiums_gross=100)}, config)
    assert result.holiday_gross == pytest.approx(239.80)
    assert result.holiday_net == pytest.approx(239.80 * .6)
    assert result.variables_net == 60
    assert result.adjustment == -63
    assert result.credited_net == pytest.approx(2200 + 60 - 63 + 239.80 * .6)
    assert result.realistic_low == result.credited_net


def test_payment_delay_moves_annual_holiday_to_following_year():
    config = rules(festivita_ritardo_mesi=1)
    # December 26, 2027 is Sunday, paid in Jan 2028 with a one-month delay.
    assert holiday_compensation("2028-01", config)[0].day == date(2027, 12, 26)


def test_disabled_preserves_existing_payroll():
    result = estimate_payslip("2026-11", {}, DEFAULT_RULES)
    assert result.holiday_gross == 0
    assert result.credited_net == 2137
    assert holiday_net("2026-11", rules(festivita_automatiche=0)) == 0


def test_calibration_neutralizes_automatic_holiday_income_without_changing_adjustments():
    config = rules()
    variables = {
        "2026-08": VariableBreakdown(premiums_gross=100),
        "2026-09": VariableBreakdown(premiums_gross=200),
        "2026-10": VariableBreakdown(premiums_gross=300),
    }
    automatic = {m: sum(e.gross for e in holiday_compensation(m, config)) for m in ["2026-09", "2026-10", "2026-11"]}
    adjustments = {m: -63 for m in automatic}
    salaries = {m: 2000 + (gross + automatic[m]) * .6 - 63
                for m, gross in zip(automatic, [100, 200, 300])}
    result = calibrate(salaries, variables, adjustments=adjustments, automatic_gross=automatic)
    assert result.fixed_net == pytest.approx(2000)
    assert result.variable_coefficient == pytest.approx(.6)
    assert result.mean_absolute_error == pytest.approx(0)
    assert all(row.adjustment == -63 for row in result.rows)


def test_san_giustino_local_patron_is_recurrent():
    assert any(e.day == date(2023, 6, 11) for e in paid_holidays(2023, patron_month=6, patron_day=11))
    assert any(e.day == date(2028, 6, 11) for e in paid_holidays(2028, patron_month=6, patron_day=11))
    assert all(e.day != date(2026, 6, 11) for e in paid_holidays(2026, patron_month=6, patron_day=11))


def test_existing_saved_rules_enable_new_feature_with_san_giustino_defaults():
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    import pandas as pd
    from payroll_engine import migrate_rules

    source = ast.parse((Path(__file__).parents[1] / "spese_mensili.py").read_text())
    defaults_node = next(n for n in source.body if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == "DEFAULT_TURNI_RULES" for t in n.targets))
    defaults = ast.literal_eval(defaults_node.value)
    funcs = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in {"get_turni_rules", "_parse_float_turni"}]

    class State(dict):
        def __getattr__(self, key):
            return self[key]
        def __setattr__(self, key, value):
            self[key] = value

    saved = pd.DataFrame([{"netto_fisso_mensile": 2300}])
    ns = dict(DEFAULT_TURNI_RULES=defaults, PAYROLL_V2_DEFAULTS=DEFAULT_RULES,
              TURNI_RULES_WORKSHEET="Rules", TURNI_RULES_HEADERS=list(defaults), pd=pd,
              CALIBRATION_STATE_HEADER="Calibrazione salvata", _decode_calibration_snapshot=lambda v: None,
              st=SimpleNamespace(session_state=State()), migrate_payroll_rules=migrate_rules,
              load_data_gsheets=lambda *a, **kw: saved)
    exec(compile(ast.Module(body=funcs, type_ignores=[]), "app-rule-migration", "exec"), ns)
    result = ns["get_turni_rules"]()
    assert result["festivita_automatiche"] == 1
    assert result["festivita_quota_lorda"] == 119.90
    assert result["patrono_mese"] == 6 and result["patrono_giorno"] == 11
    assert result["netto_fisso_mensile"] == 2300


def test_san_francesco_shift_calendar_changes_only_from_2026_in_mobile():
    from datetime import datetime
    from payroll_engine import is_holiday
    assert is_holiday(datetime(2027, 10, 4), san_francesco=True)
    assert not is_holiday(datetime(2021, 10, 4), san_francesco=True)
    assert not is_holiday(datetime(2027, 11, 4), san_francesco=True)
