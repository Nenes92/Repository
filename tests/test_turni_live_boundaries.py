"""An overnight shift remains live across midnight, month/year changes and rest."""
import ast
import calendar
import html
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from payroll_engine import estimate_live_net_accrual, estimate_payslip


def app_at(now):
    source = Path(__file__).parents[1] / "spese_mensili.py"
    nodes = []
    for node in ast.parse(source.read_text()).body:
        if isinstance(node, ast.Assign):
            try:
                ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef):
            node.decorator_list = []
            nodes.append(node)
    env = dict(pd=pd, datetime=datetime, timedelta=timedelta, calendar=calendar,
               html=html, json=json, MOBILE_VIEW=True,
               estimate_live_net_accrual=estimate_live_net_accrual,
               st=SimpleNamespace(session_state={}))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), env)
    env["_now_italy"] = lambda: now
    # Only replace the persisted payroll forecast; live calculations are real.
    env["_payroll_estimate_for_month"] = lambda df, rules, month: estimate_payslip(month, {}, rules)
    return env


def calendar_with_night(env, start_day="2026-10-31", rest_kind="Riposo"):
    start = pd.Timestamp(start_day)
    following = (start + pd.Timedelta(days=1)).normalize()
    rows = [{"Data": start.strftime("%Y-%m-%d"), "Turno": "Notte"},
            {"Data": following.strftime("%Y-%m-%d"), "Turno": rest_kind}]
    # Different planned hours in the two months expose accidental hourly-base
    # changes at midnight. No work overlaps the overnight/rest interval.
    for day in range(1, 16):
        rows.append({"Data": start.replace(day=day).strftime("%Y-%m-%d"), "Turno": "Mattina"})
    for day in range(2, 22):
        rows.append({"Data": following.replace(day=day).strftime("%Y-%m-%d"), "Turno": "Mattina"})
    frame = env["_normalize_turni_df"](pd.DataFrame(rows))
    return frame, dict(env["DEFAULT_TURNI_RULES"], notte_feriale_pct=50.)


@pytest.mark.parametrize("start_day", ["2026-10-31", "2026-12-31", "2027-02-28"])
@pytest.mark.parametrize("minutes", [0, 2, 359])
def test_month_and_year_boundary_keep_whole_shift_in_start_month(start_day, minutes):
    start = datetime.fromisoformat(start_day + "T22:00")
    midnight = (start + timedelta(days=1)).replace(hour=0)
    now = midnight + timedelta(minutes=minutes)
    env = app_at(now)
    frame, rules = calendar_with_night(env, start_day)
    before = frame.copy(deep=True)
    result = env["compute_turni_dashboard"](frame, rules)
    hourly = env["_live_net_hourly_base"](frame, rules, start_day[:7])
    assert hourly != env["_live_net_hourly_base"](frame, rules, now.strftime("%Y-%m"))
    expected = env["compute_turno_net_estimate"](start_day, "Notte", False, rules, hourly, until=now)
    full = env["compute_turno_net_estimate"](start_day, "Notte", False, rules, hourly, until=datetime.max)
    assert result["is_on_shift"] and not result["is_on_leave"]
    assert result["current_turno"] == "Notte"
    assert result["current_shift_start_date"] == start_day
    assert result["current_shift_end"] == (midnight + timedelta(hours=6)).isoformat()
    assert result["rate_min"] == pytest.approx(expected["rate_min"])
    assert result["rate_min"] > 0
    assert result["live_today"] == pytest.approx(expected["total"])
    assert result["expected_today"] == pytest.approx(full["total"])
    assert result["live_month"] == 0
    assert result["hours_live"] == 0
    assert not result["live_month_accrues"]
    assert "01/" in result["current_shift"] and "06:00" in result["current_shift"]
    pd.testing.assert_frame_equal(frame, before, check_dtype=False)


