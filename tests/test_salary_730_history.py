"""PDF 730 history is read-only; markers never add cash to salary statistics."""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import json
import re
import pytest
from payroll_engine import thirteenth_month_net
import pandas as pd
import altair as alt
import salary_extras


def helpers():
    tree = ast.parse((Path(__file__).parents[1] / 'spese_mensili.py').read_text())
    names = {'_thirteenth_card_amounts', '_payroll_thirteenth_history', '_payroll_730_history', '_saved_730_history', '_salary_730_markers', '_salary_thirteenth_markers', '_salary_marker_label_layers', '_salary_extra_markers', '_salary_extra_chart_layers',
             'render_grafico_stipendi_desktop_style', '_render_mobile_salary_legend', '_signed_money_turni', '_money_turni'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    plots, errors = [], []
    st = SimpleNamespace(session_state={}, markdown=lambda *a, **kw: None,
        info=lambda *a, **kw: None, error=errors.append,
        altair_chart=lambda chart, **kw: plots.append(chart.to_dict()))
    env = dict(st=st, pd=pd, alt=alt, re=re, MOBILE_VIEW=True,
               _salary_extras=salary_extras, _load_salary_extras=lambda: [],
               thirteenth_month_net=thirteenth_month_net,
               _now_italy=lambda: datetime(2026, 11, 1),
               get_turni_rules=lambda: (_ for _ in ()).throw(AssertionError('Unexpected external read')))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'salary-history', 'exec'), env)
    return env, st, plots, errors


def snapshot():
    # Synthetic amounts, and an intentionally different applied calibration map.
    return {'tax_adjustments': {'2025-07': -607.}, 'drive': {
        'tax_adjustments': {'2025-07': -607., '2026-09': 200.},
        'tax_details': {'2025-07': [{'amount': -640.}, {'amount': 33.}],
                        '2026-09': [{'amount': 200.}]}}}


def test_history_uses_paid_month_and_raw_730_even_when_manually_corrected():
    env, _, _, _ = helpers()
    rows = env['_payroll_730_history'](snapshot())
    assert rows == [dict(Mese='2025-07', Anno=2025, Rimborsi=33., Trattenute=640., **{'Saldo 730': -607.}),
                    dict(Mese='2026-09', Anno=2026, Rimborsi=200., Trattenute=0., **{'Saldo 730': 200.})]
    assert env['_payroll_730_history'](None) == []
    assert env['_payroll_730_history']({'drive': {'tax_adjustments': {'2026-09': 12.}}})[0]['Saldo 730'] == 12.


def test_chart_marks_only_matching_months_and_keeps_salary_values_and_means():
    env, st, plots, errors = helpers()
    st.session_state['payroll_calibration_saved'] = snapshot()
    data = pd.DataFrame({'Mese': pd.to_datetime(['2026-08-01', '2026-09-01']),
        'Stipendio': [2400., 2600.], 'Risparmi': [10., 20.], 'Messi da parte Totali': [100., 200.],
        'Media Stipendio': [2400., 2500.], 'Media Stipendio NO 13°/PDR': [2400., 2500.],
        'Media Risparmi': [10., 15.], 'Media Messi da parte Totali': [100., 150.]})
    before = data.copy(deep=True)
    env['render_grafico_stipendi_desktop_style'](data, years_back=3)
    assert not errors
    pd.testing.assert_frame_equal(data, before)
    encoded = json.dumps(plots[0])
    assert 'Saldo 730' in encoded and 'diamond' in encoded and '730: +' in encoded
    fiscal_rows = [row for values in plots[0]['datasets'].values() for row in values if 'Saldo 730' in row]
    assert len(fiscal_rows) == 1
    assert fiscal_rows[0]['Stipendio'] == 2600. and fiscal_rows[0]['Saldo 730'] == 200.


def test_mobile_trend_displays_exactly_sixteen_months_without_recomputing_statistics():
    env, st, plots, errors = helpers()
    st.session_state['payroll_calibration_saved'] = {}
    dates = pd.date_range('2025-06-01', '2026-11-01', freq='MS')
    data = pd.DataFrame({'Mese': dates, 'Stipendio':range(2100,2100+len(dates)),
        'Risparmi':10., 'Messi da parte Totali':100., 'Media Stipendio':2200.,
        'Media Stipendio NO 13°/PDR':2150., 'Media Risparmi':10., 'Media Messi da parte Totali':100.})
    before = data.copy(deep=True)
    env['render_grafico_stipendi_desktop_style'](data, months_back=16)
    assert not errors
    months = {row['Mese'][:7] for rows in plots[0]['datasets'].values() for row in rows if 'Mese' in row}
    assert len(months) == 16 and min(months) == '2025-08' and max(months) == '2026-11'
    pd.testing.assert_frame_equal(data,before)