@pytest.mark.parametrize("now", [datetime(2026, 11, 1, 6), datetime(2026, 11, 1, 12), datetime(2026, 11, 2, 1)])
def test_rest_starts_at_six_without_crediting_previous_month_shift_again(now):
    env = app_at(now)
    frame, rules = calendar_with_night(env)
    result = env["compute_turni_dashboard"](frame, rules)
    assert not result["is_on_shift"]
    assert result["current_shift_end"] == ""
    assert result["rate_min"] == 0
    assert result["turno_kpi_label"] == "Ultimo / prossimo turno"
    assert result["live_month"] == 0
    assert result["hours_live"] == 0
    assert result["next_shift_start"] == "2026-11-02T06:00:00"


@pytest.mark.parametrize("rest_kind", ["", "Riposo", "Ferie"])
@pytest.mark.parametrize("reverse", [False, True])
def test_previous_night_has_priority_over_today_rest_or_leave_regardless_of_row_order(rest_kind, reverse):
    env = app_at(datetime(2026, 11, 1, 2))
    frame, rules = calendar_with_night(env, rest_kind=rest_kind)
    if reverse:
        frame = frame.iloc[::-1]
    result = env["compute_turni_dashboard"](frame, rules)
    assert result["is_on_shift"] and not result["is_on_leave"]
    assert result["current_turno"] == "Notte"
    assert result["turno_kpi_label"] == "Turno — netto live / totale netto"


def test_same_month_night_before_rest_remains_live():
    env = app_at(datetime(2026, 10, 31, 2))
    frame = env["_normalize_turni_df"](pd.DataFrame([
        {"Data": "2026-10-30", "Turno": "Notte"}, {"Data": "2026-10-31", "Turno": "Riposo"}]))
    result = env["compute_turni_dashboard"](frame, env["DEFAULT_TURNI_RULES"])
    assert result["is_on_shift"]
    assert result["live_today"] > 0 and result["rate_min"] > 0
    assert result["live_month_accrues"]


def test_shift_counter_does_not_restart_at_midnight_and_holiday_rate_changes():
    before_env = app_at(datetime(2026, 10, 31, 23, 59))
    frame, rules = calendar_with_night(before_env)
    before = before_env["compute_turni_dashboard"](frame.copy(), rules)
    after_env = app_at(datetime(2026, 11, 1, 0, 0))
    after = after_env["compute_turni_dashboard"](frame.copy(), rules)
    assert before["is_on_shift"] and after["is_on_shift"]
    assert after["live_today"] - before["live_today"] == pytest.approx(before["rate_min"])
    assert after["expected_today"] == pytest.approx(before["expected_today"])
    assert after["rate_min"] > before["rate_min"]  # Sunday/Nov 1 starts at midnight.
    assert after["live_month"] == 0
    assert before["live_month_accrues"] and not after["live_month_accrues"]


def test_summary_includes_only_the_active_previous_month_night_until_six():
    env = app_at(datetime(2026, 11, 1, 2))
    frame, rules = calendar_with_night(env)
    summary = env["_turni_month_summary_html"](frame, "2026-11", rules, "2026-10-31")
    assert 'id="turni-focus-card"' in summary
    assert "31 ottobre 2026" in summary
    assert "01 ottobre 2026" not in summary
    env["_now_italy"] = lambda: datetime(2026, 11, 1, 6)
    assert "31 ottobre 2026" not in env["_turni_month_summary_html"](frame, "2026-11", rules, "2026-10-31")


def test_month_report_attributes_all_eight_hours_and_premiums_to_shift_start_month():
    env = app_at(datetime(2026, 11, 1, 6))
    frame = env["_normalize_turni_df"](pd.DataFrame([
        {"Data": "2026-10-31", "Turno": "Notte"},
        {"Data": "2026-11-01", "Turno": "Riposo"}]))
    rules = dict(env["DEFAULT_TURNI_RULES"], notte_feriale_pct=50.)
    october = env["_turni_month_money_summary"](frame, rules, "2026-10")
    november = env["_turni_month_money_summary"](frame, rules, "2026-11")
    hourly = env["_live_net_hourly_base"](frame, rules, "2026-10")
    whole_shift = env["compute_turno_net_estimate"](
        "2026-10-31", "Notte", False, rules, hourly, until=datetime.max)
    assert october["hours_by_pct"] == pytest.approx({50.: 2., 60.: 6.})
    assert october["month_total"] == pytest.approx(whole_shift["total"])
    assert november["hours_by_pct"] == {}
    assert november["month_total"] == 0


@pytest.mark.parametrize('start_day', ['2026-10-31', '2026-12-31'])
@pytest.mark.parametrize('overtime', [0, 60, 120])
def test_mobile_live_month_follows_start_month_until_actual_end(start_day, overtime):
    start = datetime.fromisoformat(start_day + 'T22:00')
    end = (start + timedelta(days=1)).replace(hour=6) + timedelta(minutes=overtime)
    env = app_at(end - timedelta(minutes=1))
    frame, rules = calendar_with_night(env, start_day)
    frame.loc[frame['Data'] == start_day, 'Straordinario minuti'] = overtime
    month = env['_turni_live_month'](frame)
    assert month == start_day[:7]
    live = env['compute_turni_dashboard'](frame, rules, month)
    assert live['live_month_key'] == start_day[:7]
    assert live['live_month_accrues'] and live['live_month'] > 0
    assert live['is_on_shift'] and live['current_shift_end'] == end.isoformat()
    assert live['rate_min'] > 0
    if overtime:
        expected_rate = rules['paga_oraria'] * (1 + env['_pct_for_straordinario']('Notte', end-timedelta(minutes=1), False, rules)/100) * rules['coefficiente_netto_variabili']/60
        assert live['rate_min'] == pytest.approx(expected_rate)
    env['_now_italy'] = lambda: end
    month = env['_turni_live_month'](frame)
    handoff = end.replace(hour=8, minute=0)
    assert month == (start_day[:7] if end < handoff else end.strftime('%Y-%m'))
    ended = env['compute_turni_dashboard'](frame, rules, month)
    assert not ended['is_on_shift']
    if end < handoff:
        assert ended['live_month'] > 0 and ended['rate_min'] == 0
        assert ended['month_handoff_at'] == handoff.isoformat()
    env['_now_italy'] = lambda: handoff
    assert env['_turni_live_month'](frame) == handoff.strftime('%Y-%m')
    assert env['compute_turni_dashboard'](frame, rules, handoff.strftime('%Y-%m'))['live_month'] == 0


def test_live_month_target_uses_same_month_shifts_instead_of_delayed_payslip():
    from payroll_engine import VariableBreakdown
    env = app_at(datetime(2026, 11, 1, 2, 10))
    frame, rules = calendar_with_night(env)
    rules = dict(rules, coefficiente_netto_variabili=.6)
    payslip = estimate_payslip('2026-10', {'2026-09': VariableBreakdown(premiums_gross=10.)}, rules)
    env['_payroll_estimate_for_month'] = lambda *args: payslip
    result = env['compute_turni_dashboard'](frame, rules, '2026-10')
    # The old comparison crossed two competence months. It is valid for the
    # accrued October work to exceed an October payslip with September extras.
    assert result['live_month'] > result['payslip_estimate']
    assert result['payslip_estimate'] == payslip.credited_net
    assert result['expected_month'] > result['live_month']
    hourly = env['_live_net_hourly_base'](frame, rules, '2026-10')
    full = sum(env['compute_turno_net_estimate'](
        row['Data'], row['Turno'], bool(row['Festivo']), rules, hourly, until=datetime.max)['total']
        for _, row in frame[frame['Data'].str.startswith('2026-10')].iterrows())
    assert result['expected_month'] == pytest.approx(full)
    env['_now_italy'] = lambda: datetime(2026, 11, 1, 6)
    ended = env['compute_turni_dashboard'](frame, rules, '2026-10')
    assert ended['live_month'] == pytest.approx(ended['expected_month'])
    assert ended['expected_month'] == pytest.approx(result['expected_month'])
    assert ended['payslip_estimate'] == payslip.credited_net