def test_fifteen_months_end_at_latest_saved_salary_and_keep_august_labels():
    env, st, plots, errors = helpers()
    st.session_state['payroll_calibration_saved'] = {}
    dates = pd.date_range('2025-05-01', '2026-10-01', freq='MS')
    data = pd.DataFrame({'Mese':dates, 'Stipendio':2400., 'Risparmi':10.,
        'Messi da parte Totali':100., 'Media Stipendio':2400.,
        'Media Stipendio NO 13°/PDR':2400., 'Media Risparmi':10., 'Media Messi da parte Totali':100.})
    env['render_grafico_stipendi_desktop_style'](data, months_back=15)
    assert not errors
    months = {row['Mese'][:7] for rows in plots[0]['datasets'].values() for row in rows if 'Mese' in row}
    assert len(months) == 15
    assert min(months) == '2025-08' and max(months) == '2026-10'
    assert '2026-08' in months
    assert '"labelOverlap": false' in json.dumps(plots[0])


def test_first_month_keeps_salary_when_saved_savings_are_zero():
    env, st, plots, errors = helpers()
    st.session_state['payroll_calibration_saved'] = {}
    dates = pd.date_range('2025-08-01', '2026-10-01', freq='MS')
    data = pd.DataFrame({'Mese': dates, 'Stipendio': 2300., 'Risparmi': 0.,
        'Messi da parte Totali': 0., 'Media Stipendio': 2300.,
        'Media Stipendio NO 13°/PDR': 2300., 'Media Risparmi': 0., 'Media Messi da parte Totali': 0.})
    data.loc[0, 'Stipendio'] = 2371.
    data.loc[1:, 'Messi da parte Totali'] = 150.
    legends = []
    st.markdown = lambda content, **kw: legends.append(content)
    env['render_grafico_stipendi_desktop_style'](data, months_back=15, show_legend=False)
    assert not errors and not legends
    first = [row for rows in plots[0]['datasets'].values() for row in rows
             if row.get('Mese', '').startswith('2025-08') and 'Stipendio' in row]
    assert first and all(row['Stipendio'] == 2371. for row in first)
    assert all(row['Risparmi'] == 0. and row['Messi da parte Totali'] == 0. for row in first)
    env['_render_mobile_salary_legend']()
    assert len(legends) == 1 and 'mobile-salary-legend' in legends[0]


def test_thirteenth_history_uses_explicit_gross_rows_and_not_calibrated_estimates():
    env, _, _, _ = helpers()
    saved = {'result': {'thirteenth_net': 1500., 'rows': [
        {'month': '2025-12', 'thirteenth_net': 1500.}]}, 'drive': {'details': {
        '2024-12': [{'description': 'Tredicesima mensilità', 'amount': 2100.},
                    {'description': 'Retribuzione ordinaria', 'amount': 2500.}],
        '2025-12': [{'description': '13ma mensilità', 'amount': 2300.},
                    {'description': 'Conguaglio tredicesima', 'amount': -50.},
                    {'description': 'Ratei tredicesima', 'amount': 200.}],
        '2026-10': [{'description': 'Rateo tredicesima', 'amount': 300.}]}}}
    rows = env['_payroll_thirteenth_history'](saved)
    assert rows == {2024: {'gross': 2100., 'months': {'2024-12'}},
                    2025: {'gross': 2250., 'months': {'2025-12'}}}
    assert env['_payroll_thirteenth_history'](None) == {}
    assert env['_payroll_thirteenth_history']({'result': saved['result']}) == {}


def test_historical_and_forecast_net_use_annual_calibration_not_variable_coefficient():
    env, _, _, _ = helpers()
    snapshot = {'drive': {'details': {
        '2024-12': [{'description': 'Tredicesima', 'amount': 2000.}],
        '2025-12': [{'description': 'Tredicesima', 'amount': 2500.}]}},
        'result': {'rows': [{'month': '2025-12', 'included': True, 'thirteenth_net': 1750.}]}}
    rules = dict(tredicesima_automatica=1, netto_fisso_mensile=2300.,
                 tredicesima_coeff_netto=.8, coefficiente_netto_variabili=.1)
    before = json.dumps(snapshot)
    amounts = env['_thirteenth_card_amounts'](2024, snapshot, rules)
    assert amounts == {'gross': 2000., 'net': 1400., 'actual': True}
    forecast = env['_thirteenth_card_amounts'](2026, snapshot, rules)
    assert forecast['net'] == thirteenth_month_net('2026-12', rules) == 1840.
    assert forecast['gross'] == pytest.approx(1840. / .7)
    assert not forecast['actual']
    snapshot['drive']['details']['2026-12'] = [{'description': 'Tredicesima', 'amount': 2600.}]
    paid = env['_thirteenth_card_amounts'](2026, snapshot, rules)
    assert paid == {'gross': 2600., 'net': pytest.approx(1820.), 'actual': True}
    snapshot['drive']['details'].pop('2026-12')
    assert json.dumps(snapshot) == before


def test_thirteenth_display_does_not_invent_gross_without_pdf_reference():
    env, _, _, _ = helpers()
    rules = dict(tredicesima_automatica=1, netto_fisso_mensile=2000., tredicesima_coeff_netto=.8)
    assert env['_thirteenth_card_amounts'](2026, {}, rules) == {'gross': None, 'net': 1600., 'actual': False}
    assert env['_thirteenth_card_amounts'](2025, {}, rules) == {'gross': None, 'net': None, 'actual': False}
    saved = {'drive': {'details': {'2025-12': [{'description': 'Tredicesima', 'amount': 2500.}]}}}
    assert env['_thirteenth_card_amounts'](2025, saved, rules)['net'] == 1600.


def test_chart_labels_730_and_thirteenth_with_colons_and_smaller_separate_signs():
    env, st, plots, errors = helpers()
    st.session_state['payroll_calibration_saved'] = {
        'drive': {'details': {'2025-12': [{'description': 'Tredicesima', 'amount': 2500.}],
                             '2024-12': [{'description': 'Tredicesima', 'amount': 2100.}]},
                  'tax_adjustments': {'2025-12': -100.}},
        'result': {'rows': [{'month': '2025-12', 'included': True, 'thirteenth_net': 1700.}]}}
    data = pd.DataFrame({'Mese': pd.to_datetime(['2025-12-01','2026-01-01']),
        'Stipendio': [4100.,2500.], 'Risparmi': 0., 'Messi da parte Totali': 100.,
        'Media Stipendio': 3000., 'Media Stipendio NO 13°/PDR': 2400.,
        'Media Risparmi': 0., 'Media Messi da parte Totali': 100.})
    before = data.copy(deep=True)
    env['render_grafico_stipendi_desktop_style'](data, months_back=15)
    assert not errors
    pd.testing.assert_frame_equal(data, before)
    rows = [row for values in plots[0]['datasets'].values() for row in values]
    annual = [row for row in rows if 'Tredicesima lorda' in row]
    tax = [row for row in rows if 'Saldo 730' in row]
    assert len(annual) == 1 and annual[0]['Prefisso'] == '13ª:'
    assert annual[0]['Importo tredicesima'] == pytest.approx(1700.) and annual[0]['Tipo importo'] == 'Netto stimato'
    assert annual[0]['Tredicesima lorda'] == 2500. and annual[0]['Segno'] == '+'
    assert len(tax) == 1 and tax[0]['Prefisso'] == '730:' and tax[0]['Segno'] == '−'
    def marks(value):
        if isinstance(value, dict):
            if isinstance(value.get('mark'), dict):yield value
            for child in value.values():yield from marks(child)
        elif isinstance(value,list):
            for child in value:yield from marks(child)
    text = [mark for mark in marks(plots[0]) if mark['mark']['type']=='text']
    assert [mark['mark']['fontSize'] for mark in text if mark['encoding']['text']['field']=='Segno'] == [7,7]
    assert all(mark['mark']['fontSize']==10 for mark in text if mark['encoding']['text']['field']!='Segno')


def test_production_and_welfare_markers_leave_salary_and_average_values_unchanged():
    env, st, plots, errors = helpers()
    st.session_state['payroll_calibration_saved'] = {}
    env['_load_salary_extras'] = salary_extras.initial_rows
    data = pd.DataFrame({'Mese': pd.to_datetime(['2026-02-01','2026-07-01']),
        'Stipendio': [2500.,2800.], 'Risparmi': 0., 'Messi da parte Totali': 100.,
        'Media Stipendio': 2600., 'Media Stipendio NO 13°/PDR': 2400.,
        'Media Risparmi': 0., 'Media Messi da parte Totali': 100.})
    before = data.copy(deep=True)
    env['render_grafico_stipendi_desktop_style'](data, months_back=15)
    assert not errors
    datasets = [row for rows in plots[0]['datasets'].values() for row in rows]
    extra_rows = [row for row in datasets if row.get('Prefisso') in {'Premio produzione:', 'Welfare:'}]
    assert {row['Importo'] for row in extra_rows} == {250., 3500.}
    assert {row['Stipendio'] for row in extra_rows} == {2500., 2800.}
    pd.testing.assert_frame_equal(before, data)
